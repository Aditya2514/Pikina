"""
Pikina OS — Context Assembly Pipeline Test Suite (Phase 4b)
Verifies semantic filters, strict threshold boundaries, KG entity parsing, and drop-order budgeting.
"""
import sys
import os
import time
import sqlite3
import shutil
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure test DB and KG files are cleared before imports
TEST_DB = "data/test_vector_memory.db"
TEST_KG_JSON = "data/test_knowledge_graph.json"

for f in (TEST_DB, TEST_KG_JSON):
    Path(f).unlink(missing_ok=True)

from core.memory.vector_store import VectorStore
from core.memory.knowledge_graph import KnowledgeGraph
from core.context.retrieval import assemble_context
from core.context.budget import estimate_tokens

def run_tests():
    print("=" * 65)
    print("  Pikina OS — Phase 4b Context Assembly Test Suite")
    print("=" * 65)

    # Initialize isolated test databases
    VectorStore._instance = None  # Clear singleton cache
    vs = VectorStore(TEST_DB)
    kg = KnowledgeGraph(TEST_KG_JSON)

    # -------------------------------------------------------------
    # Seed Knowledge Graph data
    # -------------------------------------------------------------
    kg.add_relationship("ProjectX", "USES", "PostgreSQL")
    kg.add_relationship("ProjectX", "BUILT_WITH", "Python")
    kg.add_relationship("ProjectY", "USES", "SQLite")

    # -------------------------------------------------------------
    # Seed Vector memories
    # -------------------------------------------------------------
    # We will seed items and manually set their vectors to control similarity scores.
    # To test similarity matching exactly, we can use exact string matches which trigger 
    # the exact phrase hybrid boost (+0.4) in VectorStore.search().
    # Let's check hybrid search rules in vector_store.py:
    #   if query_lower in content_lower: sim += 0.4
    # Let's seed content and test search logic directly.
    
    # Seed permanent items
    vs.add_permanent("p1", "ProjectX is a secure project using PostgreSQL")
    vs.add_permanent("p2", "Short term goals include writing unit tests")
    
    # Seed ephemeral items
    vs.add_ephemeral("e1", "The user works on ProjectY in their office")
    vs.add_ephemeral("e2", "Remember to drink water every hour")

    # Let's manually set the corroboration count for e1 to 5
    with sqlite3.connect(vs.db_path) as conn:
        conn.execute("UPDATE ephemeral_vectors SET corroboration_count = 5 WHERE id = 'e1'")
        conn.commit()

    # -------------------------------------------------------------
    # Case 1: Command semantically similar to permanent item (> 0.5)
    # -------------------------------------------------------------
    print("\n[Case 1] Permanent memory match > 0.5...")
    ctx = assemble_context("ProjectX is a secure project using PostgreSQL", vs, kg)
    assert "(permanent, confirmed): ProjectX is a secure project using PostgreSQL" in ctx
    print("  [PASS] Permanent item included with correct label.")

    # -------------------------------------------------------------
    # Case 2: Permanent memory match <= 0.5 (strict check)
    # -------------------------------------------------------------
    print("\n[Case 2] Permanent memory match <= 0.5 (strict check)...")
    # For a completely unrelated query, similarity will be low and fail the >0.5 bar
    ctx = assemble_context("Unrelated search query for coffee recipes", vs, kg)
    assert "(permanent, confirmed)" not in ctx
    print("  [PASS] Low similarity permanent items excluded.")

    # -------------------------------------------------------------
    # Case 3: Ephemeral memory match between 0.5 and 0.6
    # -------------------------------------------------------------
    print("\n[Case 3] Ephemeral match between 0.5 and 0.6 (excluded)...")
    # Let's construct a search where we get a similarity around 0.55.
    # To mock/simulate exact similarity scores, we can temporarily patch vs.search in this test.
    original_search = vs.search
    
    def mock_search(query, tier="ephemeral", top_k=5):
        if query == "test_boundary_query":
            if tier == "permanent":
                return [(0.51, "p1", "Sample permanent content")]
            else: # ephemeral
                return [(0.59, "e1", "Sample ephemeral content")]
        return original_search(query, tier, top_k)
    
    vs.search = mock_search

    # query has 0.51 for permanent (should pass >0.5) and 0.59 for ephemeral (should fail >0.6)
    ctx = assemble_context("test_boundary_query", vs, kg)
    assert "Sample permanent content" in ctx
    assert "Sample ephemeral content" not in ctx
    print("  [PASS] Ephemeral match at 0.59 excluded (fails 0.6 bar).")

    # -------------------------------------------------------------
    # Case 4: Ephemeral memory match > 0.6
    # -------------------------------------------------------------
    print("\n[Case 4] Ephemeral match > 0.6...")
    # Exact match query for e1 triggers hybrid boost, pushing similarity above 0.6
    ctx = assemble_context("The user works on ProjectY in their office", vs, kg)
    assert "(ephemeral, inferred, corroboration_count=5): The user works on ProjectY in their office" in ctx
    print("  [PASS] Ephemeral item included with correct corroboration_count.")

    # -------------------------------------------------------------
    # Case 5: Command mentions known Knowledge Graph entity by name
    # -------------------------------------------------------------
    print("\n[Case 5] KG entity matching (one-hop subgraph)...")
    # Case-insensitive entity substring check: 'projectx' should match 'ProjectX' node
    ctx = assemble_context("I want to run projectx now", vs, kg)
    assert "(structural fact): ProjectX -[USES]-> PostgreSQL" in ctx
    assert "(structural fact): ProjectX -[BUILT_WITH]-> Python" in ctx
    print("  [PASS] Entity one-hop subgraph matches case-insensitively.")

    # -------------------------------------------------------------
    # Case 6: Command mentions no known entity, has no matches
    # -------------------------------------------------------------
    print("\n[Case 6] Empty matches return minimal block...")
    ctx = assemble_context("Blah blah no matching keywords anywhere", vs, kg)
    assert ctx.strip() == ""
    print("  [PASS] Empty block returned without errors.")

    # -------------------------------------------------------------
    # Case 7: Deliberately exceed token budget (~2000 tokens / 500 chars)
    # -------------------------------------------------------------
    print("\n[Case 7] Exceeding token budget (drop-order check)...")
    # Let's override vs.search to return multiple heavy items with controlled similarities
    # We set max_tokens=150 (approx 600 characters) to force truncation
    def mock_large_search(query, tier="ephemeral", top_k=5):
        if query.startswith("heavy_query"):
            if tier == "permanent":
                return [
                    (0.95, "p1", "A" * 200),  # permanent 1 (high similarity) - 200 chars
                    (0.55, "p2", "B" * 200),  # permanent 2 (low similarity) - 200 chars
                ]
            else: # ephemeral
                return [
                    (0.99, "e1", "C" * 200),  # ephemeral 1 (high similarity) - 200 chars
                    (0.65, "e2", "D" * 200),  # ephemeral 2 (low similarity) - 200 chars
                ]
        return original_search(query, tier, top_k)
        
    vs.search = mock_large_search
    
    # 1. Ephemeral items should drop first in order of similarity (lowest first: D, then C)
    # 2. KG facts drop second (if all ephemerals are gone)
    # 3. Permanent items drop last in order of similarity (lowest first: B, then A)
    # Let's run a query that matches both permanent, both ephemeral, and a KG node ('ProjectY')
    ctx = assemble_context("heavy_query ProjectY", vs, kg, max_tokens=120)
    
    # Expected drop order:
    # Total chars before truncation ~ 800-900 chars (over 120 tokens/480 chars ceiling)
    # Ephemeral 2 (D) drops first.
    # Ephemeral 1 (C) drops next.
    # If still over budget, ProjectY fact drops.
    # If still over, Permanent 2 (B) drops.
    # Permanent 1 (A) remains.
    
    # Let's verify what is retained
    print(f"  [Diagnostics] Assembled Prompt length: {len(ctx)} chars")
    assert "A" * 200 in ctx  # permanent 1 (high sim) must be retained
    assert "D" * 200 not in ctx  # ephemeral 2 (low sim) must be dropped first
    assert "C" * 200 not in ctx  # ephemeral 1 (high sim) dropped next
    print("  [PASS] Drop-order rules respected: lowest-similarity ephemerals dropped first.")

    # Restore search method
    vs.search = original_search

    # Clean up test files
    VectorStore._instance = None
    import gc
    gc.collect()
    time.sleep(0.1)

    for f in (TEST_DB, TEST_KG_JSON):
        try:
            Path(f).unlink(missing_ok=True)
        except Exception as e:
            print(f"[Cleanup Warning] Failed to delete {f}: {e}")
            
    print("\n[Cleanup] Isolated test files unlinked.")

    print("\n" + "=" * 65)
    print("  === ALL PHASE 4b RETRIEVAL TESTS PASSED ===")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_tests()
