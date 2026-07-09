from core.todo.todo_store import ToDoStore

def run(params: dict) -> dict:
    text     = params.get("text", "").strip()
    bucket   = params.get("bucket", "backlog").strip().lower()
    due_date = params.get("due_date")

    if not text:
        return {"status": "error", "reason": "Missing required param: 'text'"}

    if bucket not in ("today", "tomorrow", "this_week", "backlog"):
        bucket = "backlog"

    if due_date:
        try:
            import dateparser
            dt = dateparser.parse(due_date)
            if dt:
                due_date = dt.date().isoformat()
        except ImportError:
            pass  # Fall back to using the raw input string if library not available

    store = ToDoStore()
    return store.add(text, bucket, due_date)
