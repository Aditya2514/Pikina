from core.todo.todo_store import ToDoStore

def run(params: dict) -> dict:
    text = params.get("text", "").strip()

    if not text:
        return {"status": "error", "reason": "Missing required param: 'text'"}

    store = ToDoStore()
    return store.complete(text)
