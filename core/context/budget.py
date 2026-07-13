"""
Pikina OS — Context Budgeting Layer
Computes token estimates and applies drop-order truncation to fit the bounded context limit.
"""

def estimate_tokens(text: str) -> int:
    """Lightweight token estimator heuristic (len // 4)."""
    return len(text) // 4


def truncate_context(permanent_items: list, ephemeral_items: list, kg_facts: list, max_tokens: int = 2000) -> list:
    """
    Applies drop-order truncation to fit the target token budget:
    1. Drop lowest-similarity ephemeral items first.
    2. Drop KG facts second (if all ephemerals are gone).
    3. Drop lowest-similarity permanent items last (if all KG facts are gone).
    
    Each item is a dict containing 'text', 'tier', and 'similarity'.
    Returns a unified list of remaining items.
    """
    # Clone lists to avoid modifying in-place
    p_list = list(permanent_items)
    e_list = list(ephemeral_items)
    k_list = list(kg_facts)

    def get_total_tokens():
        unified_text = "\n".join(item["text"] for item in p_list + e_list + k_list)
        return estimate_tokens(unified_text)

    while get_total_tokens() > max_tokens:
        if e_list:
            # Drop the ephemeral item with the lowest similarity
            e_list.sort(key=lambda x: x["similarity"])
            e_list.pop(0)
        elif k_list:
            # Drop the last KG fact
            k_list.pop()
        elif p_list:
            # Drop the permanent item with the lowest similarity
            p_list.sort(key=lambda x: x["similarity"])
            p_list.pop(0)
        else:
            break

    # Re-assemble preserving order: Permanent first, Ephemeral second, KG last
    return p_list + e_list + k_list
