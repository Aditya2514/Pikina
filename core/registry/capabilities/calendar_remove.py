from core.calendar.calendar_store import CalendarStore

def run(params: dict) -> dict:
    text = params.get("text", "").strip()

    if not text:
        return {"status": "error", "reason": "Missing required param: 'text'"}

    store = CalendarStore()
    return store.remove_event(text)
