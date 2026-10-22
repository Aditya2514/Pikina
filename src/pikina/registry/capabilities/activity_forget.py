"""activity.forget — delete every recorded action and app switch. Asks first (permission level 4)."""
from pikina.activity.store import ActivityStore


def run(params: dict) -> dict:
    deleted = ActivityStore().forget()
    note = "" if deleted.get("compacted", True) else (
        " Another program still has activity.db open, so the space is reclaimed once it closes.")
    return {"status": "ok", "deleted": deleted,
            "message": (f"Deleted {deleted['actions']} actions and {deleted['app_switches']} app switches."
                        f"{note} Recording continues; say 'pause activity' to stop it.")}
