"""
Pikina OS — Task Dispatcher
Controls the dispatch loop, recomputes status, applies cost checks, and protects against starvation.
"""
from core.scheduler.task_graph import Task, validate_task_graph

try:
    from core.governor.cost_function import calculate_capability_cost
    from core.governor.profiles import THRESHOLD
except ImportError:
    # Safe fallbacks if run in an environment where governor is missing
    calculate_capability_cost = lambda manifest: 0.1
    THRESHOLD = 1.5


class TaskDispatcher:
    """Manages scheduling state, recomputes dependencies, and dispatches ready tasks."""
    def __init__(self, registry=None, execution_callback=None):
        self.tasks = {}  # task_id -> Task
        self.registry = registry
        self.execution_callback = execution_callback

    def add_tasks(self, tasks: list):
        """
        Validates the dependency graph atomically.
        If acyclic validation passes, all tasks are added to the scheduler queue.
        If a cycle is detected, no tasks are added.
        """
        # Validate graph atomically first
        validate_task_graph(tasks)

        # If valid, insert into queue and initialize initial statuses
        for t in tasks:
            if t.dependencies:
                # If any dependency task is not already done, it is blocked
                all_done = True
                for dep_id in t.dependencies:
                    dep = self.tasks.get(dep_id)
                    if dep and dep.status != "done":
                        all_done = False
                        break
                t.status = "blocked" if not all_done else "ready"
            else:
                t.status = "ready"
            self.tasks[t.id] = t

    def _recompute_status(self):
        """Transition blocked or queued tasks whose dependencies are all done to ready."""
        for t in self.tasks.values():
            if t.status in ("blocked", "queued"):
                all_done = True
                for dep_id in t.dependencies:
                    dep = self.tasks.get(dep_id)
                    # If the dependency exists in our queue and is not done, we cannot run
                    if dep and dep.status != "done":
                        all_done = False
                        break
                if all_done:
                    t.status = "ready"

    def _sort_key(self, t: Task):
        """
        Sort key:
        1. Earliest deadline first (no deadline sorts last using 9999-12-31).
        2. Within equal/no deadline, highest priority first (ascending order sort uses negative priority).
        
        Starvation Protection:
        If a task has been skipped >= 5 times, its effective priority increases by 1 for sorting purposes.
        """
        dl = t.deadline if t.deadline is not None else "9999-12-31T23:59:59"
        effective_priority = t.priority + (1 if t.skipped_count >= 5 else 0)
        return (dl, -effective_priority)

    def check_cost(self, task: Task) -> bool:
        """Determines if a task fits within the governor's profile threshold."""
        try:
            manifest = self.registry.get_manifest(task.capability) if self.registry else {}
        except Exception:
            manifest = {}
        score = calculate_capability_cost(manifest)
        return score <= THRESHOLD

    def tick(self):
        """Runs one execution step of the scheduling queue."""
        # 1. Recompute dependency status
        self._recompute_status()

        # 2. Gather ready tasks
        ready_tasks = [t for t in self.tasks.values() if t.status == "ready"]
        if not ready_tasks:
            return

        # 3. Sort ready tasks
        ready_tasks.sort(key=self._sort_key)

        # 4. Find the first task that fits the cost budget
        for t in ready_tasks:
            if self.check_cost(t):
                # Dispatch this task
                t.status = "running"
                t.skipped_count = 0  # Reset skipped ticks upon dispatch
                
                if self.execution_callback:
                    self.execution_callback(t)
                else:
                    # Default synchronous execution callback
                    t.status = "done"
                    self._recompute_status()
                # Tick executes at most one task
                break
            else:
                # Increment skip count for starvation protection
                t.skipped_count += 1

    def complete_task(self, task_id: str, success: bool = True):
        """Callback to mark a running task as finished."""
        task = self.tasks.get(task_id)
        if task:
            task.status = "done" if success else "failed"
            self._recompute_status()
