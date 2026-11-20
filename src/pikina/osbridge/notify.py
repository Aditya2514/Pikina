"""
Desktop notifications (Linux: notify-send, from libnotify). Used where Pikina has no
window or terminal to answer in, such as voice from a hotkey.

notify() never raises. It returns False when nothing could be shown, so callers can
fall back to printing (a compositor keybind's output lands in its log).
"""
import shutil
import subprocess

from pikina.osbridge import IS_LINUX, has_display

APP_NAME = "Pikina"


def available() -> bool:
    return IS_LINUX and has_display() and shutil.which("notify-send") is not None


def notify(title: str, body: str = "", urgent: bool = False, seconds: float | None = None) -> bool:
    if not available():
        return False
    cmd = ["notify-send", "-a", APP_NAME, "-u", "critical" if urgent else "normal"]
    if seconds:
        cmd += ["-t", str(int(seconds * 1000))]
    cmd += [title, body[:500]]
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
