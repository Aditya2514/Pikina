"""
Tier 1 routing, the shared consent gate, the kill switch, and alias chains
executed as transactions with real rollback.
"""
import pytest

from pikina.mcm import killswitch
from pikina.mcm.orchestrator import Orchestrator
from pikina.router.tier1 import Tier1Router
from pikina.todo.todo_store import ToDoStore
from pikina.calendar.calendar_store import CalendarStore
from pikina.preferences.profile_manager import PreferencesProfileManager


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
    ("what's on 2026-07-20", "calendar.query", {"date": "2026-07-20"}),
    ("what is on 2026-07-20", "calendar.query", {"date": "2026-07-20"}),
])
def test_phrases_route_deterministically(router, monkeypatch, text, tool, params):
    seen = []
    monkeypatch.setattr(router, "_execute", lambda t, p, raw, **kw: seen.append((t, p)) or {"status": "ok"})
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


@pytest.mark.parametrize("text,tool,params", [
    ("launch firefox", "app.open", {"path": "firefox"}),
    ("open google chrome", "app.open", {"path": "google chrome"}),
    ("import ics ~/Downloads/invite.ics", "calendar.import_ics", {"filepath": "~/Downloads/invite.ics"}),
    ("look up files with report in the name", "fs.find_file", {"name": "report"}),
    ("start stopwatch", "utility.stopwatch", {"action": "start"}),
])
def test_routes_found_in_live_run(router, monkeypatch, text, tool, params):
    seen = []
    monkeypatch.setattr(router, "_execute", lambda t, p, raw, **kw: seen.append((t, p)) or {"status": "ok"})
    router.route(text)
    assert seen == [(tool, params)]


def test_long_launch_requests_are_left_for_tier2(router):
    assert router.route("launch something to write some text")["status"] == "no_match"


def test_ics_import_end_to_end(router, tmp_path):
    ics = tmp_path / "invite.ics"
    ics.write_text("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:x1\nDTSTART:20261022T100000\n"
                   "SUMMARY:Design review\nEND:VEVENT\nEND:VCALENDAR\n")
    assert router.route(f"import ics {ics}")["status"] == "ok"
    assert [e["title"] for e in CalendarStore().query_range("2026-10-22", "2026-10-22")] == ["Design review"]


@pytest.mark.parametrize("expr,expected", [
    ("2^10", 1024), ("(45 * 2) + 10", 100), ("3 x 4", 12), ("max(2, 7)", 7), ("10 % 3", 1), ("sqrt(16)", 4),
])
def test_calculator(registry, expr, expected):
    assert registry.execute("utility.calculate", {"expr": expr})["result"] == expected


@pytest.mark.parametrize("expr", ["__import__('os')", "9**9**9", "().__class__", "1/0"])
def test_calculator_rejects_unsafe_or_invalid(registry, expr):
    assert registry.execute("utility.calculate", {"expr": expr})["status"] == "error"


def test_weekday_due_dates_point_forward(registry):
    from datetime import date
    due = registry.execute("todo.add", {"text": "x", "due_date": "monday"})["todo"]["due_date"]
    assert date.fromisoformat(due) > date.today()
