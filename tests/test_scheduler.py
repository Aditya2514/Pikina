"""
Pikina OS — Scheduler Test Suite (Phase 4c)
Verifies dependency resolution, DAG cycle checks, earliest-deadline-first ordering,
governor cost limits, and starvation priority boosting.
"""
import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.scheduler.task_graph import Task, validate_task_graph
from core.scheduler.dispatcher import TaskDispatcher


def run_tests():
    print("=" * 65)
    print("  Pikina OS — Phase 4c Scheduler Test Suite")
    print("=" * 65)

    # -------------------------------------------------------------
    # Case 1: Dependency keeps task blocked
    # -------------------------------------------------------------
    print("\n[Case 1] Dependency A and B not done yet -> C stays blocked...")
    dispatcher = TaskDispatcher()
    task_a = Task(id="task_a", capability="app.open", dependencies=[])
    task_b = Task(id="task_b", capability="app.open", dependencies=[])
    task_c = Task(id="task_c", capability="app.open", dependencies=["task_a", "task_b"])

    dispatcher.add_tasks([task_a, task_b, task_c])
    
    # Tick should run one of the ready tasks (A or B), but C must remain blocked
    assert task_c.status == "blocked"
    dispatcher.tick()
    assert task_c.status == "blocked"
    print("  [PASS] Task C remains blocked.")

    # -------------------------------------------------------------
    # Case 2: Dependencies completed transitions task to ready
    # -------------------------------------------------------------
    print("\n[Case 2] Dependencies A and B marked done -> C becomes ready...")
    # Mark task_a and task_b as done
    dispatcher.complete_task("task_a")
    dispatcher.complete_task("task_b")
    
    # Run tick to recompute and transition C to ready (and execute it)
    dispatcher.tick()
    assert task_c.status in ("ready", "running", "done")
    print("  [PASS] Task C successfully transitioned and dispatched.")

    # -------------------------------------------------------------
    # Case 3: Deadlines sort comparison (earliest deadline first)
    # -------------------------------------------------------------
    print("\n[Case 3] Two ready tasks, same priority, different deadlines...")
    dispatcher = TaskDispatcher()
    task_d1 = Task(id="task_d1", capability="app.open", priority=2, deadline="2026-07-13T18:00:00")
    task_d2 = Task(id="task_d2", capability="app.open", priority=2, deadline="2026-07-13T17:00:00")
    
    dispatcher.add_tasks([task_d1, task_d2])
    
    # Ensure d2 (earlier deadline) dispatches first
    dispatcher.tick()
    assert task_d2.status == "done"
    assert task_d1.status == "ready"
    print("  [PASS] Earlier-deadline task dispatched first.")

    # -------------------------------------------------------------
    # Case 4: Priority sort comparison (highest priority first)
    # -------------------------------------------------------------
    print("\n[Case 4] Two ready tasks, no deadlines, different priorities...")
    dispatcher = TaskDispatcher()
    task_p1 = Task(id="task_p1", capability="app.open", priority=2)
    task_p2 = Task(id="task_p2", capability="app.open", priority=4)
    
    dispatcher.add_tasks([task_p1, task_p2])
    
    # Ensure p2 (higher priority) dispatches first
    dispatcher.tick()
    assert task_p2.status == "done"
    assert task_p1.status == "ready"
    print("  [PASS] Higher-priority task dispatched first.")

    # -------------------------------------------------------------
    # Case 5: Expensive task skipped, doesn't stall cheap task
    # -------------------------------------------------------------
    print("\n[Case 5] Expensive task skipped without stalling the queue...")
    dispatcher = TaskDispatcher()
    task_expensive = Task(id="task_expensive", capability="system.heavy", priority=4)
    task_cheap = Task(id="task_cheap", capability="app.open", priority=2)
    
    dispatcher.add_tasks([task_expensive, task_cheap])
    
    # Mock cost checks: task_expensive is costly, task_cheap fits budget
    def mock_check_cost(task):
        return task.id == "task_cheap"
        
    dispatcher.check_cost = mock_check_cost
    
    # Tick should check task_expensive (fails), skip it, then check and dispatch task_cheap
    dispatcher.tick()
    assert task_cheap.status == "done"
    assert task_expensive.status == "ready"
    assert task_expensive.skipped_count == 1
    print("  [PASS] Cost check skipped expensive task and processed cheap task.")

    # -------------------------------------------------------------
    # Case 6: Cyclic dependency graph rejected atomically
    # -------------------------------------------------------------
    print("\n[Case 6] Cyclic dependencies rejected atomically at add_tasks() time...")
    dispatcher = TaskDispatcher()
    
    # Add a normal task first to confirm dictionary is not cleared, only that the new batch is rejected
    normal_task = Task(id="normal_task", capability="app.open")
    dispatcher.add_tasks([normal_task])
    assert "normal_task" in dispatcher.tasks
    
    # Prepare cyclic batch
    task_x = Task(id="task_x", capability="app.open", dependencies=["task_y"])
    task_y = Task(id="task_y", capability="app.open", dependencies=["task_x"])
    
    # Check that ValueError is raised
    cycle_raised = False
    try:
        dispatcher.add_tasks([task_x, task_y])
    except ValueError as e:
        cycle_raised = True
        print(f"  Expected cycle error raised: {e}")
        
    assert cycle_raised, "Failed: Expected cycle detection to raise ValueError"
    
    # Confirm ATOMIC REJECTION: Neither task_x nor task_y was added to dispatcher.tasks
    assert "task_x" not in dispatcher.tasks
    assert "task_y" not in dispatcher.tasks
    assert len(dispatcher.tasks) == 1  # Only normal_task remains
    print("  [PASS] Cyclic dependency rejected atomically before scheduling.")

    # -------------------------------------------------------------
    # Case 7: Starvation protection (Priority Boost Sort Outcome Flip)
    # -------------------------------------------------------------
    print("\n[Case 7] Starvation protection boosts priority and flips sort order...")
    dispatcher = TaskDispatcher()
    
    # Task A: priority 3, high cost (always fails check_cost)
    task_starving = Task(id="task_starving", capability="heavy", priority=3)
    
    # Initial setup: Add Task A and a cheap Task B (priority 2)
    task_b1 = Task(id="task_b1", capability="cheap", priority=2)
    dispatcher.add_tasks([task_starving, task_b1])
    
    # Mock cost check: task_starving is always blocked, others succeed
    def starvation_cost_check(task):
        return task.id != "task_starving"
    dispatcher.check_cost = starvation_cost_check
    
    # 1. First tick: task_starving (priority 3) is checked first, fails, skipped_count = 1.
    #    task_b1 (priority 2) is checked second, dispatches.
    dispatcher.tick()
    assert task_b1.status == "done"
    assert task_starving.skipped_count == 1
    
    # 2-5. Add more cheap priority 2 tasks and tick to raise skip count to 5
    for i in range(2, 6):
        task_bi = Task(id=f"task_b{i}", capability="cheap", priority=2)
        dispatcher.add_tasks([task_bi])
        dispatcher.tick()
        
    assert task_starving.skipped_count == 5
    
    # Now task_starving's effective priority is boosted by 1: from 3 -> 4.
    # Let's add a new Task D (priority 3.5, low cost).
    # Without starvation boost, Task D (priority 3.5) would sort before task_starving (priority 3).
    # BUT with boost, task_starving's effective priority is 4, which is > 3.5, so task_starving must sort FIRST.
    task_d = Task(id="task_d", capability="cheap", priority=3.5)
    
    # We bypass add_tasks to insert directly for the comparison (since we only want to test sorting)
    dispatcher.tasks["task_d"] = task_d
    task_d.status = "ready"
    
    # Check dispatcher sorting outcome
    ready_tasks = [t for t in dispatcher.tasks.values() if t.status == "ready"]
    ready_tasks.sort(key=dispatcher._sort_key)
    
    # Assert sorting outcome flip: task_starving (boosted to 4) is ahead of task_d (priority 3.5)
    assert ready_tasks[0].id == "task_starving", f"Failed: Expected starving task first, got {ready_tasks[0].id}"
    assert ready_tasks[1].id == "task_d"
    print("  [PASS] Starvation protection successfully flipped sorting outcome.")

    print("\n" + "=" * 65)
    print("  === ALL PHASE 4c SCHEDULER TESTS PASSED ===")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_tests()
