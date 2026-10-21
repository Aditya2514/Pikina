"""
ActivityStore — the learning layer's raw data, in its own file (activity.db).

Two tables:
  activity      one row per action run through Pikina: a normalised key such as
                "app.open:code", never the typed text or any personal content
  app_switches  one row per change of the focused app (opt-in tracker): the app
                name only, never a window title

Kept apart from the replay log (an audit trail) and from pikina.db (to-dos and
calendar), so "forget everything" is one DELETE and nothing else is affected.
Timestamps are UTC ISO strings with microseconds, so text comparison orders them.
"""
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pikina import paths

RETENTION_DAYS = 90
SESSION_GAP = timedelta(minutes=30)
OUTCOMES = ("ok", "error", "denied")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id            INTEGER PRIMARY KEY,
    ts            TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    hour          INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    weekday       INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    active_app    TEXT,
    session_id    TEXT    NOT NULL,
    outcome       TEXT    NOT NULL CHECK (outcome IN ('ok', 'error', 'denied')),
    tier          TEXT,
    group_id      TEXT,
    suggestion_id TEXT,
    origin_event  TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_activity_ts        ON activity(ts);
CREATE INDEX IF NOT EXISTS idx_activity_action_ts ON activity(action, ts);
CREATE INDEX IF NOT EXISTS idx_activity_session   ON activity(session_id, ts);

