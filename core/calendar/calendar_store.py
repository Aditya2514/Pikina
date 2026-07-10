import sqlite3
import os
import uuid
from datetime import datetime, timezone

DB_PATH = "data/pikina.db"

class CalendarStore:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time TEXT,
                    type TEXT DEFAULT 'personal',
                    source TEXT DEFAULT 'user',
                    recurring TEXT DEFAULT 'none',
                    description TEXT,
                    linked_reminder_task TEXT
                )
            """)
            conn.commit()
            
            # Simple migration to add description column if database was initialized in an older phase
            try:
                conn.execute("ALTER TABLE calendar_events ADD COLUMN description TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass # Already exists

    def add_event(self, title: str, date: str, time: str = None, event_type: str = "personal", source: str = "user", recurring: str = "none", description: str = "") -> dict:
        title = title.strip()
        date = date.strip()
        desc = (description or "").strip()
        if not title:
            return {"status": "error", "reason": "Event title cannot be empty."}
        if not date:
            return {"status": "error", "reason": "Event date cannot be empty."}

        event_id = str(uuid.uuid4())[:8]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO calendar_events (id, title, date, time, type, source, recurring, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, title, date, time, event_type, source, recurring, desc)
            )
            conn.commit()

        return {
            "status": "ok",
            "event": {
                "id": event_id,
                "title": title,
                "date": date,
                "time": time,
                "type": event_type,
                "source": source,
                "recurring": recurring,
                "description": desc
            }
        }

    def remove_event(self, text_or_id: str) -> dict:
        text_or_id = text_or_id.strip()
        if not text_or_id:
            return {"status": "error", "reason": "Search query cannot be empty."}

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # 1. Try exact ID match
            c.execute("SELECT * FROM calendar_events WHERE id = ?", (text_or_id,))
            match = c.fetchone()
            if match:
                conn.execute("DELETE FROM calendar_events WHERE id = ?", (match["id"],))
                conn.commit()
                return {"status": "ok", "message": f"Event '{match['title']}' removed."}

            # 2. Try substring match
            c.execute("SELECT * FROM calendar_events WHERE title LIKE ?", (f"%{text_or_id}%",))
            matches = c.fetchall()

            if not matches:
                return {"status": "error", "reason": f"No event found matching '{text_or_id}'."}

            if len(matches) > 1:
                candidates = [{"id": r["id"], "title": r["title"], "date": r["date"]} for r in matches]
                return {
                    "status": "ambiguous",
                    "reason": f"Multiple events match '{text_or_id}'. Please specify the ID or a more specific title.",
                    "candidates": candidates
                }

            # Exactly one match
            match = matches[0]
            conn.execute("DELETE FROM calendar_events WHERE id = ?", (match["id"],))
            conn.commit()
            return {"status": "ok", "message": f"Event '{match['title']}' removed."}

    def query_date(self, date_str: str) -> list:
        # Match exact date YYYY-MM-DD or yearly recurring match (--MM-DD)
        # Recurring formats can check the last 5 characters
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            # Fetch all events and filter in memory or SQL. Simple SQL query:
            c.execute("""
                SELECT * FROM calendar_events 
                WHERE date = ? OR (recurring = 'yearly' AND substr(date, -5) = substr(?, -5))
                ORDER BY time ASC
            """, (date_str, date_str))
            return [dict(r) for r in c.fetchall()]

    def query_range(self, start_date: str, end_date: str) -> list:
        # Range queries are simpler: list events between start and end dates
        # Note: Yearly recurring events within this range need to be expanded.
        # Since this is a simple python helper, we can parse dates:
        from datetime import datetime, timedelta
        
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Load all events
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM calendar_events")
            all_events = [dict(r) for r in c.fetchall()]

        expanded_events = []
        for ev in all_events:
            ev_date_str = ev["date"]
            if ev["recurring"] == "yearly":
                # Check each year between start and end years
                # For yearly event, check if date exists in the range
                try:
                    # Parse event date (month and day)
                    ev_md = datetime.strptime(ev_date_str, "%Y-%m-%d")
                    # Check years from start.year to end.year
                    for yr in range(start.year, end.year + 1):
                        # Construct date for that year
                        # Handle leap years safely
                        try:
                            check_date = ev_md.replace(year=yr).date()
                            if start <= check_date <= end:
                                ev_copy = dict(ev)
                                # Override event date to the actual date in this range
                                ev_copy["date"] = check_date.isoformat()
                                expanded_events.append(ev_copy)
                        except ValueError:
                            pass # Leap day on non-leap year
                except Exception:
                    pass
            else:
                try:
                    ev_date = datetime.strptime(ev_date_str, "%Y-%m-%d").date()
                    if start <= ev_date <= end:
                        expanded_events.append(ev)
                except Exception:
                    pass

        # Sort expanded events by date then time
        expanded_events.sort(key=lambda x: (x["date"], x["time"] or "00:00"))
        return expanded_events

    def update_event(self, event_id: str, title: str, date_str: str, time_str: str = None, event_type: str = "personal", source: str = "user", recurring: str = "none", description: str = "") -> dict:
        event_id = event_id.strip()
        title = title.strip()
        date_str = date_str.strip()
        desc = (description or "").strip()
        
        if not event_id:
            return {"status": "error", "reason": "Event ID cannot be empty."}
        if not title:
            return {"status": "error", "reason": "Event title cannot be empty."}
        if not date_str:
            return {"status": "error", "reason": "Event date cannot be empty."}

        # Check if time_str is empty string, convert to None
        if time_str and not time_str.strip():
            time_str = None
            
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE calendar_events 
                SET title = ?, date = ?, time = ?, type = ?, source = ?, recurring = ?, description = ?
                WHERE id = ?
                """,
                (title, date_str, time_str, event_type, source, recurring, desc, event_id)
            )
            conn.commit()
            
        return {"status": "ok", "message": f"Event '{title}' updated successfully."}
