"""
Tier 1 routing, the shared consent gate, the kill switch, and alias chains
executed as transactions with real rollback.
"""
import pytest

from core.mcm import killswitch
from core.mcm.orchestrator import Orchestrator
from core.router.tier1_win32 import Tier1Router
from core.todo.todo_store import ToDoStore
from core.calendar.calendar_store import CalendarStore
from core.preferences.profile_manager import PreferencesProfileManager


@pytest.fixture
def router(registry):
    r = Tier1Router(registry=registry)
    r._alias_resolver._aliases = []
    r._alias_resolver._save = lambda: None  # never write the real aliases.json
    return r


def _alias(router, steps, trigger="chain"):
    router._alias_resolver._aliases = [{"trigger": trigger, "steps": steps}]
    return router.route(trigger)


@pytest.mark.parametrize("text,tool,params", [
    ("mark task call mom as done", "todo.complete", {"text": "call mom"}),
    ("mark buy milk as completed", "todo.complete", {"text": "buy milk"}),
    ("list all pending tasks", "todo.list", {"status": "pending"}),
    ("show my open todos", "todo.list", {"status": "pending"}),
    ("what are my tasks", "todo.list", {"status": "pending"}),
    ("list all tasks", "todo.list", {"status": "all"}),
    ("open vs code", "app.open", {"path": "vs code"}),
    ("lock screen", "system.lock_screen", {}),
])
def test_phrases_route_deterministically(router, monkeypatch, text, tool, params):
    seen = []
    monkeypatch.setattr(router, "_execute", lambda t, p, raw: seen.append((t, p)) or {"status": "ok"})
    router.route(text)
    assert seen == [(tool, params)]


def test_add_and_complete_task_end_to_end(router):
    assert router.route("add todo call mom")["status"] == "ok"
    assert router.route("mark task call mom as done")["status"] == "ok"
    assert ToDoStore().list_todos(status="pending") == []


def test_unmatched_text_is_no_match(router):
    assert router.route("tell me a joke")["status"] == "no_match"


def test_high_risk_tool_asks_and_respects_denial(router, registry, monkeypatch, consent):
    real_get = type(registry).get_manifest
    def get(tool):
        m = real_get(registry, tool)
        return dict(m, permission_level=4) if tool == "todo.remove" else m
    monkeypatch.setattr(registry, "get_manifest", get)
    ToDoStore().add("keep me")
    consent.answer = False
    assert router.route("remove todo keep me")["status"] == "denied"
    assert len(consent.calls) == 1
    assert [t["text"] for t in ToDoStore().list_todos(status="all")] == ["keep me"]


def test_kill_switch_blocks_everything(registry):
    mcm = Orchestrator(router=Tier1Router(registry=registry))
    killswitch.engage()
    assert mcm.receive("add todo should not exist")["status"] == "denied"
    assert ToDoStore().list_todos(status="all") == []


def test_alias_success_runs_all_steps(router):
    res = _alias(router, [{"tool": "todo.add", "params": {"text": "one"}},
                          {"tool": "utility.calculate", "params": {"expr": "2+2"}}])
    assert res["status"] == "ok" and res["steps_completed"] == 2


def test_alias_failure_rolls_back_todo_event_and_prefs(router):
    PreferencesProfileManager.update_field("formality", "neutral")
    res = _alias(router, [
        {"tool": "todo.add", "params": {"text": "rollback me"}},
        {"tool": "calendar.add_event", "params": {"title": "rb event", "date": "2026-12-01"}},
        {"tool": "prefs.update", "params": {"field": "formality", "value": "formal"}},
        {"tool": "utility.calculate", "params": {"expr": "1+1"}},
        {"tool": "todo.complete", "params": {"text": "does-not-exist"}},
    ])
    assert res["status"] == "error"
    assert res["rollback"] == "rolled_back"
    assert "rolled back" in res["reason"]
    assert ToDoStore().list_todos(status="all") == []
    assert CalendarStore().query_range("2026-11-01", "2026-12-31") == []
    assert PreferencesProfileManager.load()["formality"] == "neutral"


def test_alias_denied_step_triggers_rollback(router, registry, monkeypatch, consent):
    real_get = type(registry).get_manifest
    def get(tool):
        m = real_get(registry, tool)
        return dict(m, permission_level=4) if tool == "system.lock_screen" else m
    monkeypatch.setattr(registry, "get_manifest", get)
    consent.answer = False
    res = _alias(router, [{"tool": "todo.add", "params": {"text": "temp"}},
                          {"tool": "system.lock_screen", "params": {}}])
    assert res["status"] == "denied" and res["rollback"] == "rolled_back"
    assert ToDoStore().list_todos(status="all") == []


def test_irreversible_step_makes_rollback_partial(router, monkeypatch, registry):
    handler = registry.get_handler("app.open")
    monkeypatch.setattr(handler, "run", lambda params: {"status": "ok", "launched": params["path"]})
    res = _alias(router, [{"tool": "todo.add", "params": {"text": "stays"}},
                          {"tool": "app.open", "params": {"path": "editor"}},
                          {"tool": "todo.complete", "params": {"text": "missing"}}])
    assert res["rollback"] == "partially_rolled_back"
    assert "could NOT be rolled back" in res["reason"]


def test_typed_alias_definition_and_use(registry):
    router = Tier1Router(registry=registry)
    res = router.route("add alias morning -> add todo stretch ; calculate 2 * 3")
    assert res["status"] == "ok"
    assert res["steps"] == [{"tool": "todo.add", "params": {"text": "stretch", "bucket": "backlog", "due_date": None}},
                            {"tool": "utility.calculate", "params": {"expr": "2 * 3"}}]
    # The router sees the alias saved through the capability without a restart
    assert router.route("morning")["status"] == "ok"
    assert [t["text"] for t in ToDoStore().list_todos(status="all")] == ["stretch"]


def test_typed_alias_rejects_non_command_steps(registry):
    res = Tier1Router(registry=registry).route("add alias bad -> tell me a joke")
    assert res["status"] == "error" and "not a Tier 1 command" in res["reason"]
