"""Tests for eli.planning modules — ~60 tests."""
from __future__ import annotations

import sqlite3


# ── Knowledge Graph ───────────────────────────────────────────────────────


def test_knowledge_graph_schema_has_core_tables(tmp_db):
    from eli.memory.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(db_path=str(tmp_db))
    assert kg.stats() == {"entities": 0, "relations": 0}
    conn = sqlite3.connect(str(tmp_db))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"kg_entities", "kg_relations", "kg_entities_fts"} <= tables
