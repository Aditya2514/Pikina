"""activity.pause — pause or resume recording (commands and app switches) until you change it back."""
from pikina import settings


def parse(params: dict) -> dict:
    verb = str(params.pop("verb", "")).lower()
    params["paused"] = verb in ("pause", "stop")
    return params


def run(params: dict) -> dict:
    paused = params.get("paused")
    if not isinstance(paused, bool):
        return {"status": "error", "reason": "Say 'pause activity' or 'resume activity'."}
    try:
        settings.update({"activity_paused": paused})
    except settings.SettingsError as exc:
        return {"status": "error", "reason": str(exc)}
    except OSError as exc:
        return {"status": "error", "reason": f"Couldn't save settings ({exc.strerror or exc})."}
    if paused:
        return {"status": "ok", "paused": True,
                "message": "Activity log paused: nothing is recorded until you say 'resume activity'."}
    return {"status": "ok", "paused": False, "message": "Activity log resumed."}
