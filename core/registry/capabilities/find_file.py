"""
Capability: fs.find_file
Searches for files by glob name pattern within a root directory.
Permission level: 0 (read-only)
"""
import fnmatch
from pathlib import Path


def run(params: dict) -> dict:
    name        = params.get("name", "").strip()
    root        = Path(params.get("root", Path.home()))
    max_results = int(params.get("max_results", 20))

    if not name:
        return {"status": "error", "reason": "Missing required param: 'name'"}

    if not root.exists():
        return {"status": "error", "reason": f"Root directory does not exist: '{root}'"}

    results = []
    skipped = 0

    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                if fnmatch.fnmatch(p.name.lower(), name.lower()):
                    results.append(str(p))
                    if len(results) >= max_results:
                        break
            except PermissionError:
                skipped += 1
    except PermissionError:
        pass

    return {
        "status":  "ok",
        "results": results,
        "count":   len(results),
        "skipped_dirs": skipped,
        "root":    str(root),
        "pattern": name,
    }
