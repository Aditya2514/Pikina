"""
Phase 4d — Transactions: sequential plan execution, reverse-order rollback,
stop-on-rollback-failure, and replay-log auditing.
"""
from pikina.validation.failure_classes import FailureClass
from pikina.transactions.plan_executor import Plan, PlanStep, PlanExecutor


def _steps(rb1="calendar.remove_event", rb2="todo.remove", rb3=None):
    return [
        PlanStep(step_id="step1", tool="calendar.add_event", rollback_action=rb1),
        PlanStep(step_id="step2", tool="todo.add", rollback_action=rb2),
        PlanStep(step_id="step3", tool="prefs.update", rollback_action=rb3),
    ]


def _executor(fail_on=None, rollback_fails_on=None, executed=None, rolled=None):
    executed = executed if executed is not None else []
    rolled = rolled if rolled is not None else []

    def run(step):
        executed.append(step.step_id)
        return step.step_id != fail_on

    def undo(step):
        rolled.append(step.step_id)
        return step.step_id != rollback_fails_on

    return PlanExecutor(execution_callback=run, rollback_callback=undo)


def test_all_steps_succeed_without_rollback():
    executed, rolled = [], []
    plan = Plan(id="ok", steps=_steps())
    assert _executor(executed=executed, rolled=rolled).execute(plan) == "completed"
    assert executed == ["step1", "step2", "step3"]
    assert rolled == []


def test_failed_step_rolls_back_earlier_steps():
    executed, rolled = [], []
    steps = _steps()
    plan = Plan(id="fail2", steps=steps)
    assert _executor(fail_on="step2", executed=executed, rolled=rolled).execute(plan) == "rolled_back"
    assert "step3" not in executed
    assert rolled == ["step1"]
    assert steps[0].status == "rolled_back"
    assert steps[1].status == "failed"


def test_missing_rollback_action_means_partial_rollback():
    rolled = []
    steps = _steps(rb1=None)
    plan = Plan(id="norb", steps=steps)
    assert _executor(fail_on="step2", rolled=rolled).execute(plan) == "partially_rolled_back"
    assert steps[0].status == "no_rollback_available"
    assert rolled == []


def test_read_only_steps_are_skipped_during_rollback():
    rolled = []
    steps = [
        PlanStep(step_id="step1", tool="todo.add", rollback_action="todo.remove"),
        PlanStep(step_id="step2", tool="utility.calculate", read_only=True),
        PlanStep(step_id="step3", tool="app.open"),
    ]
    plan = Plan(id="ro", steps=steps)
    assert _executor(fail_on="step3", rolled=rolled).execute(plan) == "rolled_back"
    assert rolled == ["step1"]
    assert steps[1].status == "no_rollback_needed"


def test_rollback_failure_stops_walk(replay):
    executed, rolled = [], []
    steps = _steps(rb3="prefs.rollback") + [PlanStep(step_id="step4", tool="alias.add")]
    plan = Plan(id="rbfail", steps=steps)
    status = _executor(fail_on="step4", rollback_fails_on="step2",
                       executed=executed, rolled=rolled).execute(plan)

    assert status == "partially_rolled_back"
    assert executed == ["step1", "step2", "step3", "step4"]
    assert rolled == ["step3", "step2"]          # step1 is never attempted
    assert steps[1].status == "rollback_failed"
    assert steps[0].status == "done"

    rollback_events = replay.query(since_minutes=1, topic="transaction.rollback")
    assert any(e["failure_class"] == FailureClass.INFRASTRUCTURE for e in rollback_events)
    step_events = replay.query(since_minutes=1, topic="transaction.step_execution")
    assert step_events and step_events[0]["payload"]["action"] in ("start", "success", "failure")
