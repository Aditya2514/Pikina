"""project.list — your saved projects."""
from pikina import paths, projects


def run(params: dict) -> dict:
    try:
        saved = projects.load()
    except projects.ProjectError as exc:
        return {"status": "error", "reason": str(exc)}
    if not saved:
        return {"status": "ok", "projects": {},
                "message": "No projects yet. Add one: add project <name> at <folder>"}
    lines = []
    for name in sorted(saved):
        e = saved[name]
        parts = [e["path"] or "(no folder)"]
        if e.get("editor"):
            parts.append(str(e["editor"]))
        if e.get("dev"):
            parts.append(f"dev: {e['dev']}" + ("" if e.get("dev_approved") else " (not allowed yet)"))
        if e.get("workspace") not in (None, ""):
            parts.append(f"workspace {e['workspace']}")
        extra = len(e.get("apps") or []) + len(e.get("urls") or [])
        if extra:
            parts.append(f"{extra} app/link{'s' if extra != 1 else ''}")
        lines.append(f"{name}: " + " · ".join(parts))
    lines.append(f"Edit: {paths.data_file(paths.PROJECTS)}")
    return {"status": "ok", "projects": saved, "message": "\n".join(lines)}
