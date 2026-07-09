import sqlite3
import os
import uuid
from datetime import datetime, timezone

DB_PATH = "data/pikina.db"

class ToDoStore:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    bucket TEXT DEFAULT 'backlog',
                    due_date TEXT,
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            conn.commit()

    def add(self, text: str, bucket: str = "backlog", due_date: str = None) -> dict:
        text = text.strip()
        if not text:
            return {"status": "error", "reason": "Task text cannot be empty."}
        
        todo_id = str(uuid.uuid4())[:8]
        created_at = datetime.now(timezone.utc).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO todos (id, text, status, bucket, due_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (todo_id, text, "pending", bucket, due_date, created_at)
            )
            conn.commit()
            
        return {
            "status": "ok",
            "todo": {
                "id": todo_id,
                "text": text,
                "status": "pending",
                "bucket": bucket,
                "due_date": due_date,
                "created_at": created_at
            }
        }

    def complete(self, text_or_id: str) -> dict:
        text_or_id = text_or_id.strip()
        if not text_or_id:
            return {"status": "error", "reason": "Search query cannot be empty."}

        # Try exact ID match first
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM todos WHERE id = ? AND status = 'pending'", (text_or_id,))
            match = c.fetchone()
            if match:
                todo_id = match["id"]
                completed_at = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE todos SET status = 'done', completed_at = ? WHERE id = ?",
                    (completed_at, todo_id)
                )
                conn.commit()
                return {"status": "ok", "message": f"Task '{match['text']}' marked complete."}

            # Try substring match on pending tasks
            c.execute("SELECT * FROM todos WHERE text LIKE ? AND status = 'pending'", (f"%{text_or_id}%",))
            matches = c.fetchall()

            if not matches:
                return {"status": "error", "reason": f"No pending task found matching '{text_or_id}'."}
            
            if len(matches) > 1:
                # Ambiguous case: return all matches to narrow down
                candidates = [{"id": r["id"], "text": r["text"]} for r in matches]
                return {
                    "status": "ambiguous",
                    "reason": f"Multiple tasks match '{text_or_id}'. Please specify the ID or a more specific query.",
                    "candidates": candidates
                }

            # Exactly one match
            match = matches[0]
            todo_id = match["id"]
            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE todos SET status = 'done', completed_at = ? WHERE id = ?",
                (completed_at, todo_id)
            )
            conn.commit()
            return {"status": "ok", "message": f"Task '{match['text']}' marked complete."}

    def remove(self, text_or_id: str) -> dict:
        text_or_id = text_or_id.strip()
        if not text_or_id:
            return {"status": "error", "reason": "Search query cannot be empty."}

        # Try exact ID match first
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM todos WHERE id = ?", (text_or_id,))
            match = c.fetchone()
            if match:
                conn.execute("DELETE FROM todos WHERE id = ?", (match["id"],))
                conn.commit()
                return {"status": "ok", "message": f"Task '{match['text']}' removed."}

            # Try substring match
            c.execute("SELECT * FROM todos WHERE text LIKE ?", (f"%{text_or_id}%",))
            matches = c.fetchall()

            if not matches:
                return {"status": "error", "reason": f"No task found matching '{text_or_id}'."}
            
            if len(matches) > 1:
                candidates = [{"id": r["id"], "text": r["text"]} for r in matches]
                return {
                    "status": "ambiguous",
                    "reason": f"Multiple tasks match '{text_or_id}'. Please specify the ID or a more specific query.",
                    "candidates": candidates
                }

            # Exactly one match
            match = matches[0]
            conn.execute("DELETE FROM todos WHERE id = ?", (match["id"],))
            conn.commit()
            return {"status": "ok", "message": f"Task '{match['text']}' removed."}

    def list_todos(self, status: str = "pending", bucket: str = None) -> list:
        query = "SELECT * FROM todos WHERE 1=1"
        params = []
        
        if status != "all":
            query += " AND status = ?"
            params.append(status)
            
        if bucket:
            query += " AND bucket = ?"
            params.append(bucket)
            
        query += " ORDER BY created_at DESC"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query, params)
            rows = c.fetchall()
            return [dict(r) for r in rows]
