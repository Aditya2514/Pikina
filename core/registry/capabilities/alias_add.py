from core.router.alias_resolver import AliasResolver

def run(params: dict) -> dict:
    trigger = params.get("trigger", "").strip()
    steps   = params.get("steps", [])

    if not trigger:
        return {"status": "error", "reason": "Missing required param: 'trigger'"}
    if not steps or not isinstance(steps, list):
        return {"status": "error", "reason": "Missing required param: 'steps' (must be a non-empty list)"}

    # Validate each step has tool + params keys
    for i, step in enumerate(steps):
        if "tool" not in step or "params" not in step:
            return {"status": "error", "reason": f"Step {i+1} is missing 'tool' or 'params' key."}

    resolver = AliasResolver()
    return resolver.add_alias(trigger, steps)
