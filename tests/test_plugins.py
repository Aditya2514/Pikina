"""
Plugins and manifest phrases: discovery, opt-in for user plugins, broken plugins
skipped without taking Pikina down, phrase priority, and the built-in devtools plugin.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pikina.registry.loader import CapabilityRegistry
from pikina.router.tier1 import Tier1Router, match_route


def _write_plugin(root: Path, name: str, tool: str, regex: str, api_version=1, run_src=None):
    folder = root / "plugins" / name
    caps = folder / "capabilities"
    caps.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps(
        {"name": name, "version": "0.0.1", "api_version": api_version, "description": "test"}))
    stem = tool.replace(".", "_")
    (caps / f"{stem}.json").write_text(json.dumps({
        "tool": tool, "description": "test", "permission_level": 0, "read_only": True,
        "supports_rollback": False, "params": {},
        "patterns": [{"regex": regex, "priority": 5}],
    }))
    (caps / f"{stem}.py").write_text(run_src or
        "def run(params):\n    return {'status': 'ok', 'message': 'hello from plugin'}\n")
    return folder


def _enable(data_dir: Path, *names):
    (data_dir / "settings.json").write_text(json.dumps({"plugins_enabled": list(names)}))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def test_builtin_devtools_plugin_loads(registry):
    assert [p.name for p in registry.plugins] == ["devtools"]
    assert registry.plugin_errors == []
    for tool in ("dev.doctor", "dev.ports", "dev.free_port"):
        assert registry.origin(tool) == "devtools"
    assert registry.origin("app.open") == "core"


def test_user_plugin_is_off_until_enabled(isolated_env):
    _write_plugin(isolated_env, "hello", "hello.say", r"say\s+hello$")
    reg = CapabilityRegistry()
    assert "hello" in reg.disabled_plugins
    assert match_route("say hello", reg) is None

    _enable(isolated_env, "hello")
    reg = CapabilityRegistry()
    assert "hello" in [p.name for p in reg.plugins]
    assert match_route("say hello", reg) == ("hello.say", {})
    assert Tier1Router(reg).route("say hello")["message"] == "hello from plugin"


def test_broken_plugin_is_skipped_and_core_still_works(isolated_env):
    _write_plugin(isolated_env, "broken", "broken.cmd", r"broken$",
                  run_src="def nope(params):\n    return {}\n")  # no run()
    _enable(isolated_env, "broken")
    reg = CapabilityRegistry()
    assert "broken" not in [p.name for p in reg.plugins]
    assert any(e["plugin"] == "broken" and "run" in e["error"] for e in reg.plugin_errors)
    with pytest.raises(KeyError):
        reg.get_manifest("broken.cmd")
    assert match_route("open firefox", reg)[0] == "app.open"


def test_bad_regex_and_wrong_api_version_are_skipped(isolated_env):
    _write_plugin(isolated_env, "badregex", "bad.regex", r"(unclosed")
    _write_plugin(isolated_env, "future", "future.cmd", r"future$", api_version=99)
    _enable(isolated_env, "badregex", "future")
    reg = CapabilityRegistry()
    errors = {e["plugin"]: e["error"] for e in reg.plugin_errors}
    assert set(errors) == {"badregex", "future"}
    assert "API 99" in errors["future"]


def test_plugin_cannot_replace_a_core_tool(isolated_env):
    _write_plugin(isolated_env, "hijack", "app.open", r"hijack$")
    _enable(isolated_env, "hijack")
    reg = CapabilityRegistry()
    assert any(e["plugin"] == "hijack" and "already provided" in e["error"] for e in reg.plugin_errors)
    assert reg.origin("app.open") == "core"
    assert match_route("hijack", reg) is None  # all of a plugin's tools, or none


# ---------------------------------------------------------------------------
# Manifest phrases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, tool", [
    ("what's on port 5001", "dev.ports"),
    ("what's on friday", "calendar.query"),
    ("who is using port 3000", "dev.ports"),
    ("list ports", "dev.ports"),
    ("free port 3000", "dev.free_port"),
    ("doctor", "dev.doctor"),
    ("check my dev setup", "dev.doctor"),
    ("clipboard", "clipboard.history"),
    ("what did i copy", "clipboard.history"),
])
def test_phrase_priority(registry, text, tool):
    assert match_route(text, registry)[0] == tool


def test_parse_hooks_shape_params(registry):
    assert match_route("what's on port 5001", registry) == ("dev.ports", {"port": 5001})
    assert match_route("doctor for ~/code/app", registry) == ("dev.doctor", {"path": "~/code/app"})


def test_every_manifest_pattern_has_a_valid_priority(registry):
    for tool in registry._manifests:
        for p in registry.get_manifest(tool).get("patterns", []):
            assert isinstance(p.get("priority"), int), tool


# ---------------------------------------------------------------------------
# dev.doctor
# ---------------------------------------------------------------------------

def test_doctor_reports_project_problems_without_values(isolated_env, registry):
    proj = isolated_env / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    (proj / "package.json").write_text("{}")
    (proj / ".env.example").write_text("API_KEY=\nDB_URL=\nexport SECRET=\n")
    (proj / ".env").write_text("API_KEY=super-secret-value\n")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)

    result = registry.get_handler("dev.doctor").run({"path": str(proj)})
    assert result["status"] == "ok"
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["Python env (proj)"]["status"] == "warn"
    assert by_name["Node packages (proj)"]["status"] == "warn"
    assert by_name[".env (proj)"]["detail"] == "missing keys: DB_URL, SECRET"
    assert "Git (proj)" in by_name
    assert "super-secret-value" not in json.dumps(result)


def test_doctor_rejects_missing_folder(registry, isolated_env):
    result = registry.get_handler("dev.doctor").run({"path": str(isolated_env / "nope")})
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# dev.ports / dev.free_port
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def listener():
    """A throwaway child process listening on a free port."""
    port = _free_port()
    code = ("import socket,time\ns=socket.socket()\n"
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"s.bind(('127.0.0.1',{port}))\ns.listen()\ntime.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        import psutil
        for _ in range(50):
            if any(c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN
                   for c in psutil.net_connections(kind="tcp")):
                break
            time.sleep(0.1)
        yield proc, port
    finally:
        proc.kill()
        proc.wait()


def test_ports_finds_listener(registry, listener):
    proc, port = listener
    result = registry.get_handler("dev.ports").run({"port": port})
    assert result["status"] == "ok"
    pids = [r["pid"] for r in result["ports"]]
    if pids == [None]:
        pytest.skip("this OS hides process owners without elevated rights")
    assert proc.pid in pids


def test_free_port_denied_leaves_process_running(registry, listener, consent):
    proc, port = listener
    consent.answer = False
    result = Tier1Router(registry).route(f"free port {port}")
    assert result["status"] == "denied"
    assert len(consent.calls) == 1
    assert proc.poll() is None


def test_free_port_approved_stops_process(registry, listener, consent):
    proc, port = listener
    if registry.get_handler("dev.ports").listening(port)[0]["pid"] is None:
        pytest.skip("this OS hides process owners without elevated rights")
    consent.answer = True
    result = Tier1Router(registry).route(f"free port {port}")
    assert result["status"] == "ok", result
    proc.wait(timeout=5)
    assert proc.returncode is not None


def test_free_port_refuses_pikina_itself(registry, monkeypatch):
    handler = registry.get_handler("dev.free_port")
    monkeypatch.setattr(handler._ports, "listening",
                        lambda port: [{"port": port, "pid": os.getpid(), "name": "python"}])
    result = handler.run({"port": 5001})
    assert result["status"] == "error"
    assert "Pikina itself" in result["reason"]


# ---------------------------------------------------------------------------
# clipboard.history
# ---------------------------------------------------------------------------

def test_clipboard_history_newest_first(registry):
    from pikina.eventbus.bus import EventBus
    from pikina.mcm.provenance import UNTRUSTED
    bus = EventBus()
    for text in ("first", "second", "third"):
        bus.publish(topic="clipboard.copied", payload={"content": text}, provenance=UNTRUSTED)
        time.sleep(0.002)
    result = Tier1Router(registry).route("clipboard")
    assert result["status"] == "ok"
    assert [i["text"] for i in result["items"]] == ["third", "second", "first"]
    assert result["message"].splitlines()[0].endswith("third")


def test_clipboard_history_empty(registry):
    result = registry.get_handler("clipboard.history").run({})
    assert result["items"] == []
    assert "Nothing copied" in result["message"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def test_api_plugins_lists_devtools():
    from pikina.server import create_app
    app = create_app(start_daemons=False, enable_tier2=False, api_token="t")
    client = app.test_client()
    assert client.get("/api/plugins").status_code == 401
    body = client.get("/api/plugins", headers={"X-Pikina-Token": "t"}).get_json()
    devtools = next(p for p in body["loaded"] if p["name"] == "devtools")
    assert set(devtools["tools"]) == {"dev.doctor", "dev.ports", "dev.free_port"}
    assert body["errors"] == []
