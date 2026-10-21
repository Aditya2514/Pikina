"""
Capability: app.open
Launches an application by friendly name, executable name or full path.
Windows uses `start`; Linux resolves common names (notepad -> gedit/kate/...),
PATH executables and installed .desktop entries.
Permission level: 1 (interface execution)
"""
from pikina.osbridge.apps import launch


def run(params: dict) -> dict:
    path = params.get("path", "").strip()
    if not path:
        return {"status": "error", "reason": "Missing required param: 'path'"}
    return launch(path)


def parse(params: dict) -> dict:
    """Typed app names are matched case-insensitively ("Open VS Code")."""
    params["path"] = params["path"].lower()
    return params
