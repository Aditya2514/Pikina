import os
import subprocess
from pathlib import Path
from core.daemons.file_indexer import FileIndexerDaemon

def run(params: dict) -> dict:
    name        = params.get("name", "").strip()
    root_param  = params.get("root")
    max_results = int(params.get("max_results", 20))

    if not name:
        return {"status": "error", "reason": "Missing required param: 'name'"}

    if "*" not in name and "?" not in name:
        name = f"*{name}*"

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
    
    # Fast path: Query the SQLite index first
    try:
        db_results = FileIndexerDaemon.search(name, limit=max_results)
        if root_param:
            # Filter results by root if specific root requested
            root_str = str(Path(root_param).resolve())
            db_results = [r for r in db_results if r.startswith(root_str)]
            
        for r in db_results:
            if r not in results:
                results.append(r)
    except Exception:
        pass

    if len(results) < max_results:
        # Fallback to slow Windows WHERE if we need more results
        for root_dir in search_roots:
            if len(results) >= max_results:
                break
            
        try:
            # Native Windows WHERE command is orders of magnitude faster than os.walk
            process = subprocess.run(
                ["where", "/r", str(root_dir), name],
                capture_output=True,
                text=True,
                # Prevents a cmd window from popping up
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
            
            if process.returncode == 0:
                lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
                for line in lines:
                    if line not in results:
                        results.append(line)
                        if len(results) >= max_results:
                            break
                            
        except Exception:
            pass

    return {
        "status":         "ok",
        "results":        results,
        "count":          len(results),
        "roots_searched": [str(r) for r in search_roots],
        "pattern":        name,
    }
