"""
Pikina OS — Context Retrieval Layer
Retrieves, filters, and standardizes trust-labeled context from memory stores.
"""
import sqlite3
from core.memory.vector_store import VectorStore
from core.memory.knowledge_graph import KnowledgeGraph
from core.context.budget import truncate_context


def assemble_context(command_text: str, vs=None, kg=None, max_tokens: int = 2000) -> str:
    """
    Assembles a trust-labeled context block matching a given command.
    
    Args:
        command_text: The input user command.
        vs: Optional VectorStore instance (defaults to global singleton).
        kg: Optional KnowledgeGraph instance (defaults to global instance).
        max_tokens: Token ceiling limit.
        
    Returns:
        Assembled, trust-labeled context block prompt string.
    """
    vs = vs or VectorStore()
    kg = kg or KnowledgeGraph()

    # 1. Retrieve & filter permanent memories (similarity > 0.5)
    perm_matches = vs.search(command_text, tier="permanent", top_k=3)
    permanent_items = []
    for sim, row_id, content in perm_matches:
        if sim > 0.5:
            permanent_items.append({
                "text": f"(permanent, confirmed): {content}",
                "tier": "permanent",
                "similarity": sim
            })

    # 2. Retrieve & filter ephemeral memories (similarity > 0.6)
    eph_matches = vs.search(command_text, tier="ephemeral", top_k=2)
    ephemeral_items = []
    
    if eph_matches:
        # Retrieve corroboration counts for matching ephemeral rows
        with sqlite3.connect(vs.db_path) as conn:
            for sim, row_id, content in eph_matches:
                if sim > 0.6:
                    count = 0
                    row = conn.execute(
                        "SELECT corroboration_count FROM ephemeral_vectors WHERE id = ?", 
                        (row_id,)
                    ).fetchone()
                    if row:
                        count = row[0]
                    
                    ephemeral_items.append({
                        "text": f"(ephemeral, inferred, corroboration_count={count}): {content}",
                        "tier": "ephemeral",
                        "similarity": sim
                    })

    # 3. Extract KG structural facts (case-insensitive node name substring matching)
    kg_facts = []
    seen_facts = set()
    command_lower = command_text.lower()
    
    # Retrieve all nodes from the graph
    nodes = list(kg.graph.nodes)
    for node in nodes:
        if node.lower() in command_lower:
            neighbors = kg.get_neighbors(node)
            for u, rel, v in neighbors:
                fact_str = f"(structural fact): {u} -[{rel}]-> {v}"
                if fact_str not in seen_facts:
                    seen_facts.add(fact_str)
                    kg_facts.append({
                        "text": fact_str,
                        "tier": "kg",
                        "similarity": 0.0
                    })

    # 4. Truncate context to token budget
    remaining_items = truncate_context(
        permanent_items=permanent_items,
        ephemeral_items=ephemeral_items,
        kg_facts=kg_facts,
        max_tokens=max_tokens
    )

    # 5. Output unified block
    return "\n".join(item["text"] for item in remaining_items)
