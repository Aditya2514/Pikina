"""
Where Pikina keeps its files.

Everything Pikina writes (databases, settings, the API token, your profile and
aliases) lives in one per-user folder, never inside the installed code:

    Windows  %LOCALAPPDATA%\\Pikina
    macOS    ~/Library/Application Support/Pikina
    Linux    $XDG_DATA_HOME/pikina  (default ~/.local/share/pikina)

Set PIKINA_DATA_DIR to use a different folder (tests, portable installs).
The Electron app (desktop/src/paths.js) resolves the same folder.
"""
import os
import shutil
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# File names inside the data folder
MAIN_DB          = "pikina.db"            # to-dos + calendar
REPLAY_DB        = "replay_store.sqlite"  # event audit log
ACTIVITY_DB      = "activity.db"          # learning layer: what you did (normalised), app switches
VECTOR_DB        = "vector_memory.db"
FILE_INDEX_DB    = "index.db"
KNOWLEDGE_GRAPH  = "knowledge_graph.json"
FILE_GRAPH       = "file_graph.json"
PROFILE          = "profile.json"
ALIASES          = "aliases.json"
PROJECTS         = "projects.json"         # project switcher: your projects and what each opens
SETTINGS         = "settings.json"
API_TOKEN        = ".api_token"
ENV_FILE         = ".env"


def data_dir() -> Path:
    override = os.getenv("PIKINA_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Pikina"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Pikina"
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pikina"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_file(name: str) -> Path:
    """Absolute path of a file in the data folder (resolved at call time)."""
    return data_dir() / name


def source_checkout_root() -> Path | None:
    """The repository root when running from a git checkout, else None."""
    root = PACKAGE_DIR.parents[1]
    return root if (root / "pyproject.toml").exists() else None


# Where versions before 0.4 kept each file, relative to the repo root / launch folder
_LEGACY_LOCATIONS = {
    MAIN_DB:         ["data/pikina.db"],
    VECTOR_DB:       ["data/vector_memory.db"],
    FILE_INDEX_DB:   ["data/index.db"],
    KNOWLEDGE_GRAPH: ["data/knowledge_graph.json"],
    FILE_GRAPH:      ["data/file_graph.json"],
    SETTINGS:        ["data/settings.json"],
    API_TOKEN:       ["data/.api_token"],
    REPLAY_DB:       ["core/eventbus/replay_store.sqlite"],
    PROFILE:         ["core/preferences/profile.json"],
    ALIASES:         ["core/router/aliases.json"],
}


def migrate_legacy_data(extra_roots=()) -> list[str]:
    """
    Copy data left behind by pre-0.4 versions into the data folder.
    Only fills files that don't exist yet, so it is safe to run on every start.
    Returns the names of files copied.
    """
    roots = [r for r in (source_checkout_root(), Path.cwd(), *extra_roots) if r]
    copied = []
    for name, rel_paths in _LEGACY_LOCATIONS.items():
        dest = data_file(name)
        if dest.exists():
            continue
        for root in roots:
            for rel in rel_paths:
                src = Path(root) / rel
                if src.is_file():
                    shutil.copy2(src, dest)
                    copied.append(name)
                    break
            if dest.exists():
                break
    return copied
