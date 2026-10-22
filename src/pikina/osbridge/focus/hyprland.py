"""
Hyprland focus listener.

Reads the event socket $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock
(/tmp/hypr/... for Hyprland < 0.40) and seeds the current window with a one-shot
`j/activewindow` request on .socket.sock.

Hyprland never unlinks .socket2.sock on exit (only .socket.sock and hyprland.lock go),
so a leftover socket file proves nothing: an instance dir counts only while its
hyprland.lock names a live pid, and the loop tries every candidate instance until one
accepts a connection.

Hyprland drops event clients that fall 64 events behind, so the reader only parses
`activewindow>>CLASS,TITLE` lines and calls on_focus; nothing slow happens here.
Socket events carry no pid (the seed does), so pid is None for them. Hyprland cuts
event data at 1024 bytes, so a line that long can't be trusted for the private check:
the window is re-read with `j/activewindow` (full title), failing closed if that fails.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from pikina.osbridge.focus import (FocusSource, _exists, _hyprland_instances, _hyprland_lock_alive,
                                   is_firefox_family)

LEGACY_BASE = Path("/tmp/hypr")  # Hyprland < 0.40
_SEED_TIMEOUT = 1.0
_MAX_REPLY = 1 << 20
_MAX_LINE = 1 << 20
# Hyprland 0.56 sends `event>>data.substr(0, 1024)`; some older releases capped the whole
# line a little shorter (~1022). Any activewindow line this long may have lost its tail
# (where "Private Browsing" lives).
_EVENT_DATA_CAP = 1024
_TRUNCATED_LINE = 1022

_NO_INSTANCE = "no running Hyprland instance found (retrying)"
_UNREACHABLE = "can't reach Hyprland's event socket (retrying)"
_LOST = "lost Hyprland's event socket (reconnecting)"


def _safe_signature(his: str) -> bool:
    return bool(his) and "/" not in his and "\\" not in his and his not in (".", "..")


def _signature_dirs(env) -> list[Path]:
    """The HYPRLAND_INSTANCE_SIGNATURE dir(s) that belong to a live instance.

    Under $XDG_RUNTIME_DIR/hypr the dir counts only when its hyprland.lock pid is alive
    (a leftover .socket2.sock outlives Hyprland). Under the legacy /tmp/hypr base a
    missing lock is allowed (old releases didn't write one), a dead one is not.
    """
    his = env.get("HYPRLAND_INSTANCE_SIGNATURE") or ""
    if not _safe_signature(his):
        return []
    found = []
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        d = Path(runtime, "hypr", his)
        if _hyprland_lock_alive(d):
            found.append(d)
    d = LEGACY_BASE / his
    if _exists(d / ".socket2.sock") and (not _exists(d / "hyprland.lock") or _hyprland_lock_alive(d)):
        found.append(d)
    return found


def candidate_dirs(env) -> list[Path]:
    """Instance dirs to try, best first: the live HYPRLAND_INSTANCE_SIGNATURE dir, then every
    live $XDG_RUNTIME_DIR/hypr/*/hyprland.lock instance (newest first; covers a restarted
    Hyprland whose new signature our environment doesn't know)."""
    dirs = []
    for d in _signature_dirs(env) + _hyprland_instances(env):
        if d not in dirs:
            dirs.append(d)
    return dirs


def socket_dir(env) -> Path | None:
    """The first live instance dir (see candidate_dirs) that has a .socket2.sock, or None."""
    for d in candidate_dirs(env):
        if _exists(d / ".socket2.sock"):
            return d
    return None


def parse_event(line: bytes):
    """(app, title) for an `activewindow>>CLASS,TITLE` line, else None. Empty class -> app None."""
    name, sep, data = line.partition(b">>")
    if not sep or name != b"activewindow":
        return None
    cls, _, title = data.partition(b",")
    app = cls.decode("utf-8", errors="replace").strip() or None
    return app, title.decode("utf-8", errors="replace")


def maybe_truncated(line: bytes) -> bool:
    """True when Hyprland may have cut this event line short (see _EVENT_DATA_CAP)."""
    _, _, data = line.partition(b">>")
    return len(data) >= _EVENT_DATA_CAP or len(line) >= _TRUNCATED_LINE


class HyprlandSource(FocusSource):
    backend = "hyprland"

    def __init__(self, on_focus, **options):
        super().__init__(on_focus, **options)
        self._sock_lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._req_sock: socket.socket | None = None  # a j/activewindow request in flight

    # -- stop ------------------------------------------------------------------
    def _interrupt(self) -> None:
        with self._sock_lock:
            socks = [s for s in (self._sock, self._req_sock) if s is not None]
        for sock in socks:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- main loop -------------------------------------------------------------
    def _loop(self) -> None:
        backoff = self._backoff()
        while not self.stopping:
            dirs = candidate_dirs(self._env)
            directory = sock = None
            for d in dirs:
                if self.stopping:
                    break
                sock = self._connect(d / ".socket2.sock")
                if sock is not None:
                    directory = d
                    break
            if sock is not None:
                self.failure = None
                backoff.reset()
                request_path = directory / ".socket.sock"
                try:
                    self._seed(request_path)
                    self._read_events(sock, request_path)
                except Exception:
                    pass  # treat any surprise as a lost connection; reconnect below
                finally:
                    self._close(sock)
                self._lost(_LOST)
            elif not self.stopping:
                self._lost(_UNREACHABLE if dirs else _NO_INSTANCE)
            if self.stopping:
                break
            self._stop_event.wait(backoff.next())

    def _connect(self, path: Path) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError:  # EMFILE and friends: retry later rather than kill the thread
            return None
        try:
            sock.settimeout(_SEED_TIMEOUT)  # a wedged compositor must not block stop()
            sock.connect(str(path))
            sock.settimeout(None)
        except (OSError, ValueError):
            sock.close()
            return None
        with self._sock_lock:
            if self.stopping:
                sock.close()
                return None
            self._sock = sock
        return sock

    def _close(self, sock: socket.socket) -> None:
        with self._sock_lock:
            if self._sock is sock:
                self._sock = None
        try:
            sock.close()
        except OSError:
            pass

    # -- seed ------------------------------------------------------------------
    def _request(self, path: Path, command: bytes) -> bytes | None:
        """One-shot request on .socket.sock: send, read until EOF, close (1 s overall).

        None on failure or once stop() is called (stop() shuts this socket down too, so a
        wedged compositor can't hold the thread for the timeout)."""
        deadline = time.monotonic() + _SEED_TIMEOUT
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError:
            return None
        with self._sock_lock:
            if self.stopping:
                sock.close()
                return None
            self._req_sock = sock
        try:
            sock.settimeout(_SEED_TIMEOUT)
            sock.connect(str(path))
            if self.stopping:  # stop() came before connect(): its shutdown() missed us
                return None
            sock.sendall(command)
            chunks, size = [], 0
            while size < _MAX_REPLY:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                sock.settimeout(remaining)
                data = sock.recv(65536)
                if not data:
                    return None if self.stopping else b"".join(chunks)
                chunks.append(data)
                size += len(data)
            return None
        except (OSError, ValueError):
            return None
        finally:
            with self._sock_lock:
                if self._req_sock is sock:
                    self._req_sock = None
            sock.close()

    def _seed(self, path: Path) -> bool:
        """Report the active window from `j/activewindow`; False if the request failed."""
        reply = self._request(path, b"j/activewindow")
        if reply is None:
            return False
        try:
            info = json.loads(reply.decode("utf-8", errors="replace"))
        except ValueError:
            return False
        if not isinstance(info, dict) or not info:
            self._emit(None, None, None)  # `{}` = nothing focused
            return True
        app = info.get("class")
        pid = info.get("pid")
        title = info.get("title")
        self._emit(app if isinstance(app, str) else None,
                   title if isinstance(title, str) else None,
                   pid if isinstance(pid, int) else None)
        return True

    # -- events ----------------------------------------------------------------
    def _read_events(self, sock: socket.socket, request_path: Path) -> None:
        buf = bytearray()
        while not self.stopping:
            data = sock.recv(65536)
            if not data:
                return  # Hyprland went away (or stop() shut the socket down)
            buf += data
            while not self.stopping:  # a burst of cut lines means one re-read each: stay stoppable
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(buf[:nl])
                del buf[:nl + 1]
                parsed = parse_event(line)
                if parsed is None:
                    continue
                app, title = parsed
                if maybe_truncated(line):
                    title = None  # cut short: the "Private Browsing" suffix may be gone
                    if not self._seed(request_path) and not self.stopping:
                        self._emit(app, None, None, private=is_firefox_family(app))
                    continue
                self._emit(app, title, None)
            if len(buf) > _MAX_LINE:
                buf.clear()  # absurdly long line: drop it rather than grow forever
