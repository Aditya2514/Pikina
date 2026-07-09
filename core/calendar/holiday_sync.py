import sqlite3
import holidays
from datetime import date
from core.calendar.calendar_store import CalendarStore

def sync_year(year: int, country: str = "IN") -> dict:
    store = CalendarStore()
    try:
        country_holidays = holidays.country_holidays(country, years=year)
    except Exception as e:
        return {"status": "error", "reason": f"Failed to load holidays: {e}"}

    # Clean existing holiday_lib events for this year to avoid duplicates
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "DELETE FROM calendar_events WHERE source = 'holiday_lib' AND date LIKE ?",
            (f"{year}-%",)
        )
        conn.commit()

    added = 0
    for dt, name in sorted(country_holidays.items()):
        store.add_event(
            title=name,
            date=dt.isoformat(),
            time=None,
            event_type="holiday",
            source="holiday_lib",
            recurring="none"
        )
        added += 1

    return {"status": "ok", "synced_year": year, "added_count": added}
