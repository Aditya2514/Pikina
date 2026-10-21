"""
Phase 1 — the activity log: privacy-safe action keys, the executor hook,
sources and outcomes, aliases and rollbacks, pause, sessions, retention,
backfill from the replay log, the HTTP API and the window tracker's cleaning.
"""
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from pikina import paths, settings
from pikina.activity import recorder
from pikina.activity.backfill import backfill
from pikina.activity.keys import RULES, action_key, alias_hash, file_type
from pikina.activity.store import ActivityStore, utc_iso
from pikina.activity.tracker import WindowTracker
from pikina.eventbus.replay import ReplayStore
from pikina.mcm import killswitch
from pikina.mcm.orchestrator import Orchestrator
from pikina.osbridge.apps import canonical_app
from pikina.router.tier1 import Tier1Router


@pytest.fixture
def store():
    return ActivityStore()


@pytest.fixture
def router(registry):
    r = Tier1Router(registry=registry)
    r._alias_resolver._aliases = []
    r._alias_resolver._save = lambda: None
    return r


@pytest.fixture
def fake_launch(registry, monkeypatch):
    handler = registry.get_handler("app.open")
    monkeypatch.setattr(handler, "run", lambda params: {"status": "ok", "launched": "/usr/bin/code"})


def rows(store):
    return list(reversed(store.recent(limit=500)))


def db_bytes() -> bytes:
    out = b""
    for f in paths.data_dir().glob(paths.ACTIVITY_DB + "*"):
        out += f.read_bytes()
    return out


# ---------------------------------------------------------------------------
# Action keys
# ---------------------------------------------------------------------------

def test_every_core_and_builtin_tool_has_an_explicit_key_rule(registry):
    missing = {t["tool"] for t in registry.list_tools()} - RULES
    assert not missing, f"decide what the activity log keeps for: {sorted(missing)}"


@pytest.mark.parametrize("tool, params, result, key", [
    ("app.open", {"path": "vs code"}, None, "app.open:code"),
    ("app.open", {"path": "Visual Studio Code"}, None, "app.open:code"),
    ("app.open", {"path": "code-oss"}, None, "app.open:code"),
    ("app.open", {"path": "zz-not-installed"}, {"launched": "/usr/bin/flatpak"}, "app.open:?"),
    ("app.open", {"path": "/home/aditya/tax_2024.sh"}, None, "app.open:?"),
    ("app.open", {"path": "resume.pdf"}, {"status": "error"}, "app.open:?"),
    ("fs.find_file", {"name": "resume.PDF"}, None, "fs.find_file:*.pdf"),
    ("fs.find_file", {"name": "*.tar.gz"}, None, "fs.find_file:*.tar.gz"),
    ("fs.find_file", {"name": "my secret plan"}, None, "fs.find_file:*"),
    ("fs.find_file", {"name": "john.smith"}, None, "fs.find_file:*.?"),
    ("todo.add", {"text": "call the bank about my loan"}, None, "todo.add"),
    ("utility.calculate", {"expr": "52000 * 12"}, None, "utility.calculate"),
    ("prefs.update", {"field": "preferred_name", "value": "Aditya"}, None, "prefs.update:preferred_name"),
    ("prefs.update", {"field": "Aditya", "value": "x"}, None, "prefs.update:?"),
    ("utility.stopwatch", {"action": "start"}, None, "utility.stopwatch:start"),
    ("dev.free_port", {"port": 3000}, None, "dev.free_port:3000"),
    ("dev.ports", {"port": "port 51234"}, None, "dev.ports:ephemeral"),
    ("dev.ports", {}, None, "dev.ports"),
    ("dev.doctor", {"path": "~/clients/acme"}, None, "dev.doctor:project"),
    ("calendar.query", {"start_date": "2026-10-20", "end_date": "2026-10-27"}, None, "calendar.query:range"),
    ("calendar.query", {"date": "tomorrow"}, None, "calendar.query:tomorrow"),
    ("alias.run", {"trigger": "  Morning "}, None, "alias.run:" + alias_hash("morning")),
    ("myplugin.do", {"secret": "hunter2"}, None, "myplugin.do"),
])
def test_action_keys(tool, params, result, key):
    assert action_key(tool, params, result) == key


