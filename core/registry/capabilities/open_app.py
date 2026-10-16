"""
Capability: app.open
Launches an application by friendly name, executable name or full path.
Windows uses `start`; Linux resolves common names (notepad -> gedit/kate/...),
PATH executables and installed .desktop entries.
Permission level: 1 (interface execution)
"""
from core.osbridge.apps import launch


def run(params: dict) -> dict:
    path = params.get("path", "").strip()
    if not path:
        return {"status": "error", "reason": "Missing required param: 'path'"}
    return launch(path)
