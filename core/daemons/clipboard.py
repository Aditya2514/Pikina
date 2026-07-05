import threading
import ctypes
import time
import win32api
import win32gui
import win32con
import win32clipboard
from core.mcm.provenance import UNTRUSTED

WM_CLIPBOARDUPDATE = 0x031D

class ClipboardDaemon(threading.Thread):
    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.hwnd = None

    def run(self):
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = "PikinaClipboardListener"
        wc.hInstance = win32api.GetModuleHandle(None)
        
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass  # Class already registered
            
        self.hwnd = win32gui.CreateWindow(
            wc.lpszClassName,
            "PikinaClipboardListenerWindow",
            0, 0, 0, 0, 0,
            0, 0, wc.hInstance, None
        )
        
        # Register zero-idle listener
        ctypes.windll.user32.AddClipboardFormatListener(self.hwnd)
        
        # Blocking message pump for zero-idle operation
        win32gui.PumpMessages()

    def stop(self):
        if self.hwnd:
            ctypes.windll.user32.RemoveClipboardFormatListener(self.hwnd)
            win32gui.PostMessage(self.hwnd, win32con.WM_QUIT, 0, 0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            self._on_clipboard_change()
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _on_clipboard_change(self):
        try:
            win32clipboard.OpenClipboard()
            
            # Privacy: Check for sensitive data flags
            # Password managers often use:
            # - "ExcludeClipboardContentFromMonitor"
            # - "Clipboard Viewer Ignore"
            sensitive = False
            format_id = win32clipboard.EnumClipboardFormats(0)
            while format_id:
                try:
                    name = win32clipboard.GetClipboardFormatName(format_id)
                    if name in ("ExcludeClipboardContentFromMonitor", "Clipboard Viewer Ignore"):
                        sensitive = True
                        break
                except:
                    pass
                format_id = win32clipboard.EnumClipboardFormats(format_id)
            
            if sensitive:
                win32clipboard.CloseClipboard()
                return

            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if text and text.strip():
                    self.bus.publish(
                        topic="clipboard.copied",
                        payload={"content": text.strip()[:10000]}, # limit size
                        provenance=UNTRUSTED
                    )
            win32clipboard.CloseClipboard()
        except Exception as e:
            # Clipboard might be locked by another process momentarily
            pass
