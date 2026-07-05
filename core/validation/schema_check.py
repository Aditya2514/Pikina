"""
Action Validation Layer — schema check before any model-proposed action runs.
Nothing a model proposes reaches a tool without passing through here first.
"""
from .failure_classes import FailureClass


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
