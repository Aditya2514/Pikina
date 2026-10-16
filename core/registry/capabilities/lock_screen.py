"""
Capability: system.lock_screen
Locks the screen: LockWorkStation() on Windows; loginctl / xdg-screensaver /
the desktop's ScreenSaver D-Bus API on Linux.
Permission level: 1 (interface execution)
"""
from core.osbridge.apps import lock_screen


def run(params: dict) -> dict:
    return lock_screen()
