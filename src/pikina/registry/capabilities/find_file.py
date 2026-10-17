"""
Capability: fs.find_file
Finds files by name or wildcard. Queries the background SQLite index first,
then falls back to a live search of the user's folders:
  - Windows: native `where /r` (fast)
  - Linux:   bounded os.walk (depth- and time-limited, skips hidden/vendor dirs)
"""
import os
import time
import fnmatch
import subprocess
from pathlib import Path

from pikina.daemons.file_indexer import FileIndexerDaemon
from pikina.osbridge import IS_WINDOWS, user_dirs

_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", ".git", ".cache"}
_WALK_BUDGET_S = 3.0


def _where_search(root: Path, pattern: str, limit: int) -> list[str]:
    try:
        proc = subprocess.run(
            ["where", "/r", str(root), pattern],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),  # no console flash
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()][:limit]


def _walk_search(root: Path, pattern: str, limit: int, max_depth: int, deadline: float) -> list[str]:
    pattern = pattern.lower()
    root_depth = len(root.parts)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        if time.monotonic() > deadline:
            break
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in filenames:
            if fnmatch.fnmatchcase(name.lower(), pattern):
                found.append(os.path.join(dirpath, name))
                if len(found) >= limit:
                    return found
    return found


def run(params: dict) -> dict:
    name        = params.get("name", "").strip()
    root_param  = params.get("root")
    max_results = int(params.get("max_results", 20))
    max_depth   = int(params.get("max_depth", 8))

    if not name:
        return {"status": "error", "reason": "Missing required param: 'name'"}

    if "*" not in name and "?" not in name:
        name = f"*{name}*"

    if root_param:
        search_roots = [Path(root_param)]
    else:
        search_roots = [Path.cwd()] + user_dirs()

    results: list[str] = []

    # Fast path: the SQLite index maintained by FileIndexerDaemon
    try:
        db_results = FileIndexerDaemon.search(name, limit=max_results)
        if root_param:
            root_str = str(Path(root_param).resolve())
            db_results = [r for r in db_results if r.startswith(root_str)]
        for r in db_results:
            if r not in results:
                results.append(r)
    except Exception:
        pass

    # Slow path: live search of every root until we have enough results
    deadline = time.monotonic() + _WALK_BUDGET_S
    for root_dir in search_roots:
        if len(results) >= max_results:
            break
        remaining = max_results - len(results)
        if IS_WINDOWS:
            hits = _where_search(root_dir, name, remaining)
        else:
            hits = _walk_search(root_dir, name, remaining, max_depth, deadline)
        for h in hits:
            if h not in results:
                results.append(h)

    return {
        "status":         "ok",
        "results":        results[:max_results],
        "count":          len(results[:max_results]),
        "roots_searched": [str(r) for r in search_roots],
        "pattern":        name,
    }
