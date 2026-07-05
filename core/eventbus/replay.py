"""
Replay Store — append-only SQLite table for event audit and debugging.
Retention: 30 days default. Level 4+ events retained indefinitely.
The "Time Machine" feature is just a query against this table.
"""
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "replay_store.sqlite"


class ReplayStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id               TEXT PRIMARY KEY,
                    topic            TEXT NOT NULL,
                    timestamp        TEXT NOT NULL,
                    provenance       TEXT NOT NULL,
                    payload          TEXT NOT NULL,
                    failure_class    TEXT,
                    permission_level INTEGER DEFAULT 0,
                    retain_forever   INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_topic     ON events(topic)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)")
            conn.commit()

    def append(self, event: dict, permission_level: int = 0) -> None:
        """Append an event. Level 4+ events are retained indefinitely."""
        retain = 1 if permission_level >= 4 else 0
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO events
                    (id, topic, timestamp, provenance, payload,
                     failure_class, permission_level, retain_forever)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event["id"],
                event["topic"],
                event["timestamp"],
                event["provenance"],
                json.dumps(event.get("payload", {})),
                event.get("failure_class"),
                permission_level,
                retain,
            ))
            conn.commit()

    def query(self, since_minutes: int = 30, topic: str = None, limit: int = 200) -> list:
        """Return events from the last N minutes, optionally filtered by topic."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
        sql    = "SELECT * FROM events WHERE timestamp >= ?"
        params = [cutoff]
        if topic:
            sql += " AND topic = ?"
            params.append(topic)
        sql += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def prune(self) -> int:
        """Delete events older than 30 days, except retained ones. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM events WHERE timestamp < ? AND retain_forever = 0",
                (cutoff,)
            )
            conn.commit()
            return cur.rowcount

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d
