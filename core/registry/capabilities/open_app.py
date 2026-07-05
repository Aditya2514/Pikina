"""
Capability: app.open
Launches an application by executable name or full path.
Permission level: 1 (interface execution)
"""
import subprocess
import sys


# Common name aliases so users can say "open vs code" not type a path
_ALIASES: dict = {
    "vs code":  "code",
    "vscode":   "code",
    "code":     "code",
    "chrome":   "chrome",
    "firefox":  "firefox",
    "notepad":  "notepad",
    "explorer": "explorer",
    "terminal": "wt",
    "powershell": "powershell",
    "calculator": "calc",
    "paint":    "mspaint",
}


def run(params: dict) -> dict:
    path = params.get("path", "").strip()
    if not path:
        return {"status": "error", "reason": "Missing required param: 'path'"}

    # Resolve alias if present
    resolved = _ALIASES.get(path.lower(), path)

    try:
        if sys.platform == "win32":
            subprocess.Popen(["start", "", resolved], shell=True)
        else:
            subprocess.Popen([resolved])
        return {"status": "ok", "launched": resolved, "alias_resolved": resolved != path}
    except FileNotFoundError:
        return {"status": "error", "reason": f"Executable not found: '{resolved}'"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
