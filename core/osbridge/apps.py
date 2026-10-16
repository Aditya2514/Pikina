"""
Application launching and screen locking, per OS.
"""
import os
import shlex
import subprocess
from pathlib import Path

from core.osbridge import IS_WINDOWS, which_first

# Friendly name -> candidate executables. First one found on PATH wins.
WINDOWS_APPS: dict[str, list[str]] = {
    "vs code": ["code"], "vscode": ["code"], "code": ["code"],
    "chrome": ["chrome"], "firefox": ["firefox"],
    "notepad": ["notepad"], "explorer": ["explorer"],
    "terminal": ["wt"], "powershell": ["powershell"],
    "calculator": ["calc"], "calc": ["calc"], "paint": ["mspaint"],
}

LINUX_APPS: dict[str, list[str]] = {
    "vs code": ["code", "codium"], "vscode": ["code", "codium"], "code": ["code", "codium"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "firefox": ["firefox"],
    "browser": ["firefox", "google-chrome", "chromium", "chromium-browser"],
    "notepad": ["gnome-text-editor", "gedit", "kate", "kwrite", "mousepad", "xed", "pluma", "featherpad"],
    "text editor": ["gnome-text-editor", "gedit", "kate", "kwrite", "mousepad", "xed", "pluma"],
    "explorer": ["nautilus", "dolphin", "thunar", "nemo", "pcmanfm", "caja"],
    "files": ["nautilus", "dolphin", "thunar", "nemo", "pcmanfm", "caja"],
    "file manager": ["nautilus", "dolphin", "thunar", "nemo", "pcmanfm", "caja"],
    "terminal": ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "kitty", "alacritty", "xterm"],
    "calculator": ["gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "mate-calc"],
    "calc": ["gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "mate-calc"],
    "paint": ["pinta", "kolourpaint", "drawing", "mtpaint", "gimp"],
}

_DESKTOP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
]
_FIELD_CODES = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}


def _parse_desktop_file(path: Path) -> dict:
    entry, in_main = {}, False
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("["):
                in_main = line == "[Desktop Entry]"
                continue
            if in_main and "=" in line:
                k, v = line.split("=", 1)
                entry.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    return entry


def find_desktop_entry(name: str) -> list[str] | None:
    """Find an installed app by its menu name or .desktop id; return its Exec command."""
    wanted = name.lower().strip()
    for d in _DESKTOP_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            entry = _parse_desktop_file(f)
            if entry.get("NoDisplay", "").lower() == "true" or "Exec" not in entry:
                continue
            names = {entry.get("Name", "").lower(), f.stem.lower(), f.stem.split(".")[-1].lower()}
            if wanted in names:
                try:
                    argv = [a for a in shlex.split(entry["Exec"]) if a not in _FIELD_CODES]
                except ValueError:
                    continue
                if argv:
                    return argv
    return None


def resolve_linux_command(name: str) -> list[str] | None:
    key = name.lower().strip()
    if key in LINUX_APPS:
        exe = which_first(LINUX_APPS[key])
        if exe:
            return [exe]
    if os.path.sep in name and os.access(name, os.X_OK):
        return [name]
    exe = which_first([name, key, key.replace(" ", "-"), key.replace(" ", "")])
    if exe:
        return [exe]
    return find_desktop_entry(name)


def launch(name: str) -> dict:
    """Launch an application by friendly name, executable name or path."""
    if IS_WINDOWS:
        resolved = (WINDOWS_APPS.get(name.lower()) or [name])[0]
        try:
            subprocess.Popen(["start", "", resolved], shell=True)
            return {"status": "ok", "launched": resolved, "alias_resolved": resolved != name}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    argv = resolve_linux_command(name)
    if not argv:
        return {"status": "error", "reason": f"Executable not found: '{name}'"}
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,  # survives backend restarts, no zombie ties
        )
        return {"status": "ok", "launched": argv[0], "pid": proc.pid,
                "alias_resolved": os.path.basename(argv[0]) != name}
    except FileNotFoundError:
        return {"status": "error", "reason": f"Executable not found: '{argv[0]}'"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


# Each command is tried in order until one exits 0.
_LINUX_LOCKERS = [
    ["loginctl", "lock-session"],
    ["xdg-screensaver", "lock"],
    ["dbus-send", "--session", "--dest=org.freedesktop.ScreenSaver", "--type=method_call",
     "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
    ["dbus-send", "--session", "--dest=org.gnome.ScreenSaver", "--type=method_call",
     "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.Lock"],
    ["gnome-screensaver-command", "-l"],
    ["xflock4"],
    ["dm-tool", "lock"],
]


def lock_screen() -> dict:
    if IS_WINDOWS:
        import ctypes
        if ctypes.windll.user32.LockWorkStation():
            return {"status": "ok", "message": "Workstation locked."}
        return {"status": "error", "reason": f"LockWorkStation() failed. Error code: {ctypes.get_last_error()}"}

    tried = []
    for cmd in _LINUX_LOCKERS:
        if not which_first([cmd[0]]):
            continue
        tried.append(cmd[0] if cmd[0] != "dbus-send" else cmd[2].split("=", 1)[1])
        try:
            if subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0:
                return {"status": "ok", "message": f"Screen locked via {cmd[0]}."}
        except (OSError, subprocess.TimeoutExpired):
            continue
    if not tried:
        return {"status": "error", "reason": "No screen locker found (install systemd-logind, xdg-utils or your desktop's locker)."}
    return {"status": "error", "reason": f"All screen lockers failed: {', '.join(tried)}"}
