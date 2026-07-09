from core.router.alias_resolver import AliasResolver

def run(params: dict) -> dict:
    resolver = AliasResolver()
    aliases  = resolver.list_aliases()

    if not aliases:
        return {"status": "ok", "aliases": [], "message": "No aliases defined yet. Use 'add alias' to create one."}

    lines = []
    for a in aliases:
        tools = " → ".join(s["tool"] for s in a.get("steps", []))
        lines.append(f"  '{a['trigger']}' → {tools}")

    return {
        "status":  "ok",
        "aliases": aliases,
        "count":   len(aliases),
        "display": "\n".join(lines),
    }
