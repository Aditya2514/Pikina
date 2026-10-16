"""
The single execution path for capabilities.

Every caller (Tier 1 router, Tier 2 router, alias transactions, the HTTP API)
goes through execute_action(), so the kill switch, the consent gate and the
audit events are applied identically everywhere.
"""
from core.eventbus.bus import EventBus
from core.mcm import killswitch
from core.registry.gatekeeper import needs_consent, request_consent
from core.validation.failure_classes import FailureClass


def execute_action(registry, tool: str, params: dict, *, raw: str = "", tier=1,
                   provenance: str = "TRUSTED_COMMAND", gate: bool = True) -> dict:
    """
    Run one capability.
      gate=False is only for callers that already obtained consent
      (the Tier 2 AVL holds and asks before returning).
    """
    bus = EventBus()

    if killswitch.is_engaged():
        return {"status": "denied", "reason": "Kill switch is engaged — all actions are halted."}

    try:
        manifest = registry.get_manifest(tool)
    except KeyError:
        return {"status": "error", "reason": f"Unknown tool: '{tool}'",
                "failure_class": FailureClass.VALIDATION}

    level = manifest["permission_level"]

    if gate and needs_consent(manifest):
        if not request_consent(tool, manifest["description"], params):
            bus.publish(
                topic="router.denied",
                payload={"tool": tool, "params": params, "raw": raw, "tier": tier},
                provenance=provenance,
                permission_level=level,
            )
            return {"status": "denied", "reason": "User declined consent dialog."}

    bus.publish(
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

    bus.publish(
        topic="router.result",
        payload={"tool": tool, "result": result, "tier": tier},
        provenance=provenance,
        permission_level=level,
    )
    return result
