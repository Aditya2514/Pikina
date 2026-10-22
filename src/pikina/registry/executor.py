"""
The single execution path for capabilities.

Every caller (Tier 1 router, Tier 2 router, alias transactions, the HTTP API)
goes through execute_action(), so the kill switch, the consent gate and the
audit events are applied identically everywhere. It is also where the
activity log hears about every action (see pikina.activity).
"""
from datetime import datetime, timezone

from pikina.activity.recorder import record
from pikina.eventbus.bus import EventBus
from pikina.mcm import killswitch
from pikina.registry.gatekeeper import needs_consent, request_consent
from pikina.validation.failure_classes import FailureClass


def execute_action(registry, tool: str, params: dict, *, raw: str = "", tier=1,
                   provenance: str = "TRUSTED_COMMAND", gate: bool = True,
                   origin: str = "typed", log_activity: bool = True, group_id: str | None = None,
                   started_at: datetime | None = None) -> dict:
    """
    Run one capability.
      gate=False is only for callers that already obtained consent
      (the Tier 2 AVL holds and asks before returning).
      origin is where the command came from (typed, quick_panel, cli, alias, ui).
      log_activity=False keeps it out of the activity log (rollbacks: not something you did).
      group_id links alias steps to their alias run.
      started_at: when the user asked (Tier 2 passes the time before its own consent dialog).
    """
    bus = EventBus()
    started = started_at or datetime.now(timezone.utc)  # before any consent dialog, which can take a while

    def log(result, event_id):
        if log_activity:
            record(tool, params, result, source=origin, tier=tier, origin_event=event_id,
                   group_id=group_id, ts=started)

    if killswitch.is_engaged():
        return {"status": "denied", "reason": "Kill switch is engaged — all actions are halted."}

    try:
        manifest = registry.get_manifest(tool)
    except KeyError:
        return {"status": "error", "reason": f"Unknown tool: '{tool}'",
                "failure_class": FailureClass.VALIDATION}

    level = manifest["permission_level"]
    # Capabilities that show or control the activity log itself stay out of it, and
    # their results (your app names, top actions) stay out of the replay log too.
    private_result = manifest.get("activity_log") is False
    if private_result:
        log_activity = False

    if gate and needs_consent(manifest):
        if not request_consent(tool, manifest["description"], params):
            denied_id = bus.publish(
                topic="router.denied",
                payload={"tool": tool, "params": params, "raw": raw, "tier": tier},
                provenance=provenance,
                permission_level=level,
            )
            denied = {"status": "denied", "reason": "User declined consent dialog."}
            log(denied, denied_id)
            return denied

    executing_id = bus.publish(
        topic="router.executing",
        payload={"tool": tool, "params": params, "raw": raw, "tier": tier},
        provenance=provenance,
        permission_level=level,
    )

    try:
        result = registry.execute(tool, params)
    except Exception as exc:  # a crashing handler must not take down the router
        result = {"status": "error", "reason": f"{type(exc).__name__}: {exc}",
                  "failure_class": FailureClass.RECOVERABLE}

    logged = {"status": result.get("status") if isinstance(result, dict) else None} if private_result else result
    bus.publish(
        topic="router.result",
        payload={"tool": tool, "result": logged, "tier": tier, "exec_id": executing_id},
        provenance=provenance,
        permission_level=level,
    )
    log(result, executing_id)
    return result