def test_installed_programs_keep_their_name(monkeypatch):
    from pikina.activity import keys
    monkeypatch.setattr(keys.shutil, "which", lambda n: "/usr/bin/spotify" if n == "spotify" else None)
    assert action_key("app.open", {"path": "spotify"}, {"launched": "/usr/bin/spotify"}) == "app.open:spotify"
    assert action_key("app.open", {"path": "Spotify"}, {"status": "error"}) == "app.open:spotify"
    assert action_key("app.open", {"path": "C:\\Apps\\Spotify.exe"}) == "app.open:spotify"
    assert action_key("app.open", {"path": "hunter2"}, {"launched": "hunter2"}) == "app.open:?"


def test_keys_never_contain_personal_params():
    personal = ["call the bank", "hunter2", "Aditya", "/home/aditya", "tax 2024", "john", "acme"]
    for tool in sorted(RULES):
        for value in personal:
            params = {k: value for k in ("path", "name", "text", "expr", "query", "title", "value",
                                         "trigger", "field", "action", "date", "port", "filepath")}
            key = action_key(tool, params, {"launched": value})
            assert value.lower() not in key.lower(), (tool, key)


def test_file_type_edge_cases():
    assert file_type("~/Documents/*.PDF") == "*.pdf"
    assert file_type("photo.jpeg") == "*.jpg"
    assert file_type(".env") == ".env"
    assert file_type(".bashrc") == ".*"
    assert file_type("*.env") == "*.env"
    assert file_type("notes") == "*"


def test_canonical_app_names():
    assert canonical_app("Code.exe") == "code"
    assert canonical_app("org.keepassxc.KeePassXC") == "keepassxc"
    assert canonical_app("com.bitwarden.desktop") == "bitwarden"
    assert canonical_app("kitty") == "terminal"
    assert canonical_app("C:\\Program Files\\Mozilla Firefox\\firefox.exe") == "firefox"


def test_open_vs_code_finds_arch_code_oss(monkeypatch):
    from pikina.osbridge import apps
    monkeypatch.setattr(apps, "which_first", lambda names: "/usr/bin/code-oss" if "code-oss" in names else None)
    assert apps.resolve_linux_command("vs code") == ["/usr/bin/code-oss"]


# ---------------------------------------------------------------------------
# The executor hook
# ---------------------------------------------------------------------------

def test_typed_command_is_recorded_without_its_text(router, store, fake_launch):
    router.route("add todo call the bank about my loan")
    router.route("open vs code")
    got = rows(store)
    assert [(r["action"], r["source"], r["outcome"], r["tier"]) for r in got] == [
        ("todo.add", "typed", "ok", "1"), ("app.open:code", "typed", "ok", "1")]
    assert all(r["origin_event"] for r in got)
    assert 0 <= got[0]["hour"] <= 23 and 0 <= got[0]["weekday"] <= 6
    assert b"bank" not in db_bytes() and b"loan" not in db_bytes()


def test_denied_and_error_outcomes(router, registry, store, consent, monkeypatch):
    consent.answer = False
    router.route("forget my activity")          # level 4, declined; not logged (activity_log: false)
    real = type(registry).get_manifest
    monkeypatch.setattr(registry, "get_manifest",
                        lambda t: dict(real(registry, t), permission_level=4) if t == "system.lock_screen"
                        else real(registry, t))
    router.route("lock screen")                 # declined -> denied
    router.route("mark task nothing-like-this as done")  # handler says ambiguous/error
    got = [(r["action"], r["outcome"]) for r in rows(store)]
    assert got == [("system.lock_screen", "denied"), ("todo.complete", "error")]


def test_kill_switch_and_unknown_tools_are_not_recorded(registry, store):
    from pikina.registry.executor import execute_action
    execute_action(registry, "no.such.tool", {})
    killswitch.engage()
    execute_action(registry, "todo.list", {})
    killswitch.release()
    assert rows(store) == []


