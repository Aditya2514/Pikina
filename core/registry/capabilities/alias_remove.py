from core.router.alias_resolver import AliasResolver

def run(params: dict) -> dict:
    trigger = params.get("trigger", "").strip()

    if not trigger:
        return {"status": "error", "reason": "Missing required param: 'trigger'"}

    resolver = AliasResolver()
    return resolver.remove_alias(trigger)
