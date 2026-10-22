"""
The project switcher (workspace plugin): saving projects, 'work on <project>',
the dev-command approval, workspaces, links, git status, routing and privacy.
Nothing is really launched: process starts are recorded instead.
"""
import json
import subprocess

import pytest

from pikina import paths, projects
from pikina.activity.keys import project_hash
from pikina.activity.store import ActivityStore
from pikina.mcm.orchestrator import Orchestrator
from pikina.osbridge import apps, launch
from pikina.router.tier1 import Tier1Router, match_route


@pytest.fixture
def spawned(monkeypatch):
    """Record every detached start instead of running it; pretend kitty and VS Code are installed."""
    calls = []
    monkeypatch.setattr(launch, "IS_WINDOWS", False)
    monkeypatch.setattr(launch, "_spawn", lambda argv, cwd=None: calls.append((list(argv), cwd)) or 4242)
    monkeypatch.setattr(launch.shutil, "which",
                        lambda name, *a, **k: f"/usr/bin/{name}" if name in ("kitty", "xdg-open", "hyprctl") else None)
    monkeypatch.setattr(apps, "resolve_linux_command",
                        lambda name: ["/usr/bin/code"] if name in ("code", "vs code") else None)
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    return calls


@pytest.fixture
def router(registry):
    return Tier1Router(registry=registry)


@pytest.fixture
def proj(tmp_path):
    folder = tmp_path / "pikina"
    folder.mkdir()
    return folder


def _save(name, **fields):
    def put(data):
        data.setdefault("projects", {})[name] = fields
    from pikina import settings
    settings.edit_json(paths.data_file(paths.PROJECTS), put)


# ---------------------------------------------------------------------------
# Saving projects
# ---------------------------------------------------------------------------

def test_add_detects_the_dev_command(router, tmp_path):
    web = tmp_path / "site"
    web.mkdir()
    (web / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "build": "vite build"}}))
    (web / "pnpm-lock.yaml").write_text("")
    res = router.route(f"add project my site at {web}")
    assert res["status"] == "ok"
    saved = projects.get("my site")
    assert saved["dev"] == "pnpm run dev" and saved["editor"] == "code" and saved["terminal"] is True
    assert "dev_approved" not in json.loads(paths.data_file(paths.PROJECTS).read_text())["projects"]["my site"]
    assert "runs only after you allow it" in res["message"]


@pytest.mark.parametrize("files, expected", [
    ({"package.json": {"scripts": {"start": "node ."}}}, "npm run start"),
    ({"package.json": {"scripts": {"dev": "next"}}, "yarn.lock": ""}, "yarn dev"),
    ({"package.json": {"scripts": {"dev": "x"}}, "bun.lock": ""}, "bun run dev"),
    ({"package.json": {"scripts": {"lint": "eslint"}}}, None),
    ({"manage.py": ""}, "python manage.py runserver"),
    ({"README.md": ""}, None),
])
def test_dev_command_detection(tmp_path, files, expected):
    for name, content in files.items():
        (tmp_path / name).write_text(json.dumps(content) if isinstance(content, dict) else content)
    assert projects.detect_dev_command(tmp_path) == expected


def test_add_uses_the_folder_name_and_refuses_duplicates_and_missing_folders(router, proj, tmp_path):
    assert router.route(f"add project {proj}")["status"] == "ok"
    assert projects.get("pikina") is not None
    assert "already have" in router.route(f"add project pikina at {proj}")["reason"]
    assert "No folder" in router.route(f"add project ghost at {tmp_path / 'nope'}")["reason"]


def test_list_and_remove(router, proj):
    assert "No projects yet" in router.route("list projects")["message"]
    router.route(f"add project pikina at {proj}")
    assert "pikina:" in router.route("projects")["message"]
    assert router.route("remove project pikina")["status"] == "ok"
    assert proj.is_dir()  # the folder itself is untouched
    assert router.route("remove project pikina")["status"] == "error"


