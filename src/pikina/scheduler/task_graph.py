"""
Pikina OS — Task Graph and Schema Definition
Defines Task schema and Directed Acyclic Graph (DAG) dependency checks using NetworkX.
"""
from datetime import datetime
import networkx as nx


class Task:
    """Represents a scheduled unit of work in Pikina OS."""
    def __init__(
        self,
        id: str,
        capability: str,
        params: dict = None,
        priority: int = 1,
        deadline: str = None,
        dependencies: list = None,
        status: str = "queued",
        created_at: str = None,
        estimated_duration_s: float = 10.0,
    ):
        self.id = id
        self.capability = capability
        self.params = params or {}
        
        # Priority must be between 1 and 5 (clamped)
        self.priority = max(1, min(5, priority))
        
        self.deadline = deadline  # ISO8601 string or None
        self.dependencies = dependencies or []  # list of task IDs
        self.status = status  # queued, blocked, ready, running, done, failed
        self.created_at = created_at or datetime.utcnow().isoformat()
        self.estimated_duration_s = estimated_duration_s
        self.skipped_count = 0  # Number of times skipped due to cost limits

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "capability": self.capability,
            "params": self.params,
            "priority": self.priority,
            "deadline": self.deadline,
            "dependencies": self.dependencies,
            "status": self.status,
            "created_at": self.created_at,
            "estimated_duration_s": self.estimated_duration_s,
        }


def validate_task_graph(tasks: list) -> bool:
    """
    Validates that the set of tasks does not contain cyclic dependencies.
    Raises ValueError if a cycle is detected.
    """
    g = nx.DiGraph()
    
    # 1. Add all tasks in the list as nodes
    for t in tasks:
        g.add_node(t.id)

    # 2. Add edges: A -> B means A is a dependency of B (A must finish before B)
    for t in tasks:
        for dep_id in t.dependencies:
            # If dependency is external (not in this batch), we add it as a node to build the graph
            if dep_id not in g:
                g.add_node(dep_id)
            g.add_edge(dep_id, t.id)

    # 3. Check for cycles
    if not nx.is_directed_acyclic_graph(g):
        # We can extract the cycles to provide a helpful error message
        try:
            cycle = nx.find_cycle(g)
            cycle_desc = " -> ".join(f"{u}" for u, v in cycle) + f" -> {cycle[0][0]}"
            raise ValueError(f"Cyclic dependency detected: {cycle_desc}")
        except nx.NetworkXNoCycle:
            raise ValueError("Cyclic dependency detected among tasks.")
            
    return True
