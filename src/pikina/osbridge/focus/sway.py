"""
Sway / i3 focus listener over raw i3-ipc ($SWAYSOCK or $I3SOCK).

Frame: struct "=6sII" (b"i3-ipc", payload length, message type) + JSON payload.
We SUBSCRIBE to ["window", "shutdown"] and then ask GET_TREE once on the same
connection, so no focus change can slip in between the seed and the first event
(replies and events are told apart by the event bit in the type).

app = container.app_id (native Wayland) or container.window_properties.class
(XWayland / i3); the container name (window title) is only used for the
private-window check.
"""
from __future__ import annotations

import json
import socket
import struct
import threading

from pikina.osbridge.focus import FocusSource

HEADER = struct.Struct("=6sII")
MAGIC = b"i3-ipc"
SUBSCRIBE = 2
GET_TREE = 4
EVENT_BIT = 0x80000000
EVENT_WINDOW = EVENT_BIT | 3
EVENT_SHUTDOWN = EVENT_BIT | 6
_MAX_PAYLOAD = 64 << 20


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

    def _socket_path(self) -> str | None:
        return self._env.get("SWAYSOCK") or self._env.get("I3SOCK") or None

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
            path = self._socket_path()
            sock = self._connect(path) if path else None
            if sock is not None:
                try:
                    self._session(sock, backoff)
                except Exception:
                    pass  # any surprise = lost connection; reconnect below
                finally:
                    self._close(sock)
            if self.stopping:
                break
            self._stop_event.wait(backoff.next())

    def _connect(self, path: str) -> socket.socket | None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
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

    def _session(self, sock: socket.socket, backoff) -> None:
        """Subscribe, seed, then follow events until shutdown / disconnect / stop."""
        sock.sendall(pack(SUBSCRIBE, json.dumps(["window", "shutdown"]).encode()))
        sock.sendall(pack(GET_TREE))
        while not self.stopping:
            msg = read_message(sock)
            if msg is None:
                return
            msg_type, payload = msg
            if msg_type == SUBSCRIBE:
                if _decode(payload).get("success") is not True:
                    return
                backoff.reset()
            elif msg_type == GET_TREE:
                self._seed(payload)
            elif msg_type == EVENT_WINDOW:
                self._window_event(payload)
            elif msg_type == EVENT_SHUTDOWN:
                return  # sway is exiting / i3 restarting: reconnect

    def _seed(self, payload: bytes) -> None:
        tree = _decode(payload)
        if not tree:
            return
        win = window_of(focused_node(tree))
        if win is None:
            self._emit(None, None, None)  # an empty workspace has focus
        else:
            self._emit(*win)

    def _window_event(self, payload: bytes) -> None:
        event = _decode(payload)
        if event.get("change") != "focus":
            return
        win = window_of(event.get("container"))
        if win is not None:
            self._emit(*win)
