"""
Pikina OS — Plan Executor
Executes plans step-by-step and initiates rollback on step failure.
"""
from core.eventbus.bus import EventBus
from core.transactions.rollback_engine import RollbackEngine


class PlanStep:
    """Represents a single executable step in a Plan."""
    def __init__(self, step_id: str, tool: str, params: dict = None, rollback_action: str = None, status: str = "pending"):
        self.step_id = step_id
        self.tool = tool
        self.params = params or {}
        self.rollback_action = rollback_action  # Resolved at construction
        self.status = status  # pending, done, failed, rolled_back, rollback_failed, no_rollback_available

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "params": self.params,
            "rollback_action": self.rollback_action,
            "status": self.status,
        }


class Plan:
    """Represents an ordered sequence of PlanSteps."""
    def __init__(self, id: str, steps: list, status: str = "pending"):
        self.id = id
        self.steps = steps  # list of PlanStep objects
        self.status = status  # pending, running, completed, failed, rolled_back, partially_rolled_back

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
        }


class PlanExecutor:
    """Handles execution runner orchestration and EventBus synchronization."""
    def __init__(self, registry=None, execution_callback=None, rollback_callback=None):
        self.registry = registry
        self.execution_callback = execution_callback
        self.rollback_engine = RollbackEngine(rollback_callback)
        self.bus = EventBus()

    def execute(self, plan: Plan) -> str:
        """
        Executes the plan steps in order.
        Aborts execution and invokes rollback on first failed step.
        """
        plan.status = "running"
        completed_steps = []
        failed_step = None

        for step in plan.steps:
            # 1. Log step execution start
            self.bus.publish(
                topic="transaction.step_execution",
                payload={"plan_id": plan.id, "step_id": step.step_id, "action": "start"},
                provenance="system",
            )

            # 2. Run step callback (mocked test double)
            success = True
            if self.execution_callback:
                success = self.execution_callback(step)

            if success:
                step.status = "done"
                completed_steps.append(step)
                # Log step execution success
                self.bus.publish(
                    topic="transaction.step_execution",
                    payload={"plan_id": plan.id, "step_id": step.step_id, "action": "success"},
                    provenance="system",
                )
            else:
                step.status = "failed"
                failed_step = step
                # Log step execution failure
                self.bus.publish(
                    topic="transaction.step_execution",
                    payload={"plan_id": plan.id, "step_id": step.step_id, "action": "failure"},
                    provenance="system",
                )
                break

        if failed_step:
            plan.status = "failed"
            # 3. Trigger rollback engine
            rollback_ok = self.rollback_engine.rollback(plan.id, completed_steps)
            if rollback_ok:
                plan.status = "rolled_back"
            else:
                plan.status = "partially_rolled_back"
        else:
            plan.status = "completed"

        return plan.status