CREATE TABLE IF NOT EXISTS app_switches (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    app        TEXT NOT NULL,
    backend    TEXT,
    session_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_switches_ts ON app_switches(ts);

CREATE TABLE IF NOT EXISTS activity_meta (key TEXT PRIMARY KEY, value TEXT);
"""

_ready: set = set()
_ready_lock = threading.Lock()


def utc_iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def new_session_id() -> str:
    return "s-" + uuid.uuid4().hex[:10]


class ActivityStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.data_file(paths.ACTIVITY_DB)
        key = str(self.path)
        if key not in _ready:
            with _ready_lock:
                if key not in _ready:
                    self._init_db()
                    _ready.add(key)

    # ------------------------------------------------------------------
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self):
        """One IMMEDIATE transaction: the CLI and the server can both write without racing."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def _init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")  # tracker writes while the HUD reads
            except sqlite3.DatabaseError:
                pass
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Sessions: a new one starts after 30 minutes without any activity
    # (commands or app switches). State lives in the DB so the CLI and the
    # server agree.
    # ------------------------------------------------------------------
    @staticmethod
    def _meta(conn, key, default=None):
        row = conn.execute("SELECT value FROM activity_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    @staticmethod
    def _set_meta(conn, key, value) -> None:
        conn.execute("INSERT INTO activity_meta(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def _session_for(self, conn, ts: datetime) -> str:
        sid = self._meta(conn, "session_id")
        last = self._meta(conn, "session_last_ts")
        last_ts = parse_ts(last) if last else None
        if not sid or last_ts is None or ts - last_ts > SESSION_GAP:
            sid = new_session_id()
            self._set_meta(conn, "session_id", sid)
        if last_ts is None or ts > last_ts:
            self._set_meta(conn, "session_last_ts", utc_iso(ts))
        return sid

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def add_action(self, *, ts: datetime, action: str, source: str, outcome: str,
                   active_app: str | None = None, tier: str | None = None,
                   group_id: str | None = None, suggestion_id: str | None = None,
                   origin_event: str | None = None) -> int | None:
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        local = ts.astimezone()
        with self._write() as conn:
            sid = self._session_for(conn, ts)
            cur = conn.execute(
                "INSERT OR IGNORE INTO activity (ts, action, source, hour, weekday, active_app, session_id,"
                " outcome, tier, group_id, suggestion_id, origin_event) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (utc_iso(ts), action, source, local.hour, local.weekday(), active_app, sid,
                 outcome, tier, group_id, suggestion_id, origin_event))
            return cur.lastrowid if cur.rowcount else None

    def add_switch(self, *, ts: datetime, app: str, backend: str | None) -> None:
        with self._write() as conn:
            sid = self._session_for(conn, ts)
            conn.execute("INSERT INTO app_switches (ts, app, backend, session_id) VALUES (?,?,?,?)",
                         (utc_iso(ts), app, backend, sid))

    def add_backfill(self, rows: list[dict]) -> int:
        """Rows that already carry ts, session_id, hour, weekday. Duplicates (same origin_event) are skipped."""
        cols = ("ts", "action", "source", "hour", "weekday", "active_app", "session_id", "outcome",
                "tier", "group_id", "suggestion_id", "origin_event")
        with self._write() as conn:
            before = conn.total_changes
            conn.executemany(
                f"INSERT OR IGNORE INTO activity ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [tuple(r.get(c) for c in cols) for r in rows])
            return conn.total_changes - before

    def meta(self, key: str, default=None):
        with self._conn() as conn:
            return self._meta(conn, key, default)

    def set_meta(self, key: str, value: str) -> None:
        with self._write() as conn:
            self._set_meta(conn, key, value)

    # ------------------------------------------------------------------
    # Retention and deletion
    # ------------------------------------------------------------------
    def prune(self, now: datetime | None = None, days: int = RETENTION_DAYS) -> int:
        now = now or datetime.now(timezone.utc)
        cutoff = utc_iso(now - timedelta(days=days))
        with self._write() as conn:
            a = conn.execute("DELETE FROM activity WHERE ts < ?", (cutoff,)).rowcount
            s = conn.execute("DELETE FROM app_switches WHERE ts < ?", (cutoff,)).rowcount
            self._set_meta(conn, "last_prune", utc_iso(now))
        return a + s

    def forget(self) -> dict:
        """Delete every recorded action and app switch. Backfill is not re-run afterwards."""
        with self._write() as conn:
            a = conn.execute("DELETE FROM activity").rowcount
            s = conn.execute("DELETE FROM app_switches").rowcount
            conn.execute("DELETE FROM activity_meta WHERE key IN ('session_id', 'session_last_ts')")
            self._set_meta(conn, "backfilled_at", "forgotten")
        try:
            with self._conn() as conn:
                conn.execute("VACUUM")  # don't leave deleted rows readable in free pages
        except sqlite3.DatabaseError:
            pass
        return {"actions": a, "app_switches": s}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def recent(self, limit: int = 50, since: datetime | None = None) -> list[dict]:
        q, args = "SELECT * FROM activity", []
        if since:
            q += " WHERE ts >= ?"
            args.append(utc_iso(since))
        q += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(q, args)]

    def switches(self, limit: int = 50, since: datetime | None = None) -> list[dict]:
        q, args = "SELECT * FROM app_switches", []
        if since:
            q += " WHERE ts >= ?"
            args.append(utc_iso(since))
        q += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(q, args)]

    def stats(self, since: datetime | None = None) -> dict:
        where, args = ("WHERE ts >= ?", [utc_iso(since)]) if since else ("", [])
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM activity {where}", args).fetchone()[0]
            by_outcome = {r[0]: r[1] for r in conn.execute(
                f"SELECT outcome, COUNT(*) FROM activity {where} GROUP BY outcome", args)}
            by_source = {r[0]: r[1] for r in conn.execute(
                f"SELECT source, COUNT(*) FROM activity {where} GROUP BY source", args)}
            learnable = f"{where + ' AND' if where else 'WHERE'} outcome = 'ok' AND source != 'alias'"
            top = [{"action": r[0], "count": r[1]} for r in conn.execute(
                f"SELECT action, COUNT(*) AS n FROM activity {learnable} GROUP BY action "
                f"ORDER BY n DESC, action LIMIT 10", args)]
            sessions = conn.execute(f"SELECT COUNT(DISTINCT session_id) FROM activity {where}", args).fetchone()[0]
            switches = conn.execute(f"SELECT COUNT(*) FROM app_switches {where}", args).fetchone()[0]
            days = conn.execute(
                f"SELECT COUNT(DISTINCT substr(ts, 1, 10)) FROM activity {where}", args).fetchone()[0]
            first = conn.execute("SELECT MIN(ts) FROM activity").fetchone()[0]
            top_apps = [{"app": r[0], "count": r[1]} for r in conn.execute(
                f"SELECT app, COUNT(*) AS n FROM app_switches {where + ' AND' if where else 'WHERE'} "
                f"app NOT IN ('excluded', 'none') GROUP BY app ORDER BY n DESC, app LIMIT 10", args)]
        return {"actions": total, "by_outcome": by_outcome, "by_source": by_source, "top_actions": top,
                "sessions": sessions, "app_switches": switches, "top_apps": top_apps,
                "days_with_data": days, "first_action_at": first}

    def export(self) -> dict:
        with self._conn() as conn:
            return {
                "exported_at": utc_iso(datetime.now(timezone.utc)),
                "activity": [dict(r) for r in conn.execute("SELECT * FROM activity ORDER BY ts, id")],
                "app_switches": [dict(r) for r in conn.execute("SELECT * FROM app_switches ORDER BY ts, id")],
            }
