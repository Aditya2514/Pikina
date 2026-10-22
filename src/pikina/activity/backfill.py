"""
Backfill: rebuild activity rows from the replay (audit) log, so learning doesn't
start from zero. Runs once, automatically, when the backend starts with the
activity log on; `pikina activity backfill` re-runs it (idempotent: each row
remembers the replay event it came from).

It only imports history from BEFORE live recording began (activity_meta
"live_since"). From then on the live recorder is the source of truth, and what it
skipped on purpose — while paused or switched off, rollbacks, commands that view
or control the log — stays skipped. Nothing older than the 90-day window or than
your last "forget my activity" is ever imported.

Only normalised keys are extracted. The replay log's raw text, parameters and
results (file paths, clipboard text) are read in memory and never copied.
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


def quiet_tools() -> set:
    """Tools whose manifest says "activity_log": false (core and plugins), read without importing handlers."""
    from pikina import plugins
    from pikina.registry import loader
    folders = [loader._CAPABILITIES_DIR]
    try:
        found, _errors, _disabled = plugins.discover()
        folders += [p.capabilities_dir for p in found]
    except Exception:
        pass
    out = set()
    for folder in folders:
        for f in Path(folder).glob("*.json"):
            try:
                manifest = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(manifest, dict) and manifest.get("activity_log") is False and manifest.get("tool"):
                out.add(manifest["tool"])
    return out


def _open_readonly(path: Path) -> sqlite3.Connection:
    # A plain path (not a file: URI) also works for network shares and mapped drives on Windows.
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA query_only=ON")
    return conn


def backfill(store: ActivityStore | None = None, replay_path: Path | None = None,
             now: datetime | None = None, force: bool = False, skip_tools: set | None = None) -> dict:
    store = store or ActivityStore()
    now = now or datetime.now(timezone.utc)
    if not force and store.meta("backfilled_at"):
        return {"status": "skipped", "reason": "already backfilled", "added": 0}
    if not recorder.is_enabled():
        return {"status": "skipped", "reason": "the activity log is off or paused", "added": 0}

    epoch = store.meta("forget_epoch") or "0"
    since = utc_iso(now - timedelta(days=RETENTION_DAYS))
    floor = store.meta("no_backfill_before")
    if floor and floor > since:
        since = floor
    until = store.meta("live_since") or utc_iso(now)

    path = Path(replay_path) if replay_path else paths.data_file(paths.REPLAY_DB)
    rows: list = []
    if path.exists() and since < until:
        rows = _rows_from_replay(path, since, until, quiet_tools() if skip_tools is None else skip_tools)
        if rows is None:
            return {"status": "error", "reason": "couldn't read the replay log", "added": 0}
    added = store.add_backfill(rows, forget_epoch=epoch, done_at=utc_iso(now))
    if added is None:
        return {"status": "skipped", "reason": "your activity was forgotten while importing", "added": 0}
    return {"status": "ok", "added": added, "found": len(rows)}


def _rows_from_replay(path: Path, since: str, until: str, skip: set) -> list[dict] | None:
    try:
        conn = _open_readonly(path)
        try:
            events = conn.execute(
                f"SELECT id, topic, timestamp, payload FROM events WHERE topic IN ({','.join('?' * len(_TOPICS))}) "
                "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp, rowid", (*_TOPICS, since, until)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    by_exec: dict = {}             # router.executing id -> entry (results carry exec_id since Phase 1)
    pending = defaultdict(deque)   # (tool, tier) -> executions waiting for their result (older logs)
    open_aliases: dict = {}        # alias raw text -> latest run
    aliases: list = []
    out: list = []

    def finish(entry, status_ok: bool, outcome: str, result=None):
        if entry.get("alias") is not None:
            entry["alias"]["steps"].append((status_ok, outcome))
        if entry["source"] is None or entry["tool"] in skip:
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
                by_exec[event_id] = entry
                pending[(entry["tool"], tier)].append(entry)

        elif topic == "router.result":
            result = p.get("result")
            entry = None
            if p.get("exec_id"):                      # exact pairing
                entry = by_exec.pop(p["exec_id"], None)
                if entry is not None:
                    try:
                        pending[(entry["tool"], entry["tier"])].remove(entry)
                    except ValueError:
                        pass
            else:                                     # older logs: newest open execution of that tool
                queue = pending.get((p.get("tool"), str(p.get("tier", 1))))
                if queue:
                    entry = queue.pop()
                    by_exec.pop(entry["id"], None)
                    for orphan in queue:              # earlier ones never got a result: drop them
                        by_exec.pop(orphan["id"], None)
                    queue.clear()
            if entry is not None:
                status = result.get("status") if isinstance(result, dict) else None
                if status == "no_match":       # not an action of yours (live logging skips it too)
                    continue
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
        row.update(session_id=sid, hour=local.hour, weekday=local.weekday(), day=local.date().isoformat(),
                   ts=utc_iso(row["ts"]), active_app=None, suggestion_id=None)
    return out


def scrub_replay_log(path: Path | None = None) -> int:
    """
    The first Phase 1 build wrote the full reply of commands like "activity" (your top apps
    and actions) into the replay log. Reduce those stored replies to their status, once.
    Returns how many events were rewritten.
    """
    path = Path(path) if path else paths.data_file(paths.REPLAY_DB)
    if not path.exists():
        return 0
    quiet = quiet_tools()
    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.Error:
        return 0
    changed = 0
    try:
        conn.execute("PRAGMA secure_delete=ON")  # don't leave the old text in free pages
        rows = conn.execute("SELECT id, payload FROM events WHERE topic = 'router.result' "
                            "AND payload LIKE '%\"activity.%'").fetchall()
        with conn:
            for event_id, payload in rows:
                try:
                    p = json.loads(payload or "{}")
                except ValueError:
                    continue
                result = p.get("result")
                if p.get("tool") in quiet and isinstance(result, dict) and set(result) - {"status"}:
                    p["result"] = {"status": result.get("status")}
                    conn.execute("UPDATE events SET payload = ? WHERE id = ?", (json.dumps(p), event_id))
                    changed += 1
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return changed
