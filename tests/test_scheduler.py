"""
Phase 4c — Scheduler: dependency resolution, DAG cycle checks,
earliest-deadline-first ordering, cost limits and starvation protection.
"""
import pytest

from pikina.scheduler.task_graph import Task
from pikina.scheduler.dispatcher import TaskDispatcher


def test_dependency_keeps_task_blocked():
    d = TaskDispatcher()
    a = Task(id="task_a", capability="app.open")
    b = Task(id="task_b", capability="app.open")
    c = Task(id="task_c", capability="app.open", dependencies=["task_a", "task_b"])
    d.add_tasks([a, b, c])

    assert c.status == "blocked"
    d.tick()
    assert c.status == "blocked"


def test_completed_dependencies_unblock_task():
    d = TaskDispatcher()
    a = Task(id="task_a", capability="app.open")
    b = Task(id="task_b", capability="app.open")
    c = Task(id="task_c", capability="app.open", dependencies=["task_a", "task_b"])
    d.add_tasks([a, b, c])

    d.complete_task("task_a")
    d.complete_task("task_b")
    d.tick()
    assert c.status in ("ready", "running", "done")


def test_earliest_deadline_dispatches_first():
    d = TaskDispatcher()
    later = Task(id="d1", capability="app.open", priority=2, deadline="2026-07-13T18:00:00")
    sooner = Task(id="d2", capability="app.open", priority=2, deadline="2026-07-13T17:00:00")
    d.add_tasks([later, sooner])

    d.tick()
    assert sooner.status == "done"
    assert later.status == "ready"


def test_highest_priority_dispatches_first():
    d = TaskDispatcher()
    low = Task(id="p1", capability="app.open", priority=2)
    high = Task(id="p2", capability="app.open", priority=4)
    d.add_tasks([low, high])

    d.tick()
    assert high.status == "done"
    assert low.status == "ready"


def test_expensive_task_skipped_without_stalling_queue():
    d = TaskDispatcher()
    expensive = Task(id="expensive", capability="system.heavy", priority=4)
    cheap = Task(id="cheap", capability="app.open", priority=2)
    d.add_tasks([expensive, cheap])
    d.check_cost = lambda task: task.id == "cheap"

    d.tick()
    assert cheap.status == "done"
    assert expensive.status == "ready"
    assert expensive.skipped_count == 1


def test_cyclic_batch_rejected_atomically():
    d = TaskDispatcher()
    d.add_tasks([Task(id="normal", capability="app.open")])

    x = Task(id="task_x", capability="app.open", dependencies=["task_y"])
    y = Task(id="task_y", capability="app.open", dependencies=["task_x"])
    with pytest.raises(ValueError, match="Cyclic"):
        d.add_tasks([x, y])

    assert set(d.tasks) == {"normal"}


def test_starvation_boost_flips_sort_order():
    d = TaskDispatcher()
    starving = Task(id="starving", capability="heavy", priority=3)
    d.add_tasks([starving, Task(id="b1", capability="cheap", priority=2)])
    d.check_cost = lambda task: task.id != "starving"

    d.tick()
    assert starving.skipped_count == 1
    for i in range(2, 6):
        d.add_tasks([Task(id=f"b{i}", capability="cheap", priority=2)])
        d.tick()
    assert starving.skipped_count == 5

    # Boosted effective priority (3 + 1 = 4) now beats a 3.5-priority task
    rival = Task(id="rival", capability="cheap", priority=3.5)
    rival.status = "ready"
    d.tasks["rival"] = rival
    ready = sorted((t for t in d.tasks.values() if t.status == "ready"), key=d._sort_key)
    assert [t.id for t in ready[:2]] == ["starving", "rival"]
