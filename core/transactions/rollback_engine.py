"""
Pikina OS — Transaction Rollback Engine
Handles reverse-order execution of rollback actions and manages partial failure limits.
"""
from core.eventbus.bus import EventBus
from core.validation.failure_classes import FailureClass


class RollbackEngine:
    """Manages transactional rollback logic across successfully completed plan steps."""
    def __init__(self, rollback_callback=None):
        self.rollback_callback = rollback_callback
        self.bus = EventBus()

    def rollback(self, plan_id: str, completed_steps: list) -> bool:
        """
        Executes rollback actions in reverse order.
        Returns True if all completed steps were successfully rolled back.
        Returns False if any step rollback fails or has no rollback action defined.
        Aborts immediately on the first rollback error or un-rollbackable step.
        """
        all_ok = True

        for step in reversed(completed_steps):
            if step.rollback_action:
                # 1. Log rollback start
                self.bus.publish(
                    topic="transaction.rollback",
                    payload={"plan_id": plan_id, "step_id": step.step_id, "action": "start"},
                    provenance="system",
                )

                # 2. Run rollback callback (mocked test double)
                success = True
                if self.rollback_callback:
                    success = self.rollback_callback(step)

                if success:
                    step.status = "rolled_back"
                    # Log rollback success
                    self.bus.publish(
                        topic="transaction.rollback",
                        payload={"plan_id": plan_id, "step_id": step.step_id, "action": "success"},
                        provenance="system",
                    )
                else:
                    step.status = "rollback_failed"
                    # Log rollback failure as infrastructure class error
                    self.bus.publish(
                        topic="transaction.rollback",
                        payload={"plan_id": plan_id, "step_id": step.step_id, "action": "failure"},
                        provenance="system",
                        failure_class=FailureClass.INFRASTRUCTURE,
                    )
                    all_ok = False
                    # Stop walking immediately!
                    break
            else:
                # No rollback action defined in the manifest
                step.status = "no_rollback_available"
                # Log rollback warning
                self.bus.publish(
                    topic="transaction.rollback",
                    payload={"plan_id": plan_id, "step_id": step.step_id, "action": "missing_rollback_action"},
                    provenance="system",
                )
                all_ok = False
                # Stop walking immediately!
                break

        return all_ok
