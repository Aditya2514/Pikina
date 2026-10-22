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
import hmac
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


_TRUE, _FALSE = {"true", "yes", "on", "1"}, {"false", "no", "off", "0", ""}


def _text(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _clean_entry(raw) -> dict:
    """Hand-edited entries, made safe: wrong types are dropped, never guessed into something risky."""
    if isinstance(raw, str):             # "x": "~/code/x" is shorthand for {"path": ...}
        raw = {"path": raw}
    if not isinstance(raw, dict):
        raw = {}
    entry = dict(DEFAULTS)
    entry["path"] = _text(raw.get("path"))
    entry["editor"] = _text(raw["editor"]) if "editor" in raw else DEFAULTS["editor"]
    term = raw.get("terminal", True)
    if isinstance(term, str):
        term = True if term.strip().lower() in _TRUE else False if term.strip().lower() in _FALSE else term.strip()
    entry["terminal"] = term if isinstance(term, (bool, str)) else bool(term)
    entry["dev"] = _text(raw.get("dev"))
    ws = raw.get("workspace")
    entry["workspace"] = ws if (isinstance(ws, int) and not isinstance(ws, bool) and ws > 0) or \
        (isinstance(ws, str) and ws.strip()) else None
    for key in ("apps", "urls"):
        items = raw.get(key) or []
        items = [items] if isinstance(items, str) else items
        entry[key] = [i.strip() for i in items if isinstance(i, str) and i.strip()] if isinstance(items, list) else []
    if isinstance(raw.get("dev_approved"), str):
        entry["dev_approved"] = raw["dev_approved"]
    return entry


def load() -> dict:
    """{name: entry} with defaults filled in. Raises ProjectError if projects.json has a typo."""
    try:
        data = settings.read_json(_file())
    except settings.SettingsError as exc:
        raise ProjectError(str(exc)) from exc
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise ProjectError(f"{_file()}: \"projects\" must be an object.")
    return {clean_name(k): _clean_entry(v) for k, v in projects.items()}


def get(name) -> dict | None:
    return load().get(clean_name(name))


def folder_of(entry: dict) -> Path | None:
    """The project folder; a relative path counts from your home folder, never from Pikina's."""
    path = entry.get("path")
    if not path:
        return None
    folder = Path(os.path.expanduser(str(path)))
    return (folder if folder.is_absolute() else Path.home() / folder).resolve()


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
        for venv in (".venv", "venv"):  # Django usually lives in the project's virtualenv
            if (folder / venv / "bin" / "python").exists():
                return f"{venv}/bin/python manage.py runserver"
            if (folder / venv / "Scripts" / "python.exe").exists():
                return f"{venv}\\Scripts\\python manage.py runserver"
        return "python manage.py runserver"
    return None


def add(name, path) -> dict:
    """Save a project. The folder must exist; the dev command is detected but never run until you allow it."""
    folder = Path(os.path.expanduser(str(path).strip().strip("\"'")))
    if not folder.is_absolute():
        raise ProjectError("Give the full folder, e.g. ~/code/pikina or /home/you/code/pikina.")
    folder = folder.resolve()
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
        found = False
        for key in list(projects):
            if clean_name(key) == name:
                entry = projects.pop(key)
                found = True
                if isinstance(entry, dict):
                    removed.update(entry)
        if not found:
            raise ProjectError(f"No project called '{name}'.")
    _edit(drop)
    return removed


def dev_digest(command: str, folder: Path | None) -> str:
    """
    The approval for one command in one folder, keyed with this install's secret: an
    approval copied in from another machine's projects.json doesn't count here.
    """
    from pikina.activity.keys import _alias_secret
    return hmac.new(_alias_secret(), f"dev\0{command}\0{folder}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def approve_dev(name, digest: str) -> None:
    name = clean_name(name)

    def mark(projects):
        for key, entry in projects.items():
            if clean_name(key) == name and isinstance(entry, dict):
                entry["dev_approved"] = digest
    _edit(mark)


def _repo_config(folder: Path) -> str:
    git = folder / ".git"
    try:
        if git.is_file():  # a worktree or submodule: "gitdir: <path>"
            target = git.read_text(encoding="utf-8", errors="replace").strip().removeprefix("gitdir:").strip()
            git = (folder / target).resolve()
        return (git / "config").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def git_summary(folder: Path) -> dict | None:
    """
    Branch, uncommitted changes and ahead/behind (as of the last fetch), or None outside git.

    `git status` can run programs named in the project's own .git/config (fsmonitor, clean
    filters). That's code from the project folder, so: fsmonitor is forced off, and a repo
    whose config defines filters gets no status rather than running them unasked.
    """
    if not (folder / ".git").exists():
        return None
    if "[filter" in _repo_config(folder):
        return {"skipped": "this repo's own git config defines filters, so status isn't run automatically"}
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(folder),
                              "status", "--porcelain=v2", "--branch"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError):
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
