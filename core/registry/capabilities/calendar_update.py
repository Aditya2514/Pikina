from core.calendar.calendar_store import CalendarStore

def run(params: dict) -> dict:
    event_id  = params.get("event_id", "").strip()
    title     = params.get("title", "").strip()
    date_str  = params.get("date", "").strip()
    time_str  = params.get("time")
    type_str  = params.get("type", "personal").strip().lower()
    source    = params.get("source", "user").strip().lower()
    recurring = params.get("recurring", "none").strip().lower()

    if not event_id:
        return {"status": "error", "reason": "Missing required param: 'event_id'"}
    if not title:
        return {"status": "error", "reason": "Missing required param: 'title'"}
    if not date_str:
        return {"status": "error", "reason": "Missing required param: 'date'"}

    # Validate date format (simple check)
    if "-" not in date_str or len(date_str) < 8:
        return {"status": "error", "reason": "Invalid date format. Use YYYY-MM-DD."}

    store = CalendarStore()
    return store.update_event(
        event_id=event_id,
        title=title,
        date_str=date_str,
        time_str=time_str,
        event_type=type_str,
        source=source,
        recurring=recurring
    )
