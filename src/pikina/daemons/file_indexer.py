import os
import time
import sqlite3
import threading
from pathlib import Path

from pikina import paths
from pikina.osbridge import user_dirs

class FileIndexerDaemon:
    def __init__(self, db_path=None, scan_interval=3600):
        self.db_path = Path(db_path) if db_path else paths.data_file(paths.FILE_INDEX_DB)
        self.scan_interval = scan_interval
        self.running = False
        self._thread = None
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    path TEXT UNIQUE,
                    ext TEXT,
                    last_modified REAL
                )
            ''')
            # Create indexes for fast searching
            c.execute('CREATE INDEX IF NOT EXISTS idx_name ON files(name)')
            conn.commit()

    def start(self):
        if not self.running:
            self.running = True
            self._thread = threading.Thread(target=self._crawl_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _get_search_roots(self):
        return user_dirs()

    def _crawl_loop(self):
        while self.running:
            try:
                self._run_scan()
            except Exception as e:
                print(f"[FileIndexer] Error during scan: {e}")
            
            # Sleep for scan_interval, checking self.running every second
            for _ in range(self.scan_interval):
                if not self.running:
                    break
                time.sleep(1)

    def _run_scan(self):
        roots = self._get_search_roots()
        
        # We will collect all files in a batch and do an UPSERT
        # To avoid blocking the DB too long, we batch commits
        
        batch = []
        batch_size = 1000
        
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(str(root)):
                if not self.running:
                    return
                
                # Skip hidden directories like .git or node_modules
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and d != 'node_modules' and d != '.venv' and d != '__pycache__']
                
                for f in filenames:
                    # Skip hidden files
                    if f.startswith('.'):
                        continue
                        
                    full_path = os.path.join(dirpath, f)
                    try:
                        stat = os.stat(full_path)
                        ext = os.path.splitext(f)[1].lower()
                        batch.append((f, full_path, ext, stat.st_mtime))
                    except OSError:
                        pass
                        
                    if len(batch) >= batch_size:
                        self._commit_batch(batch)
                        batch.clear()
                        time.sleep(0.01) # Yield to prevent CPU hogging
                        
        if batch:
            self._commit_batch(batch)

    def _commit_batch(self, batch):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.executemany('''
                    INSERT INTO files (name, path, ext, last_modified)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        name=excluded.name,
                        ext=excluded.ext,
                        last_modified=excluded.last_modified
                ''', batch)
                conn.commit()
        except sqlite3.Error as e:
            print(f"[FileIndexer] DB Commit Error: {e}")

    @classmethod
    def search(cls, pattern, limit=20, db_path=None):
        """
        Search for files using SQLite LIKE clause.
        Pattern can contain * and ?. e.g. *report*.pdf
        """
        db_path = str(db_path or paths.data_file(paths.FILE_INDEX_DB))
        if not os.path.exists(db_path):
            return []
            
        # Convert glob pattern to SQL LIKE pattern
        sql_pattern = pattern.replace('*', '%').replace('?', '_')
        
        results = []
        try:
            with sqlite3.connect(db_path) as conn:
                c = conn.cursor()
                c.execute('''
                    SELECT path FROM files 
                    WHERE name LIKE ? 
                    LIMIT ?
                ''', (sql_pattern, limit))
                results = [row[0] for row in c.fetchall()]
        except sqlite3.Error as e:
            print(f"[FileIndexer] Search error: {e}")
            
        return results
