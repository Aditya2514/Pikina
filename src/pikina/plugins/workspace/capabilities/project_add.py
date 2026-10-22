"""project.add — 'add project pikina at ~/code/pikina'."""
from pikina import projects


def run(params: dict) -> dict:
    path = str(params.get("path") or "").strip()
    if not path:
        return {"status": "error", "reason": "Which folder? For example: add project pikina at ~/code/pikina"}
    try:
        entry = projects.add(params.get("name"), path)
    except projects.ProjectError as exc:
        return {"status": "error", "reason": str(exc)}
    lines = [f"Saved project '{entry['name']}' ({entry['path']}). Say 'work on {entry['name']}' to switch to it.",
             f"It opens: {entry['editor'] or 'no editor'}" + (", a terminal" if entry["terminal"] else "")]
    if entry["dev"]:
        lines.append(f"Dev command found: `{entry['dev']}`. It runs only after you allow it the first time.")
    lines.append("Workspace, apps and links: edit projects.json in your Pikina data folder.")
    return {"status": "ok", "project": entry, "message": "\n".join(lines)}
