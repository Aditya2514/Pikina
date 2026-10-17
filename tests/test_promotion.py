"""Phase 3 — Forgetting engine promotes corroborated ephemeral memories to permanent."""
import sqlite3
from datetime import datetime, timezone, timedelta

from pikina.memory.vector_store import VectorStore
from pikina.memory.forgetting_engine import ForgettingEngine


def test_corroborated_old_memory_is_promoted():
    vs = VectorStore()
    vs.add_ephemeral("promote-me", "This heavily corroborated memory should be promoted")
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    with sqlite3.connect(vs.db_path) as conn:
        conn.execute("UPDATE ephemeral_vectors SET corroboration_count = 5, timestamp = ? WHERE id = ?",
                     (old, "promote-me"))

    ForgettingEngine(sweep_interval_sec=10, max_age_hours=24, promotion_threshold=3)._sweep()

    with sqlite3.connect(vs.db_path) as conn:
        eph = conn.execute("SELECT 1 FROM ephemeral_vectors WHERE id = 'promote-me'").fetchone()
        perm = conn.execute("SELECT 1 FROM permanent_vectors WHERE id = 'promote-me'").fetchone()
    assert eph is None and perm is not None


def test_uncorroborated_old_memory_is_forgotten():
    vs = VectorStore()
    vs.add_ephemeral("forget-me", "A one-off clipboard copy nobody cares about")
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    with sqlite3.connect(vs.db_path) as conn:
        conn.execute("UPDATE ephemeral_vectors SET timestamp = ? WHERE id = ?", (old, "forget-me"))

    ForgettingEngine(sweep_interval_sec=10, max_age_hours=24, promotion_threshold=3)._sweep()

    with sqlite3.connect(vs.db_path) as conn:
        assert conn.execute("SELECT 1 FROM ephemeral_vectors WHERE id = 'forget-me'").fetchone() is None
        assert conn.execute("SELECT 1 FROM permanent_vectors WHERE id = 'forget-me'").fetchone() is None