def test_pause_and_logging_off_record_nothing(router, store):
    settings.update({"activity_paused": True})
    router.route("list todos")
    settings.update({"activity_paused": False, "activity_logging": False})
    router.route("list todos")
    assert rows(store) == []
    settings.update({"activity_logging": True})
    router.route("list todos")
    assert len(rows(store)) == 1


def test_pause_and_resume_by_command_are_not_logged_themselves(router, store):
    assert router.route("pause activity")["paused"] is True
    router.route("list todos")
    assert router.route("resume activity")["paused"] is False
    router.route("list todos")
    assert [r["action"] for r in rows(store)] == ["todo.list"]


def test_forget_asks_first_then_wipes(router, store, consent):
    router.route("list todos")
    consent.answer = False
    assert router.route("forget my activity")["status"] == "denied"
    assert len(rows(store)) == 1
    consent.answer = True
    res = router.route("forget my activity")
    assert res["status"] == "ok" and res["deleted"]["actions"] == 1
    assert rows(store) == []
    assert len(consent.calls) == 2


def test_summary_command(router, store):
    router.route("list todos")
    router.route("add todo stretch")
    res = router.route("activity")
    assert res["status"] == "ok"
    assert "Today: 2 actions in 1 session" in res["message"]
    assert "todo.add" in res["message"]
    assert len(rows(store)) == 2  # looking at the log isn't logged


# ---------------------------------------------------------------------------
# Aliases and rollbacks
# ---------------------------------------------------------------------------

def test_alias_run_is_one_parent_row_plus_linked_steps(router, store):
    router._alias_resolver._aliases = [{"trigger": "Morning", "steps": [
        {"tool": "todo.list", "params": {"status": "pending"}},
        {"tool": "utility.calculate", "params": {"expr": "1+1"}}]}]
    assert router.route("morning")["status"] == "ok"
    got = rows(store)
    by_source = {}
    for r in got:
        by_source.setdefault(r["source"], []).append(r)
    assert [r["action"] for r in by_source["alias"]] == ["todo.list", "utility.calculate"]
    (parent,) = by_source["typed"]
    assert parent["action"] == "alias.run:" + alias_hash("Morning") and parent["outcome"] == "ok"
    assert b"orning" not in db_bytes()  # the trigger you wrote is not stored
    assert {r["group_id"] for r in got} == {parent["group_id"]}


def test_failed_alias_is_error_and_rollbacks_are_not_recorded(router, store):
    router._alias_resolver._aliases = [{"trigger": "chain", "steps": [
        {"tool": "todo.add", "params": {"text": "temp"}},
        {"tool": "todo.complete", "params": {"text": "does-not-exist"}}]}]
    res = router.route("chain")
    assert res["rollback"] == "rolled_back"
    actions = [(r["action"], r["source"], r["outcome"]) for r in rows(store)]
    assert ("alias.run:" + alias_hash("chain"), "typed", "error") in actions
    assert not any(a == "todo.remove" for a, _, _ in actions)  # the undo isn't your action


# ---------------------------------------------------------------------------
# Sessions and retention
# ---------------------------------------------------------------------------

def test_sessions_split_after_30_idle_minutes(store):
    t0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    for minutes in (0, 10, 39, 80):
        store.add_action(ts=t0 + timedelta(minutes=minutes), action="todo.list", source="typed", outcome="ok")
    store.add_switch(ts=t0 + timedelta(minutes=100), app="code", backend="hyprland")
    sids = [r["session_id"] for r in reversed(store.recent())]
    assert sids[0] == sids[1] == sids[2] != sids[3]
    assert store.switches()[0]["session_id"] == sids[3]  # an app switch keeps the session alive


def test_prune_deletes_rows_older_than_90_days(store):
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    store.add_action(ts=now - timedelta(days=91), action="old", source="typed", outcome="ok")
    store.add_action(ts=now - timedelta(days=89), action="kept", source="typed", outcome="ok")
    store.add_switch(ts=now - timedelta(days=100), app="code", backend="x11")
    assert store.prune(now=now) == 2
    assert [r["action"] for r in store.recent()] == ["kept"]
    assert store.switches() == []


