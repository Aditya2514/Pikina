"""
Native consent dialog for high-risk actions.

Windows: MessageBoxW.
Linux:   zenity / kdialog / yad, falling back to a terminal prompt when running
         in the foreground of a terminal (the CLI). If no way to ask exists,
         the action is DENIED — never silently approved.
"""
import os
import sys
import subprocess

from core.osbridge import IS_WINDOWS, has_display, which_first

_TITLE = "Pikina OS — Confirm Action"
_DIALOG_TIMEOUT_S = 120  # unanswered dialogs count as "no"


def _ask_windows(message: str) -> bool:
    import ctypes
    MB_YESNO, MB_ICONWARNING, IDYES = 0x04, 0x30, 6
    result = ctypes.windll.user32.MessageBoxW(0, message, _TITLE, MB_YESNO | MB_ICONWARNING)
    return result == IDYES


def _ask_linux_gui(message: str) -> bool | None:
    """Returns True/False for an answer, None if no dialog tool could be shown."""
    if not has_display():
        return None
    tool = which_first(["zenity", "kdialog", "yad"])
    if not tool:
        return None
    name = os.path.basename(tool)
    if name == "zenity":
        cmd = [tool, "--question", "--no-markup", "--icon-name=dialog-warning",
               f"--title={_TITLE}", f"--text={message}", f"--timeout={_DIALOG_TIMEOUT_S}"]
    elif name == "kdialog":
        cmd = [tool, "--title", _TITLE, "--warningyesno", message]
    else:  # yad
        cmd = [tool, "--question", f"--title={_TITLE}", f"--text={message}",
               f"--timeout={_DIALOG_TIMEOUT_S}"]
    try:
        proc = subprocess.run(cmd, timeout=_DIALOG_TIMEOUT_S + 5,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return None
    return proc.returncode == 0


def _in_foreground_terminal() -> bool:
    try:
        fd = sys.stdin.fileno()
        return os.isatty(fd) and os.getpgrp() == os.tcgetpgrp(fd)
    except (OSError, ValueError, AttributeError):
        return False


def _ask_terminal(message: str) -> bool | None:
    if not _in_foreground_terminal():
        return None
    try:
        answer = input(f"\n{message}\nType 'yes' to proceed: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def ask(message: str) -> bool:
    """Ask the human. True only on an explicit yes."""
    if IS_WINDOWS:
        return _ask_windows(message)
    for asker in (_ask_linux_gui, _ask_terminal):
        answer = asker(message)
        if answer is not None:
            return answer
    print("[Consent] No dialog tool (zenity/kdialog/yad) or terminal available — action denied.")
    return False
