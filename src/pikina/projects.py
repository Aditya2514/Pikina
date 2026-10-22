"""
Your projects, for the project switcher ("work on pikina"): projects.json in the data folder.

    {"projects": {"pikina": {
        "path": "~/code/pikina",      folder; omit for a mode with no folder ("college")
        "editor": "code",             app to open the folder in; null to skip
        "terminal": true,             open a terminal there (true, a terminal name, or false)
        "dev": "npm run dev",         command to start in that terminal; null for none
        "workspace": 2,               Hyprland/Sway workspace to switch to first; null to stay
        "apps": [],                   other apps to open ("firefox", "obsidian")
        "urls": [],                   http(s) links to open ("https://…")
        "dev_approved": "<hash>"      set by Pikina once you allow the dev command
    }}}

Edit the file by hand for anything the commands don't cover. Saves are locked and
atomic, and a file with a typo is never overwritten.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from pikina import paths, settings

_NAME = re.compile(r"^[a-z0-9][a-z0-9 _.-]{0,39}$")
DEFAULTS = {"path": None, "editor": "code", "terminal": True, "dev": None, "workspace": None,
            "apps": [], "urls": []}


class ProjectError(Exception):
    pass


def _file() -> Path:
    return paths.data_file(paths.PROJECTS)


def clean_name(name) -> str:
    return " ".join(str(name or "").strip().strip("\"'").lower().split())


def load() -> dict:
    """{name: entry} with defaults filled in. Raises ProjectError if projects.json has a typo."""
    try:
        data = settings.read_json(_file())
    except settings.SettingsError as exc:
        raise ProjectError(str(exc)) from exc
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise ProjectError(f"{_file()}: \"projects\" must be an object.")
    return {clean_name(k): {**DEFAULTS, **v} for k, v in projects.items() if isinstance(v, dict)}


def get(name) -> dict | None:
    return load().get(clean_name(name))


def folder_of(entry: dict) -> Path | None:
    path = entry.get("path")
    return Path(os.path.expanduser(str(path))).resolve() if path else None


def _edit(mutate) -> dict:
    def apply(data):
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ProjectError(f"{_file()}: \"projects\" must be an object.")
        mutate(projects)
    try:
        return settings.edit_json(_file(), apply)
    except settings.SettingsError as exc:
        raise ProjectError(str(exc)) from exc


def detect_dev_command(folder: Path) -> str | None:
    """A likely 'start the dev server' command for a folder, or None."""
    pkg = folder / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        script = "dev" if "dev" in scripts else "start" if "start" in scripts else None
        if script:
            if (folder / "pnpm-lock.yaml").exists():
                return f"pnpm run {script}"
            if (folder / "yarn.lock").exists():
                return f"yarn {script}"
            if (folder / "bun.lockb").exists() or (folder / "bun.lock").exists():
                return f"bun run {script}"
            return f"npm run {script}"
    if (folder / "manage.py").is_file():
        return "python manage.py runserver"
    return None


def add(name, path) -> dict:
    """Save a project. The folder must exist; the dev command is detected but never run until you allow it."""
    folder = Path(os.path.expanduser(str(path).strip().strip("\"'"))).resolve()
    if not folder.is_dir():
        raise ProjectError(f"No folder at {folder}.")
    name = clean_name(name) or clean_name(folder.name)
    if not _NAME.match(name):
        raise ProjectError("Project names are up to 40 letters, digits, spaces, '.', '_' or '-'.")
    home = Path.home()
    shown = "~/" + str(folder.relative_to(home)).replace(os.sep, "/") if folder.is_relative_to(home) else str(folder)
    entry = {**DEFAULTS, "path": shown, "dev": detect_dev_command(folder)}

    def put(projects):
        if name in {clean_name(k) for k in projects}:
            raise ProjectError(f"You already have a project called '{name}'. Remove it first.")
        projects[name] = entry
    _edit(put)
    return {"name": name, **entry}


def remove(name) -> dict:
    name = clean_name(name)
    removed = {}

    def drop(projects):
        for key in list(projects):
            if clean_name(key) == name:
                removed.update(projects.pop(key))
        if not removed:
            raise ProjectError(f"No project called '{name}'.")
    _edit(drop)
    return removed


def dev_digest(command: str, folder: Path | None) -> str:
    return hashlib.sha256(f"{command}\0{folder}".encode("utf-8")).hexdigest()[:16]


def approve_dev(name, digest: str) -> None:
    name = clean_name(name)

    def mark(projects):
        for key, entry in projects.items():
            if clean_name(key) == name and isinstance(entry, dict):
                entry["dev_approved"] = digest
    _edit(mark)


def git_summary(folder: Path) -> dict | None:
    """Branch, uncommitted changes and ahead/behind (as of the last fetch), or None outside git."""
    if not (folder / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(folder), "status", "--porcelain=v2", "--branch"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    branch, ahead, behind, changed = "?", 0, 0, 0
    for line in out.stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            parts = line.split()
            ahead, behind = int(parts[2]), -int(parts[3])
        elif not line.startswith("#"):
            changed += 1
    return {"branch": branch, "uncommitted": changed, "ahead": ahead, "behind": behind}
