"""
record(): the one call that turns an executed action into an activity row.

Called from the shared executor (every capability run), from alias runs and from
the Tier 2 consent decline. It never raises and never slows a command down by
more than one small SQLite insert: a logging failure must not fail an action.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from pikina import settings
from pikina.activity.keys import action_key
from pikina.activity.store import ActivityStore

# Where a command came from. "typed" = the dashboard's command bar.
SOURCES = {"typed", "quick_panel", "cli", "alias", "ui", "suggestion", "backfill"}
REQUEST_ORIGINS = {"typed", "quick_panel"}  # what the desktop app may claim over HTTP

_OK = {"ok", "success"}
_PRUNE_EVERY = 24 * 3600
_last_prune_check = {"at": None}
_prune_lock = threading.Lock()

# The window tracker registers a callable that returns the app you were in
# (never Pikina itself), or None. Without a tracker it stays None.
_active_app_provider = None


def set_active_app_provider(fn) -> None:
    global _active_app_provider
    _active_app_provider = fn


def current_active_app() -> str | None:
    fn = _active_app_provider
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def outcome_of(result) -> str:
    """ok / denied / error. Anything that isn't clearly ok (incl. 'ambiguous') isn't learned as a habit."""
    status = result.get("status") if isinstance(result, dict) else None
    if status in _OK:
        return "ok"
    if status == "denied":
        return "denied"
    return "error"


def is_enabled() -> bool:
    s = settings.read()
    return bool(s.get("activity_logging", True)) and not bool(s.get("activity_paused", False))


def maybe_prune(store: ActivityStore) -> None:
    """Delete rows past the retention window, at most once a day (checked at most hourly)."""
    now = time.monotonic()
    last_check = _last_prune_check["at"]
    if last_check is not None and now - last_check < 3600:
        return
    with _prune_lock:
        last_check = _last_prune_check["at"]
        if last_check is not None and now - last_check < 3600:
            return
        _last_prune_check["at"] = now
        last = store.meta("last_prune")
        try:
            due = not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(seconds=_PRUNE_EVERY)
        except ValueError:
            due = True
        if due:
            store.prune()


def record(tool: str, params: dict | None, result=None, *, source: str = "typed", tier=None,
           outcome: str | None = None, origin_event: str | None = None, group_id: str | None = None,
           ts: datetime | None = None, action: str | None = None) -> None:
    try:
        if not is_enabled():
            return
        ts = ts or datetime.now(timezone.utc)
        source = source if source in SOURCES else "typed"
        store = ActivityStore()
        store.add_action(
            ts=ts,
            action=action or action_key(tool, params, result if isinstance(result, dict) else None, ts),
            source=source,
            outcome=outcome or outcome_of(result),
            active_app=current_active_app(),
            tier=None if tier is None else str(tier),
            group_id=group_id,
            origin_event=origin_event,
        )
        maybe_prune(store)
    except Exception as exc:  # never let the log break the command
        print(f"[Activity] Not recorded: {type(exc).__name__}")
