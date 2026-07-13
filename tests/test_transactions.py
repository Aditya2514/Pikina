"""
Pikina OS — Transactions and Rollback Test Suite (Phase 4d)
Verifies sequential plan execution, rollback procedures, atomic failure bounds,
and EventBus history auditing.
"""
import sys
import os
import sqlite3
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.eventbus.bus import EventBus
from core.eventbus.replay import ReplayStore
from core.validation.failure_classes import FailureClass
from core.transactions.plan_executor import Plan, PlanStep, PlanExecutor


def run_tests():
    print("=" * 65)
    print("  Pikina OS — Phase 4d Transaction Test Suite")
    print("=" * 65)

    bus = EventBus()
    replay = ReplayStore()

    # Clear previous transaction events from replay store to avoid cross-contamination
    with sqlite3.connect(replay.db_path) as conn:
        conn.execute("DELETE FROM events WHERE topic LIKE 'transaction.%'")
        conn.commit()

    # -------------------------------------------------------------
    # Case 1: 3-step plan where all steps succeed
    # -------------------------------------------------------------
    print("\n[Case 1] 3-step plan where all steps succeed...")
    step1 = PlanStep(step_id="step1", tool="calendar.add_event", rollback_action="calendar.remove_event")
    step2 = PlanStep(step_id="step2", tool="todo.add", rollback_action="todo.remove")
    step3 = PlanStep(step_id="step3", tool="prefs.update", rollback_action=None)
    plan1 = Plan(id="plan_success", steps=[step1, step2, step3])

    execution_calls = []
    rollback_calls = []

    def success_execution_callback(step):
        execution_calls.append(step.step_id)
        return True

    def rollback_callback(step):
        rollback_calls.append(step.step_id)
        return True

    executor = PlanExecutor(
        execution_callback=success_execution_callback,
        rollback_callback=rollback_callback
    )
    
    status = executor.execute(plan1)
    assert status == "completed"
    assert plan1.status == "completed"
    assert len(execution_calls) == 3
    assert len(rollback_calls) == 0
    print("  [PASS] All steps completed successfully, no rollback run.")

    # -------------------------------------------------------------
    # Case 2: 3-step plan where step 2 fails (step 1 rolls back)
    # -------------------------------------------------------------
    print("\n[Case 2] Step 2 fails -> Step 1 rolls back -> plan rolled_back...")
    step1 = PlanStep(step_id="step1", tool="calendar.add_event", rollback_action="calendar.remove_event")
    step2 = PlanStep(step_id="step2", tool="todo.add", rollback_action="todo.remove")
    step3 = PlanStep(step_id="step3", tool="prefs.update", rollback_action=None)
    plan2 = Plan(id="plan_fail_step2", steps=[step1, step2, step3])

    execution_calls.clear()
    rollback_calls.clear()

    def step2_fail_execution_callback(step):
        execution_calls.append(step.step_id)
        return step.step_id != "step2"

    executor = PlanExecutor(
        execution_callback=step2_fail_execution_callback,
        rollback_callback=rollback_callback
    )

    status = executor.execute(plan2)
    assert status == "rolled_back"
    assert plan2.status == "rolled_back"
    assert "step2" in execution_calls
    assert "step3" not in execution_calls  # Step 3 must never run
    assert rollback_calls == ["step1"]  # Step 1 rolls back
    assert step1.status == "rolled_back"
    assert step2.status == "failed"
    print("  [PASS] Sequential execution aborted, ran rollback for step1.")

    # -------------------------------------------------------------
    # Case 3: Step 2 fails, Step 1 has rollback_action: None
    # -------------------------------------------------------------
    print("\n[Case 3] Step 2 fails, Step 1 has rollback_action: None...")
    step1 = PlanStep(step_id="step1", tool="calendar.add_event", rollback_action=None)
    step2 = PlanStep(step_id="step2", tool="todo.add", rollback_action="todo.remove")
    step3 = PlanStep(step_id="step3", tool="prefs.update", rollback_action=None)
    plan3 = Plan(id="plan_no_rollback", steps=[step1, step2, step3])

    execution_calls.clear()
    rollback_calls.clear()

    executor = PlanExecutor(
        execution_callback=step2_fail_execution_callback,
        rollback_callback=rollback_callback
    )

    status = executor.execute(plan3)
    assert status == "partially_rolled_back"
    assert plan3.status == "partially_rolled_back"
    assert step1.status == "no_rollback_available"
    assert len(rollback_calls) == 0  # No callback was run since rollback_action is None
    print("  [PASS] Plan status: partially_rolled_back, step1 marked no_rollback_available.")

    # -------------------------------------------------------------
    # Case 4: Step 3 fails, Step 2 rollback fails (Step 1 never attempted)
    # -------------------------------------------------------------
    print("\n[Case 4] Rollback of Step 2 fails -> Step 1 never attempted...")
    step1 = PlanStep(step_id="step1", tool="calendar.add_event", rollback_action="calendar.remove_event")
    step2 = PlanStep(step_id="step2", tool="todo.add", rollback_action="todo.remove")
    step3 = PlanStep(step_id="step3", tool="prefs.update", rollback_action="prefs.rollback")
    step4 = PlanStep(step_id="step4", tool="alias.add", rollback_action=None)
    plan4 = Plan(id="plan_rollback_fail", steps=[step1, step2, step3, step4])

    execution_calls.clear()
    rollback_calls.clear()

    def step3_fail_execution_callback(step):
        execution_calls.append(step.step_id)
        return step.step_id != "step4"

    # Step 2's rollback fails, Step 3 succeeds (if run)
    def fail_step2_rollback_callback(step):
        rollback_calls.append(step.step_id)
        return step.step_id != "step2"

    executor = PlanExecutor(
        execution_callback=step3_fail_execution_callback,
        rollback_callback=fail_step2_rollback_callback
    )

    status = executor.execute(plan4)
    assert status == "partially_rolled_back"
    assert plan4.status == "partially_rolled_back"
    
    # Verify execution path
    assert execution_calls == ["step1", "step2", "step3", "step4"]
    
    # Verify rollback path
    assert "step3" in rollback_calls  # Step 3 rollback succeeded
    assert "step2" in rollback_calls  # Step 2 rollback ran and failed
    
    # ASSERTION OF ISOLATION: Step 1 rollback is NEVER attempted!
    assert "step1" not in rollback_calls
    assert step2.status == "rollback_failed"
    assert step1.status == "done"  # Remains in completed 'done' state since never rolled back
    print("  [PASS] Rollback stopped at failed step2; step1 rollback was never attempted.")

    # -------------------------------------------------------------
    # Case 5: Verify EventBus event auditing
    # -------------------------------------------------------------
    print("\n[Case 5] Auditing EventBus log history in replay store...")
    events = replay.query(since_minutes=1, topic="transaction.rollback")
    
    # We must have at least one failure event in the rollback logs
    failures = [e for e in events if e["failure_class"] == FailureClass.INFRASTRUCTURE]
    assert len(failures) > 0, "Failed: Expected at least one transaction.rollback event with FailureClass.INFRASTRUCTURE"
    
    # Verify rollback execution fields
    step_runs = replay.query(since_minutes=1, topic="transaction.step_execution")
    assert len(step_runs) > 0
    assert step_runs[0]["payload"]["action"] in ("start", "success", "failure")
    print("  [PASS] Replay store contains correct transaction logs with infrastructure failure classes.")

    print("\n" + "=" * 65)
    print("  === ALL PHASE 4d TRANSACTION TESTS PASSED ===")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_tests()
