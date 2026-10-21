"""
Backfill: rebuild activity rows from the replay (audit) log, so learning doesn't
start from zero. Runs once, automatically, when the backend first starts with
the activity log on; `pikina activity backfill` re-runs it (it's idempotent:
each row remembers the replay event it came from).

Only normalised keys are extracted. The replay log's raw text, parameters and
results (file paths, clipboard text) are read in memory and never copied.

What's skipped: rollbacks (an undo isn't something you chose), actions whose
outcome was never logged, and anything older than the 90-day retention window.
Backfilled rows can't tell the dashboard from the quick panel, so their source
is "backfill" (alias steps stay "alias", calendar clicks "ui").
"""
import json
import re
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pikina import paths
from pikina.activity import recorder
from pikina.activity.keys import action_key
from pikina.activity.store import RETENTION_DAYS, SESSION_GAP, ActivityStore, new_session_id, parse_ts, utc_iso

_STEP = re.compile(r"^(?P<base>.*) \[alias step \d+/\d+\]$", re.S)
_ROLLBACK = re.compile(r"^rollback of \S+ \(alias '.*'\)$", re.S)
_TOPICS = ("alias.started", "router.executing", "router.result", "router.denied", "validation.rejected")


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10.0)


def backfill(store: ActivityStore | None = None, replay_path: Path | None = None,
             now: datetime | None = None, force: bool = False) -> dict:
    store = store or ActivityStore()
    now = now or datetime.now(timezone.utc)
    if not force and store.meta("backfilled_at"):
        return {"status": "skipped", "reason": "already backfilled", "added": 0}
    if not recorder.is_enabled():
        return {"status": "skipped", "reason": "the activity log is off or paused", "added": 0}

    path = Path(replay_path) if replay_path else paths.data_file(paths.REPLAY_DB)
    rows = _rows_from_replay(path, now) if path.exists() else []
    added = store.add_backfill(rows) if rows else 0
    store.set_meta("backfilled_at", utc_iso(now))
    return {"status": "ok", "added": added, "found": len(rows)}


def _rows_from_replay(path: Path, now: datetime) -> list[dict]:
    cutoff = utc_iso(now - timedelta(days=RETENTION_DAYS))
    try:
        conn = _open_readonly(path)
    except sqlite3.Error:
        return []
    try:
        events = conn.execute(
            f"SELECT id, topic, timestamp, payload FROM events WHERE topic IN ({','.join('?' * len(_TOPICS))}) "
            "AND timestamp >= ? ORDER BY timestamp, rowid", (*_TOPICS, cutoff)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    pending = defaultdict(deque)   # (tool, tier) -> executions waiting for their result
    open_aliases: dict = {}        # alias raw text -> latest run
    aliases: list = []
    out: list = []

    def finish(entry, status_ok: bool, outcome: str, result=None):
        if entry.get("alias") is not None:
            entry["alias"]["steps"].append((status_ok, outcome))
        if entry["source"] is None:
            return
        ts = entry["ts"]
        slim = {"launched": result.get("launched")} if isinstance(result, dict) and result.get("launched") else None
        out.append({"ts": ts, "action": action_key(entry["tool"], entry["params"], slim, ts),
                    "source": entry["source"], "outcome": outcome, "tier": entry["tier"],
                    "group_id": entry["group"], "origin_event": entry["id"]})

    for event_id, topic, stamp, payload in events:
        try:
            p = json.loads(payload or "{}")
            ts = parse_ts(stamp)
        except (ValueError, TypeError):
            continue
        if not isinstance(p, dict):
            continue

        if topic == "alias.started":
            run = {"id": event_id, "ts": ts, "trigger": p.get("trigger"), "n": p.get("steps"),
                   "group": "a-" + str(event_id).replace("-", "")[:10], "steps": []}
            open_aliases[str(p.get("raw", ""))] = run
            aliases.append(run)
            continue

        if topic in ("router.executing", "router.denied"):
            raw = str(p.get("raw") or "")
            tier = str(p.get("tier", 1))
            run, group = None, None
            if _ROLLBACK.match(raw):
                source = None
            elif (m := _STEP.match(raw)):
                run = open_aliases.get(m.group("base"))
                source, group = "alias", (run["group"] if run else None)
            elif tier == "api" or raw.startswith("[api] "):
                source = "ui"
            else:
                source = "backfill"
            entry = {"id": event_id, "ts": ts, "tool": p.get("tool"), "params": p.get("params") or {},
                     "tier": tier, "source": source, "group": group, "alias": run}
            if topic == "router.denied":
                finish(entry, False, "denied")
            else:
                pending[(entry["tool"], tier)].append(entry)

        elif topic == "router.result":
            queue = pending.get((p.get("tool"), str(p.get("tier", 1))))
            if queue:
                entry = queue.popleft()
                result = p.get("result")
                status = result.get("status") if isinstance(result, dict) else None
                finish(entry, status not in ("error", "denied", None), recorder.outcome_of(result), result)

        elif topic == "validation.rejected" and p.get("outcome") == "denied":
            proposed = p.get("proposed_action") or {}
            finish({"id": event_id, "ts": ts, "tool": proposed.get("tool"), "params": proposed.get("params") or {},
                    "tier": "2", "source": "backfill", "group": None, "alias": None}, False, "denied")

    for run in aliases:
        steps, n = run["steps"], run["n"]
        if steps and isinstance(n, int) and len(steps) == n and all(ok for ok, _ in steps):
            outcome = "ok"
        elif any(o == "denied" for _, o in steps):
            outcome = "denied"
        else:
            outcome = "error"
        out.append({"ts": run["ts"], "action": action_key("alias.run", {"trigger": run["trigger"]}),
                    "source": "backfill", "outcome": outcome, "tier": "1", "group_id": run["group"],
                    "origin_event": run["id"]})

    out.sort(key=lambda r: r["ts"])
    sid, last = None, None
    for row in out:
        if last is None or row["ts"] - last > SESSION_GAP:
            sid = new_session_id()
        last = row["ts"]
        local = row["ts"].astimezone()
        row.update(session_id=sid, hour=local.hour, weekday=local.weekday(),
                   ts=utc_iso(row["ts"]), active_app=None, suggestion_id=None)
    return out
