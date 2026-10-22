"""project.open — 'work on pikina': switch to everything a project needs, in one go."""
from pikina import projects
from pikina.osbridge import apps, consent, launch


def _list_hint() -> str:
    try:
        names = sorted(projects.load())
    except projects.ProjectError:
        names = []
    if names:
        return "Your projects: " + ", ".join(names) + "."
    return "Save one first: 'add project <name> at <folder>'."


def run(params: dict) -> dict:
    name = projects.clean_name(params.get("name"))
    try:
        entry = projects.get(name)
    except projects.ProjectError as exc:
        if params.get("loose"):  # "airplane mode" must still reach the model when projects.json has a typo
            return {"status": "no_match", "message": str(exc)}
        return {"status": "error", "reason": str(exc)}
    if entry is None:
        if params.get("loose"):
            # "<something> mode" that isn't one of your projects: not ours, let the model try
            return {"status": "no_match", "message": f"No project or mode called '{name}'."}
        return {"status": "error", "reason": f"No project called '{name}'. {_list_hint()}"}

    folder = projects.folder_of(entry)
    if folder is not None and not folder.is_dir():
        return {"status": "error", "reason": f"The folder for '{name}' is missing: {folder}"}
    where = str(folder) if folder else None
    done, problems = [], []

    def note(label, result):
        if result.get("status") == "ok":
            done.append(label)
        elif result.get("status") != "skipped":
            problems.append(f"{label}: {result.get('reason', 'failed')}")

    if entry.get("workspace") not in (None, ""):
        note(f"workspace {entry['workspace']}", launch.switch_workspace(entry["workspace"]))

    terminal = entry.get("terminal")
    preferred = terminal if isinstance(terminal, str) else None
    if where and entry.get("editor"):
        note(str(entry["editor"]), launch.open_editor(str(entry["editor"]), where, preferred))

    dev = (entry.get("dev") or "").strip() or None
    if where and dev:
        digest = projects.dev_digest(dev, folder)
        allowed = entry.get("dev_approved") == digest
        if not allowed and not consent.can_ask():
            problems.append(f"dev server not started: Pikina can't ask you to allow `{dev}` (install zenity, "
                            f"or run `pikina` in a terminal and say 'work on {name}' there once)")
        elif not allowed:
            # It runs code from the project folder, so ask once (and again if the command changes).
            allowed = consent.ask(f"Start the dev command for '{name}'?\n\n  {dev}\n\nin {where}\n\n"
                                  "Pikina will remember your answer until the command or folder changes.")
            if allowed:
                try:
                    projects.approve_dev(name, digest)
                except projects.ProjectError as exc:
                    problems.append(f"couldn't remember the approval ({exc})")
            if not allowed:
                problems.append(f"dev server not started (`{dev}` not allowed)")
        if allowed:
            note(f"terminal running `{dev}`", launch.open_terminal(where, dev, preferred))
        elif terminal:
            note("terminal", launch.open_terminal(where, None, preferred))
    elif where and terminal:
        note("terminal", launch.open_terminal(where, None, preferred))

    for app in entry.get("apps") or []:
        note(str(app), apps.launch(str(app)))
    for url in entry.get("urls") or []:
        note(str(url), launch.open_url(str(url)))

    git = projects.git_summary(folder) if folder else None
    lines = [f"Working on {name}: " + (", ".join(done) if done else "nothing to open")]
    if git and git.get("skipped"):
        lines.append(f"Git: {git['skipped']}")
    elif git:
        bits = [f"on {git['branch']}", f"{git['uncommitted']} uncommitted"]
        if git["ahead"]:
            bits.append(f"{git['ahead']} not pushed")
        if git["behind"]:
            bits.append(f"{git['behind']} behind (pull first)")
        lines.append("Git: " + ", ".join(bits))
    lines += [f"! {p}" for p in problems]
    status = "error" if problems and not done else "ok"
    out = {"status": status, "project": name, "opened": done, "problems": problems, "git": git,
           "message": "\n".join(lines)}
    if status == "error":
        out["reason"] = "; ".join(problems)
    return out