def test_a_typo_in_projects_json_is_reported_and_never_overwritten(router, proj):
    f = paths.data_file(paths.PROJECTS)
    f.write_text('{"projects": {oops', encoding="utf-8")
    assert "not valid JSON" in router.route("work on pikina")["reason"]
    assert router.route(f"add project pikina at {proj}")["status"] == "error"
    assert "oops" in f.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Working on a project
# ---------------------------------------------------------------------------

def test_work_on_opens_editor_and_terminal(router, proj, spawned):
    _save("pikina", path=str(proj))
    res = router.route("work on pikina")
    assert res["status"] == "ok", res
    assert spawned == [(["/usr/bin/code", str(proj.resolve())], str(proj.resolve())),
                       (["/usr/bin/kitty", "--directory", str(proj.resolve())], str(proj.resolve()))]
    assert res["opened"] == ["code", "terminal"]


def test_dev_command_asks_once_then_remembers(router, proj, spawned, consent):
    _save("web", path=str(proj), dev="npm run dev")
    consent.answer = True
    first = router.route("work on web")
    assert len(consent.calls) == 1 and "npm run dev" in consent.calls[0]
    argv = spawned[-1][0]
    assert argv[:3] == ["/usr/bin/kitty", "--directory", str(proj.resolve())]
    assert argv[3:] == ["/bin/zsh", "-c", "npm run dev; exec /bin/zsh"]   # stays open after Ctrl+C
    assert "terminal running `npm run dev`" in first["opened"]

    router.route("work on web")
    assert len(consent.calls) == 1                     # remembered

    _save("web", path=str(proj), dev="npm run dev -- --host",
          dev_approved=projects.get("web")["dev_approved"])
    router.route("work on web")
    assert len(consent.calls) == 2                     # the command changed: ask again


def test_declining_the_dev_command_still_opens_a_plain_terminal(router, proj, spawned, consent):
    _save("web", path=str(proj), dev="npm run dev")
    consent.answer = False
    res = router.route("work on web")
    assert res["status"] == "ok"
    assert spawned[-1][0] == ["/usr/bin/kitty", "--directory", str(proj.resolve())]
    assert any("dev server not started" in p for p in res["problems"])
    assert "dev_approved" not in json.loads(paths.data_file(paths.PROJECTS).read_text())["projects"]["web"]


def test_workspace_apps_and_links(router, proj, spawned, monkeypatch):
    ran = []
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda argv, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(apps, "launch", lambda name: {"status": "ok", "launched": name})
    _save("college", workspace=3, editor=None, terminal=False, apps=["obsidian"],
          urls=["https://onlinedegree.iitm.ac.in", "javascript:alert(1)", "file:///etc/passwd"])
    res = router.route("college mode")
    assert ran == [["/usr/bin/hyprctl", "dispatch", "workspace", "3"]]
    assert [a for a, _ in spawned] == [["/usr/bin/xdg-open", "https://onlinedegree.iitm.ac.in"]]
    assert res["opened"] == ["workspace 3", "obsidian", "https://onlinedegree.iitm.ac.in"]
    assert len(res["problems"]) == 2 and all("http(s)" in p for p in res["problems"])


def test_named_workspaces_and_sway(monkeypatch):
    ran = []
    monkeypatch.setattr(launch.shutil, "which", lambda n, *a, **k: f"/usr/bin/{n}" if n == "swaymsg" else None)
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda argv, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    assert launch.switch_workspace(2, env={"SWAYSOCK": "/run/s"})["status"] == "ok"
    assert launch.switch_workspace("dev", env={"SWAYSOCK": "/run/s"})["status"] == "ok"
    assert launch.switch_workspace("x; rm -rf ~", env={"SWAYSOCK": "/run/s"})["status"] == "error"
    assert launch.switch_workspace(2, env={})["status"] == "skipped"
    assert ran == [["/usr/bin/swaymsg", "workspace", "number", "2"], ["/usr/bin/swaymsg", "workspace", "dev"]]


def test_git_status_is_shown(router, proj, spawned):
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    (proj / "notes.txt").write_text("wip")
    _save("pikina", path=str(proj))
    res = router.route("work on pikina")
    assert res["git"]["uncommitted"] == 1
    assert "Git: on " in res["message"] and "1 uncommitted" in res["message"]


