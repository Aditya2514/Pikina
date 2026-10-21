from pikina.todo.todo_store import ToDoStore

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
            dt = dateparser.parse(due_date, settings={"PREFER_DATES_FROM": "future"})
            if dt:
                due_date = dt.date().isoformat()
        except ImportError:
            pass  # Fall back to using the raw input string if library not available

    store = ToDoStore()
    return store.add(text, bucket, due_date)


def parse(params: dict) -> dict:
    """Split "buy milk in today due friday" into text, bucket and due date."""
    import re
    text = params["text"]
    bucket, due_date = "backlog", None
    bucket_match = re.search(r"\b(bucket|in)\s+(today|tomorrow|this_week|backlog)\b", text, re.I)
    if bucket_match:
        bucket = bucket_match.group(2).lower()
        text = text[:bucket_match.start()] + text[bucket_match.end():]
    due_match = re.search(r"\b(due|by|at)\s+(?P<date>.+)$", text, re.I)
    if due_match:
        due_date = due_match.group("date").strip()
        text = text[:due_match.start()].strip()
    return {"text": text.strip(), "bucket": bucket, "due_date": due_date}
