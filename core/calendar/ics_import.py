import os
import sqlite3
from icalendar import Calendar
from datetime import datetime, date
from core.calendar.calendar_store import CalendarStore

def import_ics(filepath: str) -> dict:
    store = CalendarStore()
    if not os.path.exists(filepath):
        return {"status": "error", "reason": f"File '{filepath}' not found."}
        
    try:
        with open(filepath, 'rb') as f:
            gcal = Calendar.from_ical(f.read())
    except Exception as e:
        return {"status": "error", "reason": f"Failed to parse .ics file: {e}"}

    added = 0
    errors = 0
    for component in gcal.walk():
        if component.name == "VEVENT":
            summary = component.get("summary")
            dtstart = component.get("dtstart")
            
            if not summary or not dtstart:
                errors += 1
                continue
                
            dt = dtstart.dt
            event_date = ""
            event_time = None
            
            if isinstance(dt, datetime):
                event_date = dt.date().isoformat()
                event_time = dt.strftime("%H:%M")
            elif isinstance(dt, date):
                event_date = dt.isoformat()
                
            title = str(summary)
            
            # Simple deduplication check
            with sqlite3.connect(store.db_path) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute(
                    "SELECT id FROM calendar_events WHERE title = ? AND date = ? AND (time = ? OR (time IS NULL AND ? IS NULL))",
                    (title, event_date, event_time, event_time)
                )
                if c.fetchone():
                    continue  # Already exists
                    
            store.add_event(
                title=title,
                date=event_date,
                time=event_time,
                event_type="college",
                source="ics_import",
                recurring="none"
            )
            added += 1

    return {"status": "ok", "added_count": added, "skipped_errors": errors}
