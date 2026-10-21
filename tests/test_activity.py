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


def test_backfill_never_imports_paused_or_off_periods(router, store):
    router.route("pause activity")
    router.route("find file divorce_papers.pdf")
    router.route("resume activity")
    settings.update({"activity_logging": False})
    router.route("list todos")
    settings.update({"activity_logging": True})
    assert backfill(store=store, force=True)["added"] == 0
    assert rows(store) == []


def test_logging_off_before_first_start_is_not_backfilled_later(router):
    settings.update({"activity_logging": False})
    router.route("list todos")                     # Phase 1 is running, logging is off
    ActivityStore()                                # the backend starts
    settings.update({"activity_logging": True})
    assert backfill(force=True)["added"] == 0


def test_forget_is_final_even_for_a_forced_backfill(router, store, consent, replay):
    t = datetime.now(timezone.utc) - timedelta(days=3)
    _event(replay, "router.executing", {"tool": "todo.list", "params": {}, "raw": "list todos", "tier": 1}, t)
    _event(replay, "router.result", {"tool": "todo.list", "result": {"status": "ok"}, "tier": 1}, t)
    assert backfill(store=store)["added"] == 1
    consent.answer = True
    router.route("forget my activity")
    assert backfill(store=store, force=True)["added"] == 0
    assert rows(store) == []


def test_forget_during_backfill_wins(store, replay, monkeypatch):
    from pikina.activity import backfill as bf
    t = datetime.now(timezone.utc) - timedelta(days=3)
    _event(replay, "router.executing", {"tool": "todo.list", "params": {}, "raw": "list todos", "tier": 1}, t)
    _event(replay, "router.result", {"tool": "todo.list", "result": {"status": "ok"}, "tier": 1}, t)
    real = bf._rows_from_replay

    def read_then_forget(*a, **k):
        out = real(*a, **k)
        store.forget()              # the user says "forget my activity" mid-import
        return out
    monkeypatch.setattr(bf, "_rows_from_replay", read_then_forget)
    assert backfill(store=store)["status"] == "skipped"
    assert rows(store) == []


def test_backfill_pairs_by_exec_id_and_drops_orphans(store, replay):
    t = datetime.now(timezone.utc) - timedelta(days=1)
    s = timedelta(seconds=1)
    _event(replay, "router.executing", {"tool": "app.open", "params": {"path": "firefox"}, "raw": "open firefox", "tier": 1}, t)
    # (no result for that one: the app was closed mid-command)
    _event(replay, "router.executing", {"tool": "app.open", "params": {"path": "vs code"}, "raw": "open vs code", "tier": 1}, t + s)
    _event(replay, "router.result", {"tool": "app.open", "result": {"status": "error"}, "tier": 1}, t + 2 * s)
    e3 = _event(replay, "router.executing", {"tool": "app.open", "params": {"path": "calc"}, "raw": "open calc", "tier": 1}, t + 3 * s)
    _event(replay, "router.result", {"tool": "app.open", "result": {"status": "ok"}, "tier": 1, "exec_id": e3}, t + 4 * s)
    backfill(store=store)
    assert [(r["action"], r["outcome"]) for r in rows(store)] == [("app.open:code", "error"), ("app.open:calculator", "ok")]


