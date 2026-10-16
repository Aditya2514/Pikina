"""
Clipboard daemon — picks the right implementation for the current OS.
Every clipboard event is published as UNTRUSTED_DATA: it is indexed, never executed.
"""
import sys

if sys.platform == "win32":
    from core.daemons.clipboard_win import ClipboardDaemon
else:
    from core.daemons.clipboard_linux import ClipboardDaemon

__all__ = ["ClipboardDaemon"]
