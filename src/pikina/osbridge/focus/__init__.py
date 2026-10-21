"""
Focus listeners: which app has keyboard focus, per OS / compositor.

    backend, reason = detect_backend()
    if backend:
        source = make_source(backend, on_focus)   # unstarted daemon thread
        source.start()
        ...
        source.stop()

on_focus(app, private, pid) runs on the source's own thread for the initial
window and for every change of (app, private). `app` is the raw class / app_id /
exe name ("org.keepassxc.KeePassXC", "Code", "Code.exe"), None when nothing is
focused.

Privacy: only app names leave this package. Window titles are read in memory,
solely so is_private_window() can spot private browser windows, then dropped.
They are never stored on a source, printed, logged, put in an exception message
or passed to on_focus.

Importing this package (or any backend module) starts no threads and works on
every OS; OS-specific calls only happen inside a started source.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

__all__ = ["detect_backend", "is_private_window", "make_source", "FocusSource", "BACKENDS"]

BACKENDS = ("hyprland", "sway", "x11", "windows")

_WAYLAND_UNSUPPORTED = ("GNOME/KDE on Wayland don't expose the active window; not supported yet "
                        "(works on Hyprland, Sway/i3, X11 and Windows)")
_NO_XPROP = ("xprop isn't installed; install xorg-xprop "
             "(Arch: xorg-xprop, Debian/Ubuntu: x11-utils, Fedora: xprop)")


# --------------------------------------------------------------------------- detection
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if sys.platform == "win32":  # os.kill(pid, 0) would terminate the process there
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _hyprland_instances(env) -> list[Path]:
    """Live Hyprland instance dirs from $XDG_RUNTIME_DIR/hypr/*/hyprland.lock, newest first.

    A lock file reads "<pid>\\n<wayland socket>\\n". It counts when the pid is alive and,
    if WAYLAND_DISPLAY is set, its wayland socket matches it.
    """
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        return []
    wayland = env.get("WAYLAND_DISPLAY") or ""
    found = []
    try:
        locks = list(Path(runtime, "hypr").glob("*/hyprland.lock"))
    except OSError:
        return []
    for lock in locks:
        try:
            lines = lock.read_text(encoding="utf-8", errors="replace").splitlines()
            mtime = lock.stat().st_mtime
        except OSError:
            continue
        try:
            pid = int(lines[0].strip())
        except (IndexError, ValueError):
            continue
        if not _pid_alive(pid):
            continue
        if wayland:
            sock = lines[1].strip() if len(lines) > 1 else ""
            if sock != wayland and os.path.basename(sock) != os.path.basename(wayland):
                continue
        found.append((mtime, lock.parent))
    found.sort(key=lambda item: item[0], reverse=True)
    return [d for _, d in found]


