import json
import time
from pathlib import Path

class GraphCache:
    """
    A Graph-like file cache that tracks files and their parent directories.
    Automatically ranks items by hit count and recency.
    """
    def __init__(self, storage_file="data/file_graph.json"):
        self.storage_file = Path(storage_file)
        self.graph = {}
        self._load()

    def _load(self):
        if self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    self.graph = json.load(f)
            except Exception:
                self.graph = {}
        else:
            self.graph = {}

    def _save(self):
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_file, "w", encoding="utf-8") as f:
            json.dump(self.graph, f, indent=2)

    def add_path(self, absolute_path, is_dir=False):
        """Add a path to the cache, incrementing its hits and mapping its parent."""
        path_obj = Path(absolute_path)
        path_str = str(path_obj)
        
        # Add or update the actual target
        if path_str not in self.graph:
            self.graph[path_str] = {
                "basename": path_obj.name,
                "path": path_str,
                "type": "dir" if is_dir else "file",
                "hits": 0,
                "last_accessed": 0
            }
        
        self.graph[path_str]["hits"] += 1
        self.graph[path_str]["last_accessed"] = time.time()

        # Also weakly map the parent directory so the user can search by folder name
        parent_str = str(path_obj.parent)
        if parent_str != path_str and parent_str not in self.graph:
            self.graph[parent_str] = {
                "basename": path_obj.parent.name,
                "path": parent_str,
                "type": "dir",
                "hits": 0,
                "last_accessed": time.time()
            }
            
        self._save()

    def search(self, query, limit=5):
        """
        Search the graph for paths matching the query.
        Returns a list of dicts, sorted by hits.
        """
        query = query.lower()
        results = []
        
        for p, data in self.graph.items():
            # If the query matches the basename or the full path
            if query in data["basename"].lower() or query in p.lower():
                results.append(data)
                
        # Sort by hits (descending), then by last accessed (descending)
        results.sort(key=lambda x: (x["hits"], x["last_accessed"]), reverse=True)
        return results[:limit]