def test_a_broken_store_never_breaks_the_command(router, monkeypatch):
    monkeypatch.setattr(ActivityStore, "add_action", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert router.route("list todos")["status"] == "ok"


# ---------------------------------------------------------------------------
# Backfill from the replay log
# ---------------------------------------------------------------------------

def _event(replay, topic, payload, ts):
    event_id = str(uuid.uuid4())
    replay.append({"id": event_id, "topic": topic, "timestamp": utc_iso(ts),
                   "provenance": "TRUSTED_COMMAND", "payload": payload})
    return event_id


def test_backfill_rebuilds_history_safely(store, replay):
    now = datetime.now(timezone.utc)
    t = now - timedelta(days=2)
    s = timedelta(seconds=1)
    _event(replay, "router.executing", {"tool": "app.open", "params": {"path": "vs code"}, "raw": "open vs code", "tier": 1}, t)
    _event(replay, "router.result", {"tool": "app.open", "result": {"status": "ok", "launched": "/usr/bin/code"}, "tier": 1}, t + s)
    _event(replay, "router.executing", {"tool": "fs.find_file", "params": {"name": "taxes.pdf"}, "raw": "find file taxes.pdf", "tier": 2}, t + 2 * s)
    _event(replay, "router.result", {"tool": "fs.find_file", "result": {"status": "ok", "results": ["/home/a/taxes.pdf"]}, "tier": 2}, t + 3 * s)
    # an alias with two steps, the second fails, the first is rolled back
    _event(replay, "alias.started", {"trigger": "chain", "steps": 2, "raw": "chain"}, t + 10 * s)
    _event(replay, "router.executing", {"tool": "todo.add", "params": {"text": "x"}, "raw": "chain [alias step 1/2]", "tier": 1}, t + 11 * s)
    _event(replay, "router.result", {"tool": "todo.add", "result": {"status": "ok"}, "tier": 1}, t + 12 * s)
    _event(replay, "router.executing", {"tool": "todo.complete", "params": {"text": "y"}, "raw": "chain [alias step 2/2]", "tier": 1}, t + 13 * s)
    _event(replay, "router.result", {"tool": "todo.complete", "result": {"status": "error"}, "tier": 1}, t + 14 * s)
    _event(replay, "router.executing", {"tool": "todo.remove", "params": {"text": "x"}, "raw": "rollback of todo.add (alias 'chain')", "tier": 1}, t + 15 * s)
    _event(replay, "router.result", {"tool": "todo.remove", "result": {"status": "ok"}, "tier": 1}, t + 16 * s)
    _event(replay, "router.denied", {"tool": "system.lock_screen", "params": {}, "raw": "lock screen", "tier": 1}, t + 20 * s)
    _event(replay, "validation.rejected", {"proposed_action": {"tool": "dev.free_port", "params": {"port": 3000}}, "outcome": "denied"}, t + 21 * s)
    _event(replay, "router.executing", {"tool": "calendar.update_event", "params": {"title": "Dentist"}, "raw": "[api] calendar.update_event", "tier": "api"}, t + 22 * s)
    _event(replay, "router.result", {"tool": "calendar.update_event", "result": {"status": "ok"}, "tier": "api"}, t + 23 * s)
    _event(replay, "router.executing", {"tool": "todo.list", "params": {}, "raw": "list todos", "tier": 1}, t + 24 * s)  # no result
    _event(replay, "router.executing", {"tool": "todo.list", "params": {}, "raw": "list todos", "tier": 1}, now - timedelta(days=120))
    _event(replay, "router.result", {"tool": "todo.list", "result": {"status": "ok"}, "tier": 1}, now - timedelta(days=120))

    done = backfill(store=store, now=now)
    assert done["status"] == "ok"
    got = [(r["action"], r["source"], r["outcome"]) for r in rows(store)]
    assert got == [
        ("app.open:code", "backfill", "ok"),
        ("fs.find_file:*.pdf", "backfill", "ok"),
        ("alias.run:" + alias_hash("chain"), "backfill", "error"),
        ("todo.add", "alias", "ok"),
        ("todo.complete", "alias", "error"),
        ("system.lock_screen", "backfill", "denied"),
        ("dev.free_port:3000", "backfill", "denied"),
        ("calendar.update_event", "ui", "ok"),
    ]
    assert len({r["session_id"] for r in rows(store)}) == 1
    data = db_bytes()
    assert b"taxes" not in data and b"Dentist" not in data and b"/home/a" not in data

    assert backfill(store=store, now=now)["status"] == "skipped"
    assert backfill(store=store, now=now, force=True)["added"] == 0  # idempotent


def test_backfill_does_not_duplicate_live_rows(router, store):
    router.route("list todos")
    assert backfill(store=store, force=True)["added"] == 0
    assert len(rows(store)) == 1


def test_backfill_respects_pause(store):
    settings.update({"activity_paused": True})
    assert backfill(store=store)["status"] == "skipped"
    assert store.meta("backfilled_at") is None


# ---------------------------------------------------------------------------
# HTTP API, origins and settings
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    from pikina.server import create_app
    return create_app(start_daemons=False, enable_tier2=False, api_token="t")


@pytest.fixture
def client(app):
    return app.test_client()


H = {"X-Pikina-Token": "t"}


def test_origin_labels_the_surface_but_never_changes_trust(client, store):
    assert client.post("/api/command", json={"text": "list todos", "origin": "quick_panel"}, headers=H).get_json()["status"] == "ok"
    assert client.post("/api/command", json={"text": "list todos", "origin": "suggestion"}, headers=H).get_json()["status"] == "ok"
    assert client.post("/api/command", json={"text": "list todos"}, headers=H).get_json()["status"] == "ok"
    rejected = client.post("/api/command", json={"text": "list todos", "source": "quick_panel"}, headers=H).get_json()
    assert rejected["status"] == "rejected"
    assert [r["source"] for r in rows(store)] == ["quick_panel", "typed", "typed"]


def test_dashboard_calendar_clicks_are_ui(client, store):
    client.post("/api/capability", json={"tool": "calendar.add_event",
                                         "params": {"title": "Secret meeting", "date": "2026-12-01"}}, headers=H)
    assert [(r["action"], r["source"], r["tier"]) for r in rows(store)] == [("calendar.add_event", "ui", "api")]


def test_status_and_activity_settings_endpoint(client, app):
    act = client.get("/api/status", headers=H).get_json()["activity"]
    assert act == {"logging": True, "paused": False, "window_tracking": False,
                   "tracker": {"running": False, "backend": None, "reason": None}}
    res = client.post("/api/activity/settings", json={"paused": True}, headers=H)
    assert res.status_code == 200 and res.get_json()["activity"]["paused"] is True
    assert client.post("/api/activity/settings", json={"paused": "yes"}, headers=H).status_code == 400
    res = client.post("/api/activity/settings", json={"window_tracking": True}, headers=H).get_json()
    assert res["activity"]["window_tracking"] is True
    assert "PIKINA_NO_DAEMONS" in res["activity"]["tracker"]["reason"]   # this test app runs no daemons
    killswitch.engage()
    assert client.post("/api/activity/settings", json={"window_tracking": True}, headers=H).status_code == 409
    killswitch.release()


def test_generic_settings_cannot_flip_activity_switches(client):
    client.post("/api/settings", json={"window_tracking": True, "activity_paused": True, "background3d": False}, headers=H)
    s = settings.read()
    assert s == {"background3d": False}


def test_corrupt_settings_are_never_overwritten(client):
    f = paths.data_file(paths.SETTINGS)
    f.write_text('{"plugins_enabled": ["mine"], oops', encoding="utf-8")
    assert client.post("/api/settings", json={"background3d": True}, headers=H).status_code == 409
    assert "oops" in f.read_text(encoding="utf-8")


def test_concurrent_settings_updates_keep_every_key():
    threads = [threading.Thread(target=settings.update, args=({f"k{i}": i},)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(settings.read()[f"k{i}"] == i for i in range(20))


def test_activity_listing_and_export(client, store):
    client.post("/api/command", json={"text": "list todos"}, headers=H)
    data = client.get("/api/activity?limit=5", headers=H).get_json()
    assert data["actions"][0]["action"] == "todo.list"
    assert data["stats"]["actions"] == 1
    export = client.get("/api/activity/export", headers=H).get_json()
    assert len(export["activity"]) == 1 and export["app_switches"] == []


# ---------------------------------------------------------------------------
# Window tracker cleaning (with a fake focus source and a fake clock)
# ---------------------------------------------------------------------------

class FakeSource(threading.Thread):
    def __init__(self, on_focus):
        super().__init__(daemon=True)
        self.on_focus = on_focus
        self._stop_event = threading.Event()

    def run(self):
        self._stop_event.wait()

    def stop(self):
        self._stop_event.set()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def tracker():
    clock = Clock()
    holder = {}

    def make_source(backend, on_focus):
        holder["src"] = FakeSource(on_focus)
        return holder["src"]

    tr = WindowTracker(detect=lambda: ("hyprland", None), make_source=make_source, clock=clock)
    assert tr.start()
    tr.clock, tr.src = clock, holder["src"]
    yield tr
    tr.stop()


def _focus(tr, app, at, private=False, pid=None):
    tr.clock.t = at
    tr.src.on_focus(app, private, pid)


def _settle(tr, store, n, timeout=3.0):
    tr.clock.t += 10  # let the pending focus outlast MIN_DWELL
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = [r["app"] for r in reversed(store.switches())]
        if len(got) >= n and tr._queue.empty():
            time.sleep(0.3)
            return [r["app"] for r in reversed(store.switches())]
        time.sleep(0.05)
    return [r["app"] for r in reversed(store.switches())]


def test_tracker_writes_clean_app_switches(tracker, store):
    _focus(tracker, "Code", 1000)
    _focus(tracker, "firefox", 1005)
    _focus(tracker, "kitty", 1005.3)          # flicker under 1 s: dropped
    _focus(tracker, "firefox", 1005.6)        # back to firefox
    _focus(tracker, "pikina-desktop", 1010)   # Pikina's own window: ignored
    _focus(tracker, "org.keepassxc.KeePassXC", 1020)
    _focus(tracker, "firefox", 1030, private=True)
    _focus(tracker, None, 1040)
    _focus(tracker, "Code", 1050)
    got = _settle(tracker, store, 6)
    assert got == ["code", "firefox", "excluded", "none", "code"]
    assert all(r["backend"] == "hyprland" for r in store.switches())


def test_commands_get_the_app_you_were_in(tracker, store, router):
    _focus(tracker, "Code", 1000)
    _focus(tracker, "pikina-desktop", 1003)   # you open the quick panel to type
    _settle(tracker, store, 1)
    assert tracker.current_app() == "code"
    router.route("list todos")
    assert rows(store)[-1]["active_app"] == "code"


def test_excluded_apps_are_never_the_active_app(tracker, store, router):
    settings.update({"activity_exclude_apps": ["MyBank"]})
    _focus(tracker, "mybank", 1000)
    _settle(tracker, store, 1)
    assert tracker.current_app() is None
    router.route("list todos")
    assert rows(store)[-1]["active_app"] is None
    assert [r["app"] for r in store.switches()] == ["excluded"]


def test_paused_tracker_writes_nothing(tracker, store):
    settings.update({"activity_paused": True})
    _focus(tracker, "Code", 1000)
    _focus(tracker, "firefox", 1005)
    time.sleep(0.5)
    assert store.switches() == []
    assert tracker.current_app() is None


def test_tracker_without_a_backend_reports_why():
    tr = WindowTracker(detect=lambda: (None, "GNOME on Wayland isn't supported yet"), make_source=None)
    assert tr.start() is False
    assert tr.status() == {"running": False, "backend": None, "reason": "GNOME on Wayland isn't supported yet"}
    tr.stop()


def test_tracker_stop_is_prompt_and_unhooks_active_app(tracker):
    t0 = time.monotonic()
    tracker.stop()
    assert time.monotonic() - t0 < 2
    assert recorder.current_active_app() is None
