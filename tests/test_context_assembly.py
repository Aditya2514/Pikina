"""
Phase 4b — Context assembly: similarity thresholds, trust labels,
knowledge-graph facts and drop-order budgeting.
"""
import sqlite3

import pytest

from pikina.memory.vector_store import VectorStore
from pikina.memory.knowledge_graph import KnowledgeGraph
from pikina.context.retrieval import assemble_context


@pytest.fixture
def stores():
    vs = VectorStore()
    kg = KnowledgeGraph()
    kg.add_relationship("ProjectX", "USES", "PostgreSQL")
    kg.add_relationship("ProjectX", "BUILT_WITH", "Python")
    kg.add_relationship("ProjectY", "USES", "SQLite")
    vs.add_permanent("p1", "ProjectX is a secure project using PostgreSQL")
    vs.add_permanent("p2", "Short term goals include writing unit tests")
    vs.add_ephemeral("e1", "The user works on ProjectY in their office")
    vs.add_ephemeral("e2", "Remember to drink water every hour")
    with sqlite3.connect(vs.db_path) as conn:
        conn.execute("UPDATE ephemeral_vectors SET corroboration_count = 5 WHERE id = 'e1'")
    return vs, kg


def test_permanent_match_included_with_label(stores):
    ctx = assemble_context("ProjectX is a secure project using PostgreSQL", *stores)
    assert "(permanent, confirmed): ProjectX is a secure project using PostgreSQL" in ctx


def test_low_similarity_permanent_excluded(stores):
    assert "(permanent, confirmed)" not in assemble_context("Unrelated search query for coffee recipes", *stores)


def test_threshold_boundaries(stores, monkeypatch):
    vs, kg = stores
    fake = {"permanent": [(0.51, "p1", "Sample permanent content")],
            "ephemeral": [(0.59, "e1", "Sample ephemeral content")]}
    monkeypatch.setattr(vs, "search", lambda q, tier="ephemeral", top_k=5: fake[tier])
    ctx = assemble_context("boundary", vs, kg)
    assert "Sample permanent content" in ctx       # 0.51 > 0.5
    assert "Sample ephemeral content" not in ctx   # 0.59 <= 0.6


def test_ephemeral_match_carries_corroboration_count(stores):
    ctx = assemble_context("The user works on ProjectY in their office", *stores)
    assert "(ephemeral, inferred, corroboration_count=5): The user works on ProjectY in their office" in ctx


def test_kg_entity_matched_case_insensitively(stores):
    ctx = assemble_context("I want to run projectx now", *stores)
    assert "(structural fact): ProjectX -[USES]-> PostgreSQL" in ctx
    assert "(structural fact): ProjectX -[BUILT_WITH]-> Python" in ctx


def test_no_matches_returns_empty_block(stores):
    assert assemble_context("Blah blah no matching keywords anywhere", *stores).strip() == ""


def test_budget_drops_ephemeral_first(stores, monkeypatch):
    vs, kg = stores
    fake = {"permanent": [(0.95, "p1", "A" * 200), (0.55, "p2", "B" * 200)],
            "ephemeral": [(0.99, "e1", "C" * 200), (0.65, "e2", "D" * 200)]}
    monkeypatch.setattr(vs, "search", lambda q, tier="ephemeral", top_k=5: fake[tier])
    ctx = assemble_context("heavy_query ProjectY", vs, kg, max_tokens=120)
    assert "A" * 200 in ctx
    assert "C" * 200 not in ctx and "D" * 200 not in ctx
