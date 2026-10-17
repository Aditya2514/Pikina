def run(params: dict) -> dict:
    action = params.get("action", "").strip().lower()
    if action not in ("start", "pause", "reset"):
        return {"status": "error", "reason": "Invalid action. Use start, pause, or reset."}
        
    return {
        "status": "ok",
        "tool": "utility.stopwatch",
        "action": action,
        "message": {"start": "Stopwatch started.", "pause": "Stopwatch paused.", "reset": "Stopwatch reset."}[action]
    }
