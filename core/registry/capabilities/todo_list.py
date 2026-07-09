from core.todo.todo_store import ToDoStore

def run(params: dict) -> dict:
    status = params.get("status", "pending").strip().lower()
    bucket = params.get("bucket")

    if status not in ("pending", "done", "all"):
        status = "pending"

    if bucket:
        bucket = bucket.strip().lower()
        if bucket not in ("today", "tomorrow", "this_week", "backlog"):
            bucket = None

    store = ToDoStore()
    todos = store.list_todos(status=status, bucket=bucket)

    if not todos:
        return {
            "status": "ok",
            "todos": [],
            "display": "No matching tasks found."
        }

    lines = []
    for t in todos:
        due_str = f" [Due: {t['due_date']}]" if t.get("due_date") else ""
        bucket_str = f" ({t['bucket']})" if t.get("bucket") else ""
        status_symbol = "[ ]" if t["status"] == "pending" else "[x]"
        lines.append(f"  {t['id']} {status_symbol} {t['text']}{bucket_str}{due_str}")

    return {
        "status": "ok",
        "todos": todos,
        "count": len(todos),
        "display": "\n".join(lines)
    }
