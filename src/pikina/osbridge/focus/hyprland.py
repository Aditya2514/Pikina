"""
Hyprland focus listener.

Reads the event socket $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock
(/tmp/hypr/... for Hyprland < 0.40) and seeds the current window with a one-shot
`j/activewindow` request on .socket.sock.

Hyprland drops event clients that fall 64 events behind, so the reader only parses
`activewindow>>CLASS,TITLE` lines and calls on_focus; nothing slow happens here.
Socket events carry no pid (the seed does), so pid is None for them.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from pikina.osbridge.focus import FocusSource, _hyprland_instances

LEGACY_BASE = Path("/tmp/hypr")  # Hyprland < 0.40
_SEED_TIMEOUT = 1.0
_MAX_REPLY = 1 << 20
_MAX_LINE = 1 << 20


def _safe_signature(his: str) -> bool:
    return bool(his) and "/" not in his and "\\" not in his and his not in (".", "..")


def socket_dir(env) -> Path | None:
    """The live instance dir holding .socket2.sock, or None.

    Uses HYPRLAND_INSTANCE_SIGNATURE first; when it is missing or stale (Hyprland was
    restarted), the newest live $XDG_RUNTIME_DIR/hypr/*/hyprland.lock instance.
    """
    his = env.get("HYPRLAND_INSTANCE_SIGNATURE") or ""
    runtime = env.get("XDG_RUNTIME_DIR")
    if _safe_signature(his):
        bases = ([Path(runtime, "hypr")] if runtime else []) + [LEGACY_BASE]
        for base in bases:
            d = base / his
            if (d / ".socket2.sock").exists():
                return d
    for d in _hyprland_instances(env):
        if (d / ".socket2.sock").exists():
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


class HyprlandSource(FocusSource):
    backend = "hyprland"

    def __init__(self, on_focus, **options):
        super().__init__(on_focus, **options)
        self._sock_lock = threading.Lock()
        self._sock: socket.socket | None = None

    # -- stop ------------------------------------------------------------------
    def _interrupt(self) -> None:
        with self._sock_lock:
            sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- main loop -------------------------------------------------------------
    def _loop(self) -> None:
        backoff = self._backoff()
        while not self.stopping:
            directory = socket_dir(self._env)
            sock = self._connect(directory / ".socket2.sock") if directory else None
            if sock is not None:
                backoff.reset()
                try:
                    self._seed(directory / ".socket.sock")
                    self._read_events(sock)
                except Exception:
                    pass  # treat any surprise as a lost connection; reconnect below
                finally:
                    self._close(sock)
            if self.stopping:
                break
            self._stop_event.wait(backoff.next())

    def _connect(self, path: Path) -> socket.socket | None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(path))
        except OSError:
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
        """One-shot request on .socket.sock: send, read until EOF, close (1 s overall)."""
        deadline = time.monotonic() + _SEED_TIMEOUT
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(_SEED_TIMEOUT)
            sock.connect(str(path))
            sock.sendall(command)
            chunks, size = [], 0
            while size < _MAX_REPLY:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                sock.settimeout(remaining)
                data = sock.recv(65536)
                if not data:
                    return b"".join(chunks)
                chunks.append(data)
                size += len(data)
            return None
        except OSError:
            return None
        finally:
            sock.close()

    def _seed(self, path: Path) -> None:
        reply = self._request(path, b"j/activewindow")
        if reply is None:
            return
        try:
            info = json.loads(reply.decode("utf-8", errors="replace"))
        except ValueError:
            return
        if not isinstance(info, dict) or not info:
            self._emit(None, None, None)  # `{}` = nothing focused
            return
        app = info.get("class")
        pid = info.get("pid")
        title = info.get("title")
        self._emit(app if isinstance(app, str) else None,
                   title if isinstance(title, str) else None,
                   pid if isinstance(pid, int) else None)

    # -- events ----------------------------------------------------------------
    def _read_events(self, sock: socket.socket) -> None:
        buf = bytearray()
        while not self.stopping:
            data = sock.recv(65536)
            if not data:
                return  # Hyprland went away (or stop() shut the socket down)
            buf += data
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(buf[:nl])
                del buf[:nl + 1]
                parsed = parse_event(line)
                if parsed is not None:
                    app, title = parsed
                    self._emit(app, title, None)
            if len(buf) > _MAX_LINE:
                buf.clear()  # absurdly long line: drop it rather than grow forever
