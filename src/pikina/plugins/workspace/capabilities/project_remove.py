"""project.remove — 'remove project pikina'. Only the saved entry; your folder is never touched."""
from pikina import projects


def run(params: dict) -> dict:
    name = projects.clean_name(params.get("name"))
    try:
        projects.remove(name)
    except projects.ProjectError as exc:
        return {"status": "error", "reason": str(exc)}
    return {"status": "ok", "message": f"Removed project '{name}'. Your files are untouched."}
