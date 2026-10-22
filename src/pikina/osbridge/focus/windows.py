"""
Windows focus listener: polls GetForegroundWindow() once a second.

Polling is simple and can't hang (no hooks or message loop); switches shorter than
a second are noise for an activity log anyway. ctypes only, no pywin32. The title
(GetWindowTextW, read in full) is used only for the private-window check and then
dropped.

UWP / Store apps (Calculator, Settings, Photos, ...) are drawn inside a frame window
owned by ApplicationFrameHost.exe. For those we look through the frame's child windows
for the hosted app's CoreWindow (the first child owned by another process) and report
that process; with none (e.g. the app is suspended) the app is unknown (None).

The OS calls are methods (_setup, _foreground_window, _window_pid, _window_title,
_child_windows, _process_name) so tests can replace them; importing this module works
on any OS because user32 is only loaded inside run().
"""
from __future__ import annotations

import ctypes

from pikina.osbridge.focus import FocusSource

_MAX_TITLE = 65536            # characters; far beyond any real title, so suffixes survive
_MAX_CHILDREN = 512           # stop enumerating a frame's child windows after this many
_FRAME_HOST = "applicationframehost.exe"
_ERRORS_BEFORE_FAILURE = 3    # consecutive failed polls before `failure` is reported
_POLL_FAILING = "can't read the foreground window (retrying)"


class WindowsSource(FocusSource):
    backend = "windows"
    poll_interval = 1.0

    def __init__(self, on_focus, **options):
        super().__init__(on_focus, **options)
        self._user32 = None
        self._enum_proc = None  # WNDENUMPROC type, set by _setup

    # -- OS calls (monkeypatchable) ----------------------------------------------
    def _setup(self) -> None:
        from ctypes import wintypes  # only importable/meaningful on Windows

        user32 = ctypes.WinDLL("user32", use_last_error=True)  # own instance: argtypes stay local
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
        user32.EnumChildWindows.restype = wintypes.BOOL
        self._enum_proc = enum_proc
        self._user32 = user32

    def _foreground_window(self):
        """HWND of the foreground window, or None/0 when there is none (lock screen, UAC)."""
        return self._user32.GetForegroundWindow()

    def _window_pid(self, hwnd) -> int | None:
        from ctypes import wintypes

        pid = wintypes.DWORD(0)
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None

    def _window_title(self, hwnd) -> str | None:
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        length = min(length, _MAX_TITLE)
        buf = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _child_windows(self, hwnd) -> list:
        """Child (and descendant) HWNDs of `hwnd`, in EnumChildWindows order."""
        found = []

        def collect(child, _lparam):
            found.append(child)
            return len(found) < _MAX_CHILDREN  # FALSE stops the enumeration

        callback = self._enum_proc(collect)  # referenced until the call returns
        self._user32.EnumChildWindows(hwnd, callback, 0)
        del callback
        return found

    def _process_name(self, pid: int) -> str | None:
        try:
            import psutil
        except ImportError:
            return None
        try:
            return psutil.Process(pid).name() or None
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # ZombieProcess is a NoSuchProcess
            return None
        except (psutil.Error, OSError, ValueError):
            return None

    # -- polling -----------------------------------------------------------------
    def _hosted_app(self, frame, frame_pid):
        """(hwnd, pid, name) of the UWP app inside an ApplicationFrameHost frame, or None."""
        for child in self._child_windows(frame):
            if not child:
                continue
            pid = self._window_pid(child)
            if pid and pid != frame_pid:
                return child, pid, self._process_name(pid)
        return None

    def poll_once(self) -> None:
        hwnd = self._foreground_window()
        if not hwnd:
            self._emit(None, None, None)
            return
        pid = self._window_pid(hwnd)
        app = self._process_name(pid) if pid else None
        if app and app.lower() == _FRAME_HOST:
            hosted = self._hosted_app(hwnd, pid)
            if hosted is None:
                self._emit(None, None, None)  # can't tell which Store app this is
                return
            _, pid, app = hosted  # the frame's title is the app's title: keep reading hwnd
        self._emit(app, self._window_title(hwnd), pid)

    def _loop(self) -> None:
        self._setup()
        errors = 0
        while not self.stopping:
            try:
                self.poll_once()
            except Exception:
                errors += 1  # a window vanishing mid-poll is normal; try again next tick
                if errors >= _ERRORS_BEFORE_FAILURE:
                    self._lost(_POLL_FAILING)
            else:
                errors = 0
                self.failure = None
            self._stop_event.wait(self.poll_interval)
