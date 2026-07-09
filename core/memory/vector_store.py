import sqlite3
import numpy as np
from datetime import datetime, timezone
import threading
import os

# Suppress the scary huggingface_hub symlink warning on Windows without Developer Mode
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
# Enforce offline mode for embedding generation (model must be cached)
os.environ["HF_HUB_OFFLINE"] = "1"

class VectorStore:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path="data/vector_memory.db"):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst.db_path = db_path
                # Lazy loading of sentence-transformers to keep init fast
                inst._model = None 
                inst._init_db()
                cls._instance = inst
        return cls._instance

    @property
    def model(self):
        if self._model is None:
            # We defer importing and loading the model until the first memory operation
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer('all-MiniLM-L6-v2')
        return self._model

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            # Ephemeral Store (Subject to Forgetting Engine)
            c.execute('''
                CREATE TABLE IF NOT EXISTS ephemeral_vectors (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    vector BLOB,
                    timestamp TEXT,
                    corroboration_count INTEGER DEFAULT 0
                )
            ''')
            # Permanent Store
            c.execute('''
                CREATE TABLE IF NOT EXISTS permanent_vectors (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    vector BLOB,
                    timestamp TEXT
                )
            ''')
            conn.commit()

    def _encode(self, text: str) -> bytes:
        vec = self.model.encode(text)
        return vec.tobytes()

    def add_ephemeral(self, id: str, content: str):
        try:
            vec_bytes = self._encode(content)
            timestamp = datetime.now(timezone.utc).isoformat()
            
            # 1. Semantic Check for Corroboration
            # Let's search the ephemeral store for an existing match > 0.90 similarity
            existing_matches = self.search(content, tier="ephemeral", top_k=1)
            if existing_matches:
                sim, match_id, _ = existing_matches[0]
                if sim > 0.90:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute('''
                            UPDATE ephemeral_vectors 
                            SET corroboration_count = corroboration_count + 1, timestamp = ?
                            WHERE id = ?
                        ''', (timestamp, match_id))
                    return # Successfully corroborated instead of adding duplicate
                    
            # 2. If no match, insert as new
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR IGNORE INTO ephemeral_vectors (id, content, vector, timestamp, corroboration_count)
                    VALUES (?, ?, ?, ?, ?)
                ''', (id, content, vec_bytes, timestamp, 0))
        except Exception as e:
            print(f"[VectorStore] Failed to add ephemeral vector: {e}")

    def increment_corroboration(self, id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE ephemeral_vectors SET corroboration_count = corroboration_count + 1 WHERE id = ?', (id,))

    def add_permanent(self, id: str, content: str):
        try:
            vec_bytes = self._encode(content)
            timestamp = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR IGNORE INTO permanent_vectors (id, content, vector, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (id, content, vec_bytes, timestamp))
        except Exception as e:
            print(f"[VectorStore] Failed to add permanent vector: {e}")

    def search(self, query: str, tier="ephemeral", top_k=5):
        query_vec = self.model.encode(query)
        table = "permanent_vectors" if tier == "permanent" else "ephemeral_vectors"
        
        results = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                rows = c.execute(f'SELECT id, content, vector FROM {table}').fetchall()
                
                query_lower = query.lower()
                query_words = set(query_lower.split())

                for row_id, content, vec_bytes in rows:
                    vec = np.frombuffer(vec_bytes, dtype=np.float32)
                    sim = float(np.dot(query_vec, vec) / (np.linalg.norm(query_vec) * np.linalg.norm(vec)))
                    
                    # Basic Hybrid Keyword Boosting
                    content_lower = content.lower()
                    if query_lower in content_lower:
                        sim += 0.4  # Massive boost for exact phrase match
                    else:
                        # Minor boost for individual word matches (excluding common stop words)
                        match_count = sum(1 for w in query_words if len(w) > 3 and w in content_lower)
                        sim += (0.05 * match_count)
                        
                    results.append((sim, row_id, content))
                    
            results.sort(key=lambda x: x[0], reverse=True)
            return results[:top_k]
        except Exception as e:
            print(f"[VectorStore] Search failed: {e}")
            return []
