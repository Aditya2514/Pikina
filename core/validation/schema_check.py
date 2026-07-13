"""
Action Validation Layer — schema check before any model-proposed action runs.
Nothing a model proposes reaches a tool without passing through here first.
"""
from .failure_classes import FailureClass
from core.eventbus.bus import EventBus
from core.registry.gatekeeper import request_consent


def validate_action(proposed: dict, registry) -> tuple:
    """
    Validate a proposed action against the Capability Registry.

    Args:
        proposed: dict with keys: tool, params, permission_level, provenance
        registry: CapabilityRegistry instance

    Returns:
        (is_valid: bool, failure_class: FailureClass | None, reason: str | None)
    """
    tool          = proposed.get("tool")
    params        = proposed.get("params", {})
    claimed_level = proposed.get("permission_level")
    provenance    = proposed.get("provenance", "")

    # 1. Tool must exist in the registry
    try:
        manifest = registry.get_manifest(tool)
    except KeyError:
        return False, FailureClass.VALIDATION, f"Unknown tool: '{tool}'"

    # 2. Permission level must match registry exactly — never trust model-claimed level
    registry_level = manifest["permission_level"]
    if claimed_level is not None and claimed_level != registry_level:
        return (
            False,
            FailureClass.PERMISSION,
            f"Claimed level {claimed_level} ≠ registry level {registry_level} for '{tool}'",
        )

    # 3. Model output cannot directly trigger a tool — it must pass human/AVL gate first
    if provenance == "model_output":
        return (
            False,
            FailureClass.PERMISSION,
            "model_output cannot directly trigger tool execution. "
            "Requires human confirmation or AVL re-validation after stripping provenance.",
        )

    # 4. Validate required params against the manifest's params_schema
    params_schema = manifest.get("params_schema", {})
    required_keys = params_schema.get("required", [])
    for key in required_keys:
        if key not in params:
            return (
                False,
                FailureClass.VALIDATION,
                f"Missing required param '{key}' for tool '{tool}'",
            )

    return True, None, None


def reject_model_action(proposed_action: dict, failure_class: FailureClass, reason: str, actual_level: int = 0) -> tuple:
    """Helper to publish validation rejection event and return failure status."""
    bus = EventBus()
    bus.publish(
        topic="validation.rejected",
        payload={
            "proposed_action": proposed_action,
            "reason": reason,
            "outcome": "rejected"
        },
        provenance=proposed_action.get("provenance", "model_output"),
        permission_level=actual_level,
        failure_class=failure_class,
    )
    return False, failure_class, reason


def hold_for_confirmation(proposed_action: dict, manifest: dict) -> tuple:
    """Helper to publish confirmation hold, request native consent, and log approval/denial outcome."""
    tool = proposed_action.get("tool")
    description = manifest.get("description", "")
    params = proposed_action.get("params", {})
    actual_level = manifest.get("permission_level", 0)

    bus = EventBus()
    # 1. Log "held" status
    bus.publish(
        topic="validation.rejected",
        payload={
            "proposed_action": proposed_action,
            "reason": "model_proposed_high_risk_action",
            "outcome": "held"
        },
        provenance=proposed_action.get("provenance", "model_output"),
        permission_level=actual_level,
        failure_class=FailureClass.PERMISSION,
    )

    # 2. Call the real gatekeeper MessageBoxW dialog
    consent = request_consent(tool, description, params)

    # 3. Log final "approved" or "denied" decision
    bus.publish(
        topic="validation.rejected",
        payload={
            "proposed_action": proposed_action,
            "reason": "model_proposed_high_risk_action",
            "outcome": "approved" if consent else "denied"
        },
        provenance=proposed_action.get("provenance", "model_output"),
        permission_level=actual_level,
        failure_class=None if consent else FailureClass.PERMISSION,
    )

    if consent:
        return True, None, None
    else:
        return False, FailureClass.PERMISSION, "User declined consent dialog."


def validate_model_action(proposed_action: dict, registry, retries_so_far: int = 0) -> tuple:
    """
    Validate a proposed action originating from an AI model (provenance: model_output).

    Args:
        proposed_action: dict containing tool, params, claimed_permission_level, provenance
        registry: CapabilityRegistry instance
        retries_so_far: number of validation retries attempted for the same task

    Returns:
        (is_valid: bool, failure_class: FailureClass | None, reason: str | None)
    """
    # 1. Retry safety threshold checked first
    if retries_so_far >= 2:
        # Determine actual level if tool is valid, otherwise default to 0
        actual_level = 0
        try:
            manifest = registry.get_manifest(proposed_action.get("tool"))
            actual_level = manifest.get("permission_level", 0)
        except KeyError:
            pass
        return reject_model_action(proposed_action, FailureClass.BUG, "repeated_validation_failure_same_task", actual_level)

    tool = proposed_action.get("tool")
    
    # 2. Tool must exist in the registry
    try:
        manifest = registry.get_manifest(tool)
    except KeyError:
        return reject_model_action(proposed_action, FailureClass.VALIDATION, "unknown_tool", 0)

    # 3. Validate params against input schema
    params = proposed_action.get("params", {})
    params_schema = manifest.get("params_schema", {})
    required_keys = params_schema.get("required", [])
    for key in required_keys:
        if key not in params:
            return reject_model_action(proposed_action, FailureClass.VALIDATION, "bad_params", manifest["permission_level"])

    # 4. Model must declare claimed permission level
    claimed = proposed_action.get("claimed_permission_level")
    actual = manifest["permission_level"]

    if claimed is None:
        return reject_model_action(proposed_action, FailureClass.VALIDATION, "model_did_not_declare_permission_level", actual)

    # 5. Claimed permission level must match the manifest level exactly
    if claimed != actual:
        return reject_model_action(proposed_action, FailureClass.VALIDATION, f"model_claimed_level_{claimed}_actual_{actual}", actual)

    # 6. Level 4+ actions always hold for confirmation
    if actual >= 4:
        return hold_for_confirmation(proposed_action, manifest)

    return True, None, None

