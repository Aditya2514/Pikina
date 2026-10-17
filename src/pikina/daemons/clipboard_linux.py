"""
Linux clipboard daemon.

Wayland (wl-paste) or X11 (xclip / xsel). Polls once per second and publishes
only when the content changes, matching the Windows WM_CLIPBOARDUPDATE behaviour.

Privacy: password managers (KeePassXC, KDE) tag secrets with the
`x-kde-passwordManagerHint` MIME type — such content is never read.
"""
import os
import hashlib
import threading
import subprocess

from pikina.mcm.provenance import UNTRUSTED
from pikina.osbridge import has_display, which_first

_SENSITIVE_TYPES = {"x-kde-passwordManagerHint"}
_MAX_CHARS = 10_000


class _Backend:
    def __init__(self, list_cmd, read_cmd):
        self.list_cmd = list_cmd
        self.read_cmd = read_cmd


def _pick_backend() -> _Backend | None:
    if os.environ.get("WAYLAND_DISPLAY") and which_first(["wl-paste"]):
        return _Backend(["wl-paste", "--list-types"], ["wl-paste", "--no-newline", "--type", "text"])
    if os.environ.get("DISPLAY"):
        if which_first(["xclip"]):
            return _Backend(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
                            ["xclip", "-selection", "clipboard", "-o"])
        if which_first(["xsel"]):
            return _Backend(None, ["xsel", "--clipboard", "--output"])
    return None


def _run(cmd) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


class ClipboardDaemon(threading.Thread):
    def __init__(self, bus, poll_interval: float = 1.0):
        super().__init__(daemon=True, name="ClipboardDaemon")
        self.bus = bus
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._last_hash = None
        self.backend = _pick_backend() if has_display() else None

    def run(self):
        if self.backend is None:
            print("[Clipboard] No clipboard tool found (install wl-clipboard or xclip) — daemon idle.")
            return
        first = True
        while not self._stop_event.is_set():
            self._poll(publish=not first)  # don't re-ingest whatever was copied before startup
            first = False
            self._stop_event.wait(self.poll_interval)

    def stop(self):
        self._stop_event.set()

    def _is_sensitive(self) -> bool:
        if not self.backend.list_cmd:
            return False
        types = _run(self.backend.list_cmd) or ""
        return any(t.strip() in _SENSITIVE_TYPES for t in types.splitlines())

    def _poll(self, publish: bool = True):
        if self._is_sensitive():
            return
        text = _run(self.backend.read_cmd)
        if not text or not text.strip():
            return
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        if digest == self._last_hash:
            return
        self._last_hash = digest
        if publish:
            self.bus.publish(
                topic="clipboard.copied",
                payload={"content": text.strip()[:_MAX_CHARS]},
                provenance=UNTRUSTED,
            )
