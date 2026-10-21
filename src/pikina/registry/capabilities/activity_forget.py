"""activity.forget — delete every recorded action and app switch. Asks first (permission level 4)."""
from pikina.activity.store import ActivityStore


def run(params: dict) -> dict:
    deleted = ActivityStore().forget()
    return {"status": "ok", "deleted": deleted,
            "message": (f"Deleted {deleted['actions']} actions and {deleted['app_switches']} app switches. "
                        "Recording continues; say 'pause activity' to stop it.")}