def detect_backend(env: dict | None = None, platform: str | None = None) -> tuple[str | None, str | None]:
    """(backend, reason). backend in {"hyprland","sway","x11","windows"} or None with a human reason.

    Order: windows -> hyprland (HYPRLAND_INSTANCE_SIGNATURE, or a live
    $XDG_RUNTIME_DIR/hypr/*/hyprland.lock) -> sway/i3 (SWAYSOCK / I3SOCK) -> any other
    Wayland session (WAYLAND_DISPLAY or XDG_SESSION_TYPE=wayland) = unsupported. We never
    fall back to X11 on Wayland: XWayland only sees X11 windows, so the result would be
    wrong for every native Wayland app. -> X11 (DISPLAY, needs xprop on PATH).
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform

    if platform == "win32":
        return "windows", None
    if platform == "darwin":
        return None, "macOS isn't supported yet"

    if env.get("HYPRLAND_INSTANCE_SIGNATURE") or _hyprland_instances(env):
        return "hyprland", None
    if env.get("SWAYSOCK") or env.get("I3SOCK"):
        return "sway", None
    if env.get("WAYLAND_DISPLAY") or (env.get("XDG_SESSION_TYPE") or "").lower() == "wayland":
        return None, _WAYLAND_UNSUPPORTED
    if env.get("DISPLAY"):
        path = env.get("PATH")
        if path and shutil.which("xprop", path=path):
            return "x11", None
        return None, _NO_XPROP
    return None, "no graphical session"


# --------------------------------------------------------------------------- privacy
_ALWAYS_PRIVATE_APPS = ("torbrowser", "mullvadbrowser")


def is_private_window(app: str | None, title: str | None) -> bool:
    """True for private/incognito browser windows that can be detected from the title.

    - Firefox family (Firefox, LibreWolf, Floorp, Waterfox, ...): the title ends with
      "Private Browsing" ("Page — Mozilla Firefox Private Browsing", or the older
      "Page - Mozilla Firefox (Private Browsing)").
    - Microsoft Edge InPrivate: the title carries "[InPrivate]" (best effort).
    - Tor Browser / Mullvad Browser are always private, whatever the title.

    Chromium-family incognito windows (Chrome, Chromium, Brave, Vivaldi, Opera) can NOT
    be detected: neither their title nor their window class / app_id says incognito.
    Users who care should exclude those browsers by name.

    The title is only inspected here; it is never stored.
    """
    if app:
        squashed = "".join(ch for ch in str(app).lower() if ch.isalnum())
        if any(name in squashed for name in _ALWAYS_PRIVATE_APPS):
            return True
    if not title or not isinstance(title, str):
        return False
    tail = title.rstrip().rstrip(")").rstrip()
    if tail.endswith("Private Browsing"):
        return True
    return "[InPrivate]" in title


# --------------------------------------------------------------------------- sources
_UNSET = object()


class Backoff:
    """Reconnect delays: initial, doubling, capped at maximum."""

    def __init__(self, initial: float = 1.0, maximum: float = 30.0):
        self.initial, self.maximum = initial, maximum
        self._delay = initial

    def next(self) -> float:
        delay = self._delay
        self._delay = min(self._delay * 2, self.maximum)
        return delay

    def reset(self) -> None:
        self._delay = self.initial


class FocusSource(threading.Thread):
    """Base class for focus listeners: a daemon thread calling on_focus(app, private, pid).

    Subclasses implement _loop() (runs until stop is requested, reconnecting as needed)
    and may override _interrupt() to unblock a blocking read when stop() is called.
    """

    backend = "base"

    def __init__(self, on_focus, *, env: dict | None = None,
                 initial_backoff: float = 1.0, max_backoff: float = 30.0):
        super().__init__(daemon=True, name=f"FocusSource-{self.backend}")
        self._on_focus = on_focus
        self._env = dict(os.environ if env is None else env)
        self._stop_event = threading.Event()
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._last = _UNSET  # last (app, private) handed to on_focus

    # -- lifecycle -------------------------------------------------------------
    def run(self) -> None:
        try:
            self._loop()
        except Exception as exc:  # never include the message: it could carry window data
            if not self._stop_event.is_set():
                print(f"[Focus] {self.backend} listener stopped ({type(exc).__name__})")

    def stop(self) -> None:
        """Ask the source to finish; unblocks promptly. Safe twice and before start()."""
        self._stop_event.set()
        try:
            self._interrupt()
        except Exception:
            pass

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    # -- for subclasses --------------------------------------------------------
    def _loop(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _interrupt(self) -> None:
        """Unblock whatever _loop is waiting on (socket, child process)."""

    def _backoff(self) -> Backoff:
        return Backoff(self._initial_backoff, self._max_backoff)

    def _emit(self, app, title, pid) -> None:
        """Classify, dedupe on (app, private) and call on_focus. `title` is not kept."""
        if app is not None:
            app = str(app).strip() or None
        private = is_private_window(app, title)
        title = None  # noqa: F841 - drop our reference before calling out
        key = (app, private)
        if key == self._last:
            return
        self._last = key
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            pid = None
        try:
            self._on_focus(app, private, pid)
        except Exception as exc:
            print(f"[Focus] on_focus callback failed ({type(exc).__name__})")


def make_source(backend: str, on_focus, **options) -> FocusSource:
    """Return an unstarted FocusSource for `backend` (see detect_backend).

    on_focus(app: str | None, private: bool, pid: int | None) is called on the source's
    own thread for the initial window and every focus change; it must be cheap.
    `options` (env, initial_backoff, max_backoff) are mainly for tests.
    """
    if backend == "hyprland":
        from pikina.osbridge.focus.hyprland import HyprlandSource as cls
    elif backend == "sway":
        from pikina.osbridge.focus.sway import SwaySource as cls
    elif backend == "x11":
        from pikina.osbridge.focus.x11 import X11Source as cls
    elif backend == "windows":
        from pikina.osbridge.focus.windows import WindowsSource as cls
    else:
        raise ValueError(f"unknown focus backend: {backend!r}")
    return cls(on_focus, **options)
