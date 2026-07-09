from datetime import datetime
from core.calendar.calendar_store import CalendarStore

def run(params: dict) -> dict:
    date_str   = params.get("date")
    start_date = params.get("start_date")
    end_date   = params.get("end_date")

    store = CalendarStore()

    if start_date and end_date:
        try:
            import dateparser
            sd_dt = dateparser.parse(start_date)
            ed_dt = dateparser.parse(end_date)
            if sd_dt and ed_dt:
                start_date = sd_dt.date().isoformat()
                end_date   = ed_dt.date().isoformat()
        except ImportError:
            pass

        events = store.query_range(start_date, end_date)
        display = f"Events from {start_date} to {end_date}:"
        if not events:
            display += "\n  No events scheduled."
        else:
            for ev in events:
                time_str = f" at {ev['time']}" if ev.get("time") else " (All day)"
                type_str = f" [{ev['type']}]"
                display += f"\n  {ev['date']}{time_str}: {ev['title']}{type_str} (ID: {ev['id']})"
                
        return {"status": "ok", "events": events, "display": display}

    else:
        if not date_str:
            date_str = "today"

        target_date = date_str
        try:
            import dateparser
            dt = dateparser.parse(date_str)
            if dt:
                target_date = dt.date().isoformat()
        except ImportError:
            pass

        events = store.query_date(target_date)
        display = f"Events on {target_date}:"
        if not events:
            display += "\n  No events scheduled."
        else:
            for ev in events:
                time_str = f" at {ev['time']}" if ev.get("time") else " (All day)"
                type_str = f" [{ev['type']}]"
                display += f"\n  {ev['title']}{time_str}{type_str} (ID: {ev['id']})"
                
        return {"status": "ok", "events": events, "display": display}
