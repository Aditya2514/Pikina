import time
import threading
import sqlite3
from datetime import datetime, timezone, timedelta
from core.memory.vector_store import VectorStore

class ForgettingEngine(threading.Thread):
    """
    A background daemon that periodically sweeps the ephemeral memory store.
    - Data older than `max_age_hours` is evaluated.
    - If it has high corroboration (>= `promotion_threshold`), it is promoted to permanent.
    - Otherwise, it is deleted.
    """
    def __init__(self, sweep_interval_sec=3600, max_age_hours=24, promotion_threshold=3):
        super().__init__(daemon=True, name="ForgettingEngine")
        self.sweep_interval_sec = sweep_interval_sec
        self.max_age_hours = max_age_hours
        self.promotion_threshold = promotion_threshold
        self.vector_store = VectorStore()
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        print("[ForgettingEngine] Started.")
        while not self._stop_event.is_set():
            self._sweep()
            # Sleep in small chunks to allow quick shutdown
            for _ in range(self.sweep_interval_sec):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        print("[ForgettingEngine] Stopped.")

    def _sweep(self):
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.max_age_hours)
            cutoff_iso = cutoff.isoformat()

            with sqlite3.connect(self.vector_store.db_path) as conn:
                c = conn.cursor()
                
                # 1. Promote highly corroborated older items
                c.execute('''
                    SELECT id, content FROM ephemeral_vectors 
                    WHERE timestamp < ? AND corroboration_count >= ?
                ''', (cutoff_iso, self.promotion_threshold))
                to_promote = c.fetchall()

                for row_id, content in to_promote:
                    self.vector_store.add_permanent(row_id, content)
                    print(f"[ForgettingEngine] Promoted {row_id} to permanent storage.")

                # 2. Delete all evaluated items (including those just promoted)
                c.execute('''
                    DELETE FROM ephemeral_vectors 
                    WHERE timestamp < ?
                ''', (cutoff_iso,))
                
                deleted_count = c.rowcount
                if deleted_count > 0:
                    print(f"[ForgettingEngine] Swept {deleted_count} stale ephemeral records.")
                
                conn.commit()
        except Exception as e:
            print(f"[ForgettingEngine] Sweep failed: {e}")
