"""
Resolves a manifest's rollback recipe into a concrete (tool, params) call.

Manifest example (todo.add):
    "rollback_action": "todo.remove",
    "rollback_params": {"text": "$result.todo.id"}

"$params.x.y" reads from the original call's params, "$result.x.y" from its result.
"""


class RollbackSpecError(ValueError):
    pass


def _lookup(source: dict, dotted: str):
    cur = source
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise RollbackSpecError(f"rollback value '{dotted}' not available")
        cur = cur[part]
    return cur


def resolve_rollback_params(spec: dict, params: dict, result: dict) -> dict:
    resolved = {}
    for key, value in (spec or {}).items():
        if isinstance(value, str) and value.startswith("$params."):
            resolved[key] = _lookup(params or {}, value[len("$params."):])
        elif isinstance(value, str) and value.startswith("$result."):
            resolved[key] = _lookup(result or {}, value[len("$result."):])
        else:
            resolved[key] = value
        if resolved[key] is None:
            raise RollbackSpecError(f"rollback value for '{key}' is empty")
    return resolved
