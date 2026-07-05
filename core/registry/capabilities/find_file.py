"""
Capability: fs.find_file
Searches for files by glob name pattern within specified or sensible root directories.
Permission level: 0 (read-only)
Optimized with max_depth limiting and excluded directories for sub-second responses.
"""
import os
import fnmatch
from pathlib import Path

_EXCLUDED_DIRS = {
    "node_modules", ".git", "appdata", "__pycache__", "venv", ".venv",
    ".next", "dist", "build", "$recycle.bin", ".gemini", "temp", "tmp"
}


def run(params: dict) -> dict:
    name        = params.get("name", "").strip()
    root_param  = params.get("root")
    max_results = int(params.get("max_results", 20))
    max_depth   = int(params.get("max_depth", 4))

    if not name:
        return {"status": "error", "reason": "Missing required param: 'name'"}

    # Determine search targets
    if root_param:
        search_roots = [Path(root_param)]
    else:
        home = Path.home()
        # Sensible user search targets if root is unspecified
        search_roots = [
            Path.cwd(),
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
            home / "OneDrive" / "Desktop",
            home / "OneDrive" / "Documents",
            home / "OneDrive" / "Downloads",
        ]
        search_roots = [r for r in search_roots if r.exists()]

    results = []
    skipped = 0

    for root_dir in search_roots:
        if len(results) >= max_results:
            break
        if not root_dir.exists():
            continue

        base_depth = str(root_dir).count(os.sep)

        try:
            for dirpath, dirnames, filenames in os.walk(str(root_dir)):
                current_depth = dirpath.count(os.sep) - base_depth
                if current_depth > max_depth:
                    dirnames.clear()
                    continue

                # Filter out heavy directories in-place
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in _EXCLUDED_DIRS and not d.startswith(".")
                ]

                for fname in filenames:
                    if fnmatch.fnmatch(fname.lower(), name.lower()):
                        results.append(os.path.join(dirpath, fname))
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
        except PermissionError:
            skipped += 1

    return {
        "status":         "ok",
        "results":        results,
        "count":          len(results),
        "skipped_dirs":   skipped,
        "roots_searched": [str(r) for r in search_roots],
        "pattern":        name,
    }
