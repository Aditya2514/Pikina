"""
Windows focus listener: polls GetForegroundWindow() once a second.

Polling is simple and can't hang (no hooks or message loop); switches shorter than
a second are noise for an activity log anyway. ctypes only, no pywin32. The title
(GetWindowTextW) is read only for the private-window check and then dropped.

The OS calls are methods (_setup, _foreground_window, _window_pid, _window_title,
_process_name) so tests can replace them; importing this module works on any OS
because user32 is only loaded inside run().
"""
from __future__ import annotations

import ctypes

from pikina.osbridge.focus import FocusSource

_MAX_TITLE = 1024


class WindowsSource(FocusSource):
    backend = "windows"
    poll_interval = 1.0

    def __init__(self, on_focus, **options):
        super().__init__(on_focus, **options)
        self._user32 = None

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
    def poll_once(self) -> None:
        hwnd = self._foreground_window()
        if not hwnd:
            self._emit(None, None, None)
            return
        pid = self._window_pid(hwnd)
        app = self._process_name(pid) if pid else None
        self._emit(app, self._window_title(hwnd), pid)

    def _loop(self) -> None:
        self._setup()
        while not self.stopping:
            try:
                self.poll_once()
            except Exception:
                pass  # a window vanishing mid-poll is normal; try again next tick
            self._stop_event.wait(self.poll_interval)
