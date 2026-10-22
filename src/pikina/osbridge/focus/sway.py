"""
Sway / i3 focus listener over raw i3-ipc ($SWAYSOCK or $I3SOCK).

Frame: struct "=6sII" (b"i3-ipc", payload length, message type) + JSON payload.
We SUBSCRIBE to ["window", "workspace", "shutdown"] and then ask GET_TREE once on the
same connection, so no focus change can slip in between the seed and the first event
(replies and events are told apart by the event bit in the type).

window::focus only fires for containers with a view, so switching to an empty
workspace (or closing its last window) sends just workspace::focus / window::close.
For those we ask GET_TREE again on the same socket and re-seed from the reply (an
empty focused workspace -> nothing focused).

If $SWAYSOCK no longer works (it names the pid of a sway that has since restarted), we
also try the newest live $XDG_RUNTIME_DIR/sway-ipc.<uid>.<pid>.sock.

app = container.app_id (native Wayland) or container.window_properties.class
(XWayland / i3); the container name (window title) is only used for the
private-window check.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
from pathlib import Path

from pikina.osbridge.focus import FocusSource, _pid_alive

HEADER = struct.Struct("=6sII")
MAGIC = b"i3-ipc"
SUBSCRIBE = 2
GET_TREE = 4
EVENT_BIT = 0x80000000
EVENT_WORKSPACE = EVENT_BIT | 0
EVENT_WINDOW = EVENT_BIT | 3
EVENT_SHUTDOWN = EVENT_BIT | 6
EVENTS = ["window", "workspace", "shutdown"]
_MAX_PAYLOAD = 64 << 20
_CONNECT_TIMEOUT = 1.0

_NO_SOCKET = "no sway/i3 IPC socket found (retrying)"
_UNREACHABLE = "can't reach the sway/i3 IPC socket (retrying)"
_REFUSED = "sway/i3 refused the event subscription (retrying)"
_LOST = "lost the sway/i3 IPC connection (reconnecting)"


def pack(msg_type: int, payload: bytes = b"") -> bytes:
    return HEADER.pack(MAGIC, len(payload), msg_type) + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def read_message(sock: socket.socket):
    """(type, payload bytes) or None when the connection closed / the frame is bad."""
    header = _recv_exact(sock, HEADER.size)
    if header is None:
        return None
    magic, length, msg_type = HEADER.unpack(header)
    if magic != MAGIC or length > _MAX_PAYLOAD:
        return None
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None
    return msg_type, payload


def _decode(payload: bytes) -> dict:
    """JSON object from a payload; {} when it isn't one."""
    try:
        value = json.loads(payload.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def window_of(node) -> tuple[str | None, str | None, int | None] | None:
    """(app, title, pid) when `node` is a window, else None (workspace, output, ...)."""
    if not isinstance(node, dict):
        return None
    props = node.get("window_properties")
    app = node.get("app_id") or (props.get("class") if isinstance(props, dict) else None)
    pid = node.get("pid")
    pid = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None
    if not app and pid is None and not isinstance(props, dict):
        return None
    title = node.get("name")
    return (app if isinstance(app, str) else None,
            title if isinstance(title, str) else None,
            pid)


def live_sway_sockets(env) -> list[str]:
    """$XDG_RUNTIME_DIR/sway-ipc.<uid>.<pid>.sock whose sway pid is alive, newest first."""
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        return []
    found = []
    try:
        paths = list(Path(runtime).glob("sway-ipc.*.sock"))
    except OSError:
        return []
    for path in paths:
        parts = path.name.split(".")  # sway-ipc, uid, pid, sock
        try:
            pid = int(parts[-2])
            mtime = path.stat().st_mtime
        except (IndexError, ValueError, OSError):
            continue
        if _pid_alive(pid):
            found.append((mtime, str(path)))
    found.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in found]


def focused_node(tree):
    """The node with focused == true (iterative walk over nodes + floating_nodes)."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("focused") is True:
            return node
        for key in ("nodes", "floating_nodes"):
            children = node.get(key)
            if isinstance(children, list):
                stack.extend(children)
    return None


class SwaySource(FocusSource):
    backend = "sway"

    def __init__(self, on_focus, **options):
        super().__init__(on_focus, **options)
        self._sock_lock = threading.Lock()
        self._sock: socket.socket | None = None

    def _socket_paths(self) -> list[str]:
        """$SWAYSOCK (or $I3SOCK) first; for sway also any live sway-ipc socket, in case
        $SWAYSOCK names an instance that has since exited."""
        configured = self._env.get("SWAYSOCK") or self._env.get("I3SOCK") or None
        paths = [configured] if configured else []
        if self._env.get("SWAYSOCK") or not configured:
            paths += [p for p in live_sway_sockets(self._env) if p not in paths]
        return paths

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
            paths = self._socket_paths()
            sock = None
            for path in paths:
                if self.stopping:
                    break
                sock = self._connect(path)
                if sock is not None:
                    break
            if sock is not None:
                failure = _LOST
                try:
                    failure = self._session(sock, backoff)
                except Exception:
                    pass  # any surprise = lost connection; reconnect below
                finally:
                    self._close(sock)
                self._lost(failure)
            elif not self.stopping:
                self._lost(_UNREACHABLE if paths else _NO_SOCKET)
            if self.stopping:
                break
            self._stop_event.wait(backoff.next())

    def _connect(self, path: str) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError:  # EMFILE and friends: retry later rather than kill the thread
            return None
        try:
            sock.settimeout(_CONNECT_TIMEOUT)
            sock.connect(path)
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

    def _session(self, sock: socket.socket, backoff) -> str:
        """Subscribe, seed, then follow events until shutdown / disconnect / stop.

        Returns the failure to report once the session is over."""
        sock.sendall(pack(SUBSCRIBE, json.dumps(EVENTS).encode()))
        sock.sendall(pack(GET_TREE))
        while not self.stopping:
            msg = read_message(sock)
            if msg is None:
                return _LOST
            msg_type, payload = msg
            if msg_type == SUBSCRIBE:
                if _decode(payload).get("success") is not True:
                    return _REFUSED
                self.failure = None
                backoff.reset()
            elif msg_type == GET_TREE:
                self._seed(payload)
            elif msg_type == EVENT_WINDOW:
                self._window_event(sock, payload)
            elif msg_type == EVENT_WORKSPACE:
                if _decode(payload).get("change") == "focus":
                    sock.sendall(pack(GET_TREE))  # maybe an empty workspace: re-seed from the tree
            elif msg_type == EVENT_SHUTDOWN:
                return _LOST  # sway is exiting / i3 restarting: reconnect
        return _LOST

    def _seed(self, payload: bytes) -> None:
        tree = _decode(payload)
        if not tree:
            return
        win = window_of(focused_node(tree))
        if win is None:
            self._emit(None, None, None)  # an empty workspace has focus
        else:
            self._emit(*win)

    def _window_event(self, sock: socket.socket, payload: bytes) -> None:
        event = _decode(payload)
        change = event.get("change")
        if change in ("close", "move"):
            # the last window on the workspace may have closed or moved away: re-seed
            sock.sendall(pack(GET_TREE))
            return
        if change != "focus":
            return
        win = window_of(event.get("container"))
        if win is not None:
            self._emit(*win)
