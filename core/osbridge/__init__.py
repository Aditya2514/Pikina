"""
OS bridge — the only place that knows which operating system Pikina runs on.

Everything above this layer (router, registry, daemons) calls these helpers
instead of touching Win32 or Linux desktop tools directly.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def has_display() -> bool:
    """True when a graphical session is reachable (always True on Windows)."""
    if IS_WINDOWS:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def which_first(candidates) -> str | None:
    """Return the first candidate executable found on PATH."""
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def _xdg_user_dir(key: str) -> Path | None:
    """Resolve an XDG user dir (DESKTOP, DOCUMENTS, DOWNLOAD), which may be localized."""
    tool = shutil.which("xdg-user-dir")
    if tool:
        try:
            out = subprocess.run([tool, key], capture_output=True, text=True, timeout=2).stdout.strip()
            if out and Path(out) != Path.home():
                return Path(out)
        except Exception:
            pass
    return None


def user_dirs(include_documents: bool = True) -> list[Path]:
    """
    The user's Desktop / Documents / Downloads folders that actually exist.
    Includes OneDrive-redirected folders on Windows and localized XDG folders on Linux.
    """
    home = Path.home()
    names = ["Desktop", "Documents", "Downloads"] if include_documents else ["Desktop", "Downloads"]
    roots = [home / n for n in names]

    if IS_WINDOWS:
        roots += [home / "OneDrive" / n for n in names]
    elif IS_LINUX:
        xdg_keys = {"Desktop": "DESKTOP", "Documents": "DOCUMENTS", "Downloads": "DOWNLOAD"}
        for n in names:
            p = _xdg_user_dir(xdg_keys[n])
            if p:
                roots.append(p)

    seen, result = set(), []
    for r in roots:
        try:
            key = r.resolve()
        except OSError:
            continue
        if r.exists() and key not in seen:
            seen.add(key)
            result.append(r)
    return result