def test_live_results_carry_their_execution_id(router, replay):
    router.route("list todos")
    executing = replay.query(since_minutes=5, topic="router.executing")[-1]
    result = replay.query(since_minutes=5, topic="router.result")[-1]
    assert result["payload"]["exec_id"] == executing["id"]


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
    assert act == {"logging": True, "paused": False, "window_tracking": False, "settings_error": None,
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


# ---------------------------------------------------------------------------
# Review fixes: fail-closed settings, redaction, exclusions, lifecycle
# ---------------------------------------------------------------------------

def test_alias_hash_is_keyed_per_install(tmp_path, monkeypatch):
    import hashlib
    from pikina.activity import keys
    here = alias_hash("text sarah")
    assert here == alias_hash("  Text   Sarah ") and len(here) == 12
    assert here != hashlib.sha256(b"pikina-alias:text sarah").hexdigest()[:12]
    monkeypatch.setenv("PIKINA_DATA_DIR", str(tmp_path / "other-install"))
    assert alias_hash("text sarah") != here
    assert "alias_key" not in json.dumps(ActivityStore().export())


def test_corrupt_settings_fail_closed(router, store, tracker):
    settings.update({"activity_paused": True, "activity_exclude_apps": ["MyBank"]})
    paths.data_file(paths.SETTINGS).write_text('{"activity_paused": true, oops', encoding="utf-8")
    router.route("list todos")
    _focus(tracker, "mybank", 1000)
    _focus(tracker, "firefox", 1005)
    time.sleep(0.5)
    assert rows(store) == [] and store.switches() == []
    assert recorder.flags() == {"logging": False, "paused": True, "settings_ok": False}


@pytest.mark.parametrize("value, recorded", [("false", False), (0, False), (None, False), (True, True)])
def test_only_real_booleans_turn_logging_on(router, store, value, recorded):
    settings.update({"activity_logging": value})
    router.route("list todos")
    assert bool(rows(store)) is recorded


def test_status_reports_a_broken_settings_file(client):
    paths.data_file(paths.SETTINGS).write_text("{nope", encoding="utf-8")
    act = client.get("/api/status", headers=H).get_json()["activity"]
    assert act["paused"] is True and act["logging"] is False and "settings.json" in act["settings_error"]


def test_activity_summary_result_stays_out_of_the_replay_log(router, store):
    store.add_switch(ts=datetime.now(timezone.utc), app="zzsecretapp", backend="hyprland")
    store.add_action(ts=datetime.now(timezone.utc), action="app.open:zzsecretapp", source="typed", outcome="ok")
    res = router.route("activity")
    assert "zzsecretapp" in res["message"]
    replay_bytes = b"".join(f.read_bytes() for f in paths.data_dir().glob(paths.REPLAY_DB + "*"))
    assert b"zzsecretapp" not in replay_bytes


@pytest.mark.parametrize("name", ["keepassxc", "1password", "proton pass", "tor-browser"])
def test_opening_an_excluded_app_is_not_named(name):
    assert action_key("app.open", {"path": name}) == "app.open:excluded"


def test_user_exclusions_cover_app_open_and_channel_variants(monkeypatch):
    from pikina.activity import keys, privacy
    settings.update({"activity_exclude_apps": ["MyBank", "Vivaldi"]})
    monkeypatch.setattr(keys.shutil, "which", lambda n: "/usr/bin/" + n)
    assert action_key("app.open", {"path": "mybank"}) == "app.open:excluded"
    assert privacy.is_excluded("vivaldi-stable")
    assert privacy.is_excluded("io.ente.auth")
    settings.update({"activity_exclude_apps": "MyBank"})   # a string instead of a list still works
    assert privacy.is_excluded("mybank")


def test_web_app_windows_are_recorded_as_the_browser(tracker, store):
    _focus(tracker, "chrome-chase.com__-Default", 1000)
    _focus(tracker, "brave-x.com__-Default", 1005)
    got = _settle(tracker, store, 2)
    assert got == ["chrome", "brave"]
    assert not any("chase" in r["app"] or "x.com" in r["app"] for r in store.switches())


def test_logging_off_does_not_silence_the_app_tracker(tracker, store):
    settings.update({"activity_logging": False})
    _focus(tracker, "Code", 1000)
    assert _settle(tracker, store, 1) == ["code"]
    assert tracker.current_app() == "code"


def test_pause_marks_the_boundary_and_resume_picks_up_the_current_app(tracker, store):
    _focus(tracker, "Code", 1000)
    _settle(tracker, store, 1)
    settings.update({"activity_paused": True})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(store.switches()) < 2:
        time.sleep(0.05)
    _focus(tracker, "firefox", 1100)          # while paused: not recorded
    settings.update({"activity_paused": False})
    got = _settle(tracker, store, 3)
    assert got == ["code", "paused", "firefox"]  # firefox picked up on resume, not left unknown


def test_nothing_is_written_after_stop_even_with_a_backlog(tracker, store, monkeypatch):
    gate = threading.Event()
    real = ActivityStore.add_switch

    def slow(self, **kw):
        gate.wait(2)
        return real(self, **kw)
    monkeypatch.setattr(ActivityStore, "add_switch", slow)
    _focus(tracker, "Code", 1000)
    _focus(tracker, "firefox", 1005)          # commits "code" -> blocks in slow()
    for i in range(50):
        _focus(tracker, f"app{i}", 1010 + i * 5)
    time.sleep(0.2)
    tracker.stop()
    gate.set()
    time.sleep(0.5)
    assert [r["app"] for r in store.switches()] in ([], ["code"])


class SickSource(FakeSource):
    failure = "can't reach Hyprland's event socket (retrying)"


def test_unhealthy_listener_reports_why_and_gives_no_active_app(store):
    tr = WindowTracker(detect=lambda: ("hyprland", None), make_source=lambda b, cb: SickSource(cb))
    assert tr.start()
    try:
        assert tr.current_app() is None
        assert tr.status() == {"running": False, "backend": "hyprland",
                               "reason": "can't reach Hyprland's event socket (retrying)"}
    finally:
        tr.stop()


def test_sessions_survive_the_clock_jumping_back(store):
    t0 = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    store.add_action(ts=t0 + timedelta(hours=5, minutes=30), action="a", source="typed", outcome="ok")  # RTC in local time
    store.add_action(ts=t0, action="b", source="typed", outcome="ok")
    store.add_action(ts=t0 + timedelta(minutes=45), action="c", source="typed", outcome="ok")
    sids = {r["action"]: r["session_id"] for r in store.recent()}
    assert sids["a"] != sids["b"] != sids["c"]


def test_day_is_the_local_date(store):
    ts = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    store.add_action(ts=ts, action="a", source="typed", outcome="ok")
    assert store.recent()[0]["day"] == ts.astimezone().date().isoformat()


def test_deleting_the_database_by_hand_recovers(router, store):
    router.route("list todos")
    for f in paths.data_dir().glob(paths.ACTIVITY_DB + "*"):
        f.unlink()
    router.route("list todos")
    assert len(ActivityStore().recent()) == 1


def test_bad_origin_types_fall_back_to_typed(client, store):
    for origin in (["typed"], {"a": 1}, 5):
        res = client.post("/api/command", json={"text": "list todos", "origin": origin}, headers=H)
        assert res.status_code == 200
    assert {r["source"] for r in rows(store)} == {"typed"}


def test_settings_lock_across_processes(tmp_path):
    import subprocess
    import sys
    code = ("import os,sys; os.environ['PIKINA_DATA_DIR']=sys.argv[1]\n"
            "from pikina import settings\n"
            "for i in range(30): settings.update({f'{sys.argv[2]}{i}': i})\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), p]) for p in ("a", "b", "c")]
    for proc in procs:
        assert proc.wait(60) == 0
    data = json.loads((tmp_path / paths.SETTINGS).read_text(encoding="utf-8"))
    assert all(f"{p}{i}" in data for p in "abc" for i in range(30))
    assert not list(tmp_path.glob(".settings.json.*.tmp"))


# ---------------------------------------------------------------------------
# Backend lifecycle with a live (fake) tracker
# ---------------------------------------------------------------------------

@pytest.fixture
def live_app(monkeypatch):
    from pikina.osbridge import focus
    from pikina.server import create_app
    sources = []

    def make_source(backend, on_focus, **kw):
        sources.append(FakeSource(on_focus))
        return sources[-1]
    monkeypatch.setattr(focus, "detect_backend", lambda *a, **k: ("hyprland", None))
    monkeypatch.setattr(focus, "make_source", make_source)
    app = create_app(start_daemons=False, enable_tier2=False, api_token="t")
    backend = app.extensions["pikina"]
    backend._daemons_started = True
    yield app, backend, sources
    backend.stop_daemons()


def test_tracker_toggle_kill_switch_and_restart(live_app, store):
    app, backend, sources = live_app
    c = app.test_client()
    act = c.post("/api/activity/settings", json={"window_tracking": True}, headers=H).get_json()["activity"]
    assert act["tracker"]["running"] is True and act["tracker"]["backend"] == "hyprland"
    assert recorder._active_app_provider is not None
    c.post("/api/activity/settings", json={"window_tracking": False}, headers=H)
    assert backend.tracker is None and recorder._active_app_provider is None and not sources[0].is_alive()
    c.post("/api/activity/settings", json={"window_tracking": True}, headers=H)
    c.post("/api/killswitch", headers=H)
    assert backend.tracker is None and recorder._active_app_provider is None
    assert c.post("/api/activity/settings", json={"window_tracking": True}, headers=H).status_code == 409
    killswitch.release()


def test_saved_tracking_starts_with_the_backend(live_app):
    app, backend, sources = live_app
    settings.update({"window_tracking": True})
    backend.set_window_tracking(settings.get("window_tracking") is True)
    assert backend.activity_status()["tracker"]["running"] is True


def test_turning_tracking_off_works_even_if_saving_fails(live_app, monkeypatch):
    app, backend, sources = live_app
    c = app.test_client()
    c.post("/api/activity/settings", json={"window_tracking": True}, headers=H)
    paths.data_file(paths.SETTINGS).write_text("{broken", encoding="utf-8")
    res = c.post("/api/activity/settings", json={"window_tracking": False}, headers=H)
    assert res.status_code == 409
    assert backend.tracker is None


# ---------------------------------------------------------------------------
# Tier 2 consent declines
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, text):
        self.status_code = 200
        self._text = text

    def json(self):
        return {"response": self._text}


@pytest.mark.parametrize("tool, params, level, recorded", [
    ("dev.free_port", {"port": 3000}, 3, [("dev.free_port:3000", "denied", "2")]),
    ("activity.forget", {}, 4, []),          # kept out of the log on every path
])
def test_tier2_decline_is_recorded_once(registry, store, consent, monkeypatch, tool, params, level, recorded):
    import requests
    from pikina.router.tier2_ollama import Tier2Router
    reply = json.dumps({"tool": tool, "params": params, "claimed_permission_level": level,
                        "provenance": "model_output"})
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: _Resp(reply))
    consent.answer = False
    before = datetime.now(timezone.utc)
    res = Tier2Router(registry=registry).route("please do the thing", origin="quick_panel")
    assert res["status"] == "error" and "declined" in res["validation_reason"]
    got = rows(store)
    assert [(r["action"], r["outcome"], r["tier"]) for r in got] == recorded
    if got:
        assert got[0]["source"] == "quick_panel"
        assert before <= datetime.fromisoformat(got[0]["ts"]) <= datetime.now(timezone.utc)
    assert backfill(store=store, force=True)["added"] == 0   # no duplicate from the audit log
