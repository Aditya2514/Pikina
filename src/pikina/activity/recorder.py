"""
record(): the one call that turns an executed action into an activity row.

Called from the shared executor (every capability run), from alias runs and from
the Tier 2 consent decline. It never raises, and it waits at most HOT_TIMEOUT for
the database (if something else holds a write lock, the row is skipped instead of
stalling the command): a logging failure must not fail or slow an action.

Privacy switches fail closed: if settings.json can't be parsed, nothing is recorded
until it's fixed (a typo must never silently lift a pause).
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
HOT_TIMEOUT = 0.5  # seconds the command thread may wait for activity.db
_PRUNE_EVERY = 24 * 3600
_last_prune_check = {"at": None}
_prune_lock = threading.Lock()

# The window tracker registers a callable that returns the app you were in
# (never Pikina itself), or None. Without a tracker it stays None.
_active_app_provider = None


def set_active_app_provider(fn) -> None:
    global _active_app_provider
    _active_app_provider = fn


def clear_active_app_provider(fn) -> None:
    """Unhook fn only if it is still the provider (a newer tracker may have replaced it)."""
    global _active_app_provider
    if _active_app_provider == fn:
        _active_app_provider = None


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


def flags() -> dict:
    """
    The effective switches. Only a real true/false counts: "activity_logging": "false"
    is off, any non-false "activity_paused" is paused, and an unreadable settings.json
    means logging off and paused until it's fixed.
    """
    data, ok = settings.read_checked()
    if not ok:
        return {"logging": False, "paused": True, "settings_ok": False}
    return {"logging": data.get("activity_logging", True) is True,
            "paused": data.get("activity_paused", False) is not False,
            "settings_ok": True}


def is_enabled() -> bool:
    """Are commands being recorded?"""
    f = flags()
    return f["logging"] and not f["paused"]


def is_paused() -> bool:
    """Is all recording (commands and app switches) paused? The app tracker only checks this."""
    return flags()["paused"]


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
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last) if last else None
            # a last_prune in the future (clock went backwards) also counts as due
            due = age is None or age > timedelta(seconds=_PRUNE_EVERY) or age < timedelta(0)
        except ValueError:
            due = True
        if due:
            store.prune()


def record(tool: str, params: dict | None, result=None, *, source: str = "typed", tier=None,
           outcome: str | None = None, origin_event: str | None = None, group_id: str | None = None,
           ts: datetime | None = None, action: str | None = None) -> None:
    try:
        # Opening the store first marks when live recording began, even while paused or
        # off, so a later backfill never imports what you did during that time.
        ts = ts or datetime.now(timezone.utc)
        store = ActivityStore(timeout=HOT_TIMEOUT, live_since=ts)
        if not is_enabled():
            return
        source = source if source in SOURCES else "typed"
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