def test_missing_folder_and_unknown_projects(router, proj, tmp_path):
    _save("old", path=str(tmp_path / "gone"))
    assert "missing" in router.route("work on old")["reason"]
    res = router.route("work on nothing")
    assert res["status"] == "error" and "Your projects: old" in res["reason"]


def test_unknown_mode_goes_on_to_the_model_and_is_not_logged(registry):
    store = ActivityStore()
    seen = []

    class FakeTier2:
        def route(self, text, origin="typed"):
            seen.append(text)
            return {"status": "ok", "tier": 2}
    mcm = Orchestrator(router=Tier1Router(registry=registry), tier2_router=FakeTier2())
    assert mcm.receive("dark mode")["tier"] == 2
    assert mcm.receive("switch to dark theme")["tier"] == 2
    assert seen == ["dark mode", "switch to dark theme"]
    assert store.recent() == []


def test_phrases_route_to_the_switcher_without_stealing(registry):
    assert match_route("open project pikina", registry)[0] == "project.open"
    assert match_route("open firefox", registry)[0] == "app.open"
    assert match_route("work on client acme", registry) == ("project.open", {"name": "client acme"})
    assert match_route("add project acme at ~/work/acme", registry) == \
        ("project.add", {"name": "acme", "path": "~/work/acme"})


def test_project_names_never_reach_the_activity_log(router, proj, spawned):
    _save("client acme", path=str(proj))
    router.route("work on client acme")
    row = ActivityStore().recent()[0]
    assert row["action"] == "project.open:" + project_hash("client acme")
    data = b"".join(f.read_bytes() for f in paths.data_dir().glob(paths.ACTIVITY_DB + "*"))
    assert b"acme" not in data
    assert 'project "client acme"' in router.route("activity")["message"]


# ---------------------------------------------------------------------------
# Terminals and Windows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("kitty", ["T", "--directory", "/p", "/bin/sh", "-c", "make; exec /bin/sh"]),
    ("alacritty", ["T", "--working-directory", "/p", "-e", "/bin/sh", "-c", "make; exec /bin/sh"]),
    ("foot", ["T", "--working-directory", "/p", "/bin/sh", "-c", "make; exec /bin/sh"]),
    ("wezterm", ["T", "start", "--cwd", "/p", "--", "/bin/sh", "-c", "make; exec /bin/sh"]),
    ("gnome-terminal", ["T", "--working-directory=/p", "--", "/bin/sh", "-c", "make; exec /bin/sh"]),
    ("konsole", ["T", "--workdir", "/p", "-e", "/bin/sh", "-c", "make; exec /bin/sh"]),
])
def test_terminal_argv(name, expected):
    assert launch.terminal_argv(name, "T", "/p", "make", env={"SHELL": "/bin/sh"}) == expected


def test_terminal_choice_respects_terminal_env(monkeypatch):
    monkeypatch.setattr(launch.shutil, "which", lambda n, *a, **k: f"/usr/bin/{n}" if n in ("foot", "kitty") else None)
    assert launch.find_terminal(env={"TERMINAL": "foot"}) == ("foot", "/usr/bin/foot")
    assert launch.find_terminal(env={}) == ("kitty", "/usr/bin/kitty")
    assert launch.find_terminal(preferred="alacritty", env={}) == ("kitty", "/usr/bin/kitty")


def test_windows_terminal_and_editor_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(launch, "IS_WINDOWS", True)
    monkeypatch.setattr(launch, "_spawn", lambda argv, cwd=None: calls.append(argv) or 1)
    monkeypatch.setattr(launch.shutil, "which",
                        lambda n, *a, **k: {"wt": "C:/wt.exe", "code": "C:/code.cmd"}.get(n))
    assert launch.open_terminal("C:/p", "npm run dev")["terminal"] == "wt"
    assert launch.open_editor("vs code", "C:/p")["status"] == "ok"
    assert calls == [["C:/wt.exe", "-d", "C:/p", "cmd", "/K", "npm run dev"], ["C:/code.cmd", "C:/p"]]
