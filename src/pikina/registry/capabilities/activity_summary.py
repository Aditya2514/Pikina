"""activity.summary — what the activity log has recorded, so you can check the data is clean."""
from datetime import datetime, time, timedelta

from pikina import settings
from pikina.activity.keys import alias_hash, project_hash
from pikina.activity.store import RETENTION_DAYS, ActivityStore, parse_ts


def parse(params: dict) -> dict:
    rng = " ".join(str(params.pop("range", "") or "").lower().split())
    params["days"] = 7 if "week" in rng else 1
    return params


def _since(days: int) -> datetime:
    start = datetime.combine(datetime.now().date(), time.min).astimezone()
    return start - timedelta(days=max(days, 1) - 1)


def _alias_names() -> dict:
    """alias.run:<hash> / project.open:<hash> -> a readable name, for what you have now (display only)."""
    names = {}
    try:
        from pikina.router.alias_resolver import AliasResolver
        names.update({f"alias.run:{alias_hash(a.get('trigger'))}": f'alias "{a.get("trigger")}"'
                      for a in AliasResolver().list_aliases()})
    except Exception:
        pass
    try:
        from pikina import projects
        names.update({f"project.open:{project_hash(n)}": f'project "{n}"' for n in projects.load()})
    except Exception:
        pass
    return names


def _state() -> str:
    """What is really being recorded (the same strict rules the recorder uses)."""
    from pikina.activity import recorder
    f = recorder.flags()
    if not f["settings_ok"]:
        return "Activity log: PAUSED — settings.json can't be read; fix or delete it to resume"
    if f["paused"]:
        return "Activity log: PAUSED (say 'resume activity' to continue)"
    data, _ok = settings.read_checked()
    log = "ON" if f["logging"] else "OFF"
    win = "ON" if data.get("window_tracking", False) is True else "OFF"
    return f"Activity log: {log} · active-app tracking: {win}"


def run(params: dict) -> dict:
    try:
        days = max(1, min(int(params.get("days", 1)), RETENTION_DAYS))
    except (TypeError, ValueError):
        days = 1
    store = ActivityStore()
    since = _since(days)
    stats = store.stats(since=since)
    label = "Today" if days == 1 else f"Last {days} days"
    names = _alias_names()
    show = lambda action: names.get(action, action)  # noqa: E731
    lines = [_state()]
    if not stats["actions"] and not stats["app_switches"]:
        lines.append(f"{label}: nothing recorded yet.")
    else:
        o = stats["by_outcome"]
        lines.append(f"{label}: {stats['actions']} actions in {stats['sessions']} session"
                     f"{'s' if stats['sessions'] != 1 else ''} — {o.get('ok', 0)} ok, "
                     f"{o.get('error', 0)} errors, {o.get('denied', 0)} denied")
        if stats["top_actions"]:
            lines.append("Most done: " + " · ".join(f"{show(a['action'])} ×{a['count']}" for a in stats["top_actions"][:5]))
        if stats["top_apps"]:
            lines.append("Apps: " + " · ".join(f"{a['app']} ×{a['count']}" for a in stats["top_apps"][:5]))
        recent = store.recent(limit=8, since=since)
        if recent:
            lines.append("Latest:")
            for r in recent:
                when = parse_ts(r["ts"]).astimezone().strftime("%H:%M")
                lines.append(f"  {when}  {show(r['action'])}  ({r['source']}, {r['outcome']})")
    overall = store.stats()
    if overall["first_action_at"]:
        first = parse_ts(overall["first_action_at"]).astimezone().strftime("%d %b")
        lines.append(f"Collecting since {first}: {overall['days_with_data']} day"
                     f"{'s' if overall['days_with_data'] != 1 else ''} with data, kept {RETENTION_DAYS} days.")
    return {"status": "ok", "days": days, "stats": stats, "message": "\n".join(lines)}
