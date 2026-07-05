import networkx as nx
import json
from pathlib import Path
import threading

class KnowledgeGraph:
    """
    A persistent structural graph of explicit facts and corroborated relationships.
    Uses NetworkX for traversals and algorithms, backed by JSON.
    Maintains the purity rule: only structural/corroborated facts go here, NOT raw sensory logs.
    """
    def __init__(self, storage_file="data/knowledge_graph.json"):
        self.storage_file = Path(storage_file)
        self.graph = nx.DiGraph()
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        with self._lock:
            if self.storage_file.exists():
                try:
                    with open(self.storage_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.graph = nx.node_link_graph(data)
                except Exception as e:
                    print(f"[KnowledgeGraph] Failed to load: {e}")
                    self.graph = nx.DiGraph()

    def _save(self):
        with self._lock:
            self.storage_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_file, "w", encoding="utf-8") as f:
                data = nx.node_link_data(self.graph)
                json.dump(data, f, indent=2)

    def add_relationship(self, subject: str, predicate: str, object_: str, **attributes):
        """
        Add an explicit relationship to the graph. 
        e.g., add_relationship("Pikina OS", "USES", "SQLite")
        """
        self.graph.add_node(subject)
        self.graph.add_node(object_)
        self.graph.add_edge(subject, object_, relationship=predicate, **attributes)
        self._save()

    def get_neighbors(self, node: str, limit=10):
        """Retrieve immediate relationships for a given entity."""
        if node not in self.graph:
            return []
        
        edges = self.graph.out_edges(node, data=True)
        return [(u, data.get('relationship', 'RELATES_TO'), v) for u, v, data in edges][:limit]
