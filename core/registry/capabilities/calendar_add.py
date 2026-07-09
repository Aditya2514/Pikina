import re
from core.calendar.calendar_store import CalendarStore

def run(params: dict) -> dict:
    title      = params.get("title", "").strip()
    date_input = params.get("date", "").strip()
    time_input = params.get("time")
    event_type = params.get("type", "personal").strip().lower()
    recurring  = params.get("recurring", "none").strip().lower()

    if not title:
        return {"status": "error", "reason": "Missing required param: 'title'"}
    if not date_input:
        return {"status": "error", "reason": "Please specify a date/time using 'on' or 'at' (e.g. 'add event dentist on tomorrow at 3pm')."}

    parsed_date = date_input
    parsed_time = time_input

    try:
        import dateparser
        dt = dateparser.parse(date_input)
        if dt:
            parsed_date = dt.date().isoformat()
            if not parsed_time:
                # Check if raw date_input string has time info
                if re.search(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(am|pm)\b|\bat\s+\d{1,2}\b", date_input, re.I):
                    parsed_time = dt.strftime("%H:%M")
    except ImportError:
        pass

    if event_type not in ("personal", "college", "appointment", "holiday"):
        event_type = "personal"
    if recurring not in ("none", "yearly"):
        recurring = "none"

    # Normalize time_input if explicitly passed
    if parsed_time:
        parsed_time = parsed_time.strip()
        try:
            import dateparser
            t_dt = dateparser.parse(parsed_time)
            if t_dt:
                parsed_time = t_dt.strftime("%H:%M")
        except ImportError:
            pass

    store = CalendarStore()
    return store.add_event(title, parsed_date, parsed_time, event_type, "user", recurring)
