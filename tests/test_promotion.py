import sqlite3
import time
from core.memory.vector_store import VectorStore
from core.memory.forgetting_engine import ForgettingEngine
from datetime import datetime, timezone, timedelta

def test_promotion():
    print("Testing Ephemeral -> Permanent Promotion")
    vs = VectorStore()
    
    # 1. Insert a heavily corroborated ephemeral record
    import uuid
    test_id = f"test-promote-{int(time.time())}"
    content = f"This is a heavily corroborated memory that should be promoted. {uuid.uuid4()}"
    
    vs.add_ephemeral(test_id, content)
    
    # artificially set corroboration_count to 5 (above threshold of 3)
    # and make it 25 hours old so the sweep picks it up
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    with sqlite3.connect(vs.db_path) as conn:
        conn.execute(
            "UPDATE ephemeral_vectors SET corroboration_count = 5, timestamp = ? WHERE id = ?",
            (old_time, test_id)
        )
    print(f"Inserted ephemeral record {test_id} with count 5")
    
    # 2. Run forgetting engine sweep
    print("Running ForgettingEngine sweep...")
    fe = ForgettingEngine(sweep_interval_sec=10, max_age_hours=24, promotion_threshold=3)
    fe._sweep()  # run exactly once manually
    
    # 3. Check if it was promoted
    with sqlite3.connect(vs.db_path) as conn:
        eph = conn.execute("SELECT * FROM ephemeral_vectors WHERE id = ?", (test_id,)).fetchone()
        perm = conn.execute("SELECT * FROM permanent_vectors WHERE id = ?", (test_id,)).fetchone()
    
    if eph is None and perm is not None:
        print("[SUCCESS] Record was deleted from ephemeral and inserted into permanent.")
    elif eph is not None and perm is not None:
        print("[FAILED] Record is in both tables.")
    elif eph is not None and perm is None:
        print("[FAILED] Record stayed in ephemeral store.")
    else:
        print("[FAILED] Record was lost entirely.")

if __name__ == "__main__":
    test_promotion()
