"""
Capability: system.lock_screen
Locks the Windows workstation via Win32 LockWorkStation().
Permission level: 1 (interface execution)
"""
import ctypes
import sys


def run(params: dict) -> dict:
    if sys.platform != "win32":
        return {"status": "error", "reason": "system.lock_screen is Windows-only."}
    
    result = ctypes.windll.user32.LockWorkStation()
    if result:
        return {"status": "ok", "message": "Workstation locked."}
    
    error_code = ctypes.get_last_error()
    return {"status": "error", "reason": f"LockWorkStation() failed. Error code: {error_code}"}
