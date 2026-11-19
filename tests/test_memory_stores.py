"""Tests for eli.memory stores and DB utilities — ~120 tests."""
from __future__ import annotations

import sqlite3
import pytest

from eli.memory.memory import Memory


# ── Memory store write/read cycle ────────────────────────────────────────

def _write_memories(tmp_db, count=10):
    conn = sqlite3.connect(str(tmp_db))
    for i in range(count):
        conn.execute(
            "INSERT INTO memories (text, tags, timestamp, kind) VALUES (?,?,?,?)",
            (f"Memory text {i}: user likes {['python','jazz','dark mode','linux'][i%4]}",
             f"tag_{i%4}", f"2024-01-{i+1:02d}T00:00:00", "memory")
        )
    conn.commit()
    conn.close()


def test_memory_count_after_write(tmp_db):
    _write_memories(tmp_db, 5)
    conn = sqlite3.connect(str(tmp_db))
    count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.close()
    assert count == 5


# ── FTS5 Index ────────────────────────────────────────────────────────────

def test_fts5_table_exists(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "memories_fts" in tables


def test_fts5_no_false_positives(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("INSERT INTO memories (text, kind, timestamp) VALUES (?, ?, ?)",
                 ("User likes coffee in the morning", "memory", 1700000000.0))
    conn.commit()
    conn.close()
    mem = Memory(db_path=str(tmp_db))
    result = mem.recall_memory("elephant aerodynamics quantum")
    assert isinstance(result, list)
    assert len(result) == 0


# ── Noise filtering ───────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", [
    "assistant_insight", "episodic", "reflection"
])
def test_noise_kinds_filtered(tmp_db, kind):
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO memories (text, kind, timestamp) VALUES (?,?,?)",
        (f"This is a {kind} entry", kind, 1700000000.0)
    )
    conn.commit()
    conn.close()
    mem = Memory(db_path=str(tmp_db))
    result = mem.recall_memory(kind)
    for r in result:
        assert r.get("kind") != kind

def test_orchestrator_source_filtered(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO memories (text, source, kind, timestamp) VALUES (?,?,?,?)",
        ("Orchestrator internal message", "orchestrator", "memory", 1700000000.0)
    )
    conn.commit()
    conn.close()
    mem = Memory(db_path=str(tmp_db))
    result = mem.recall_memory("Orchestrator internal")
    for r in result:
        assert r.get("source") != "orchestrator"


# ── Large DB performance ──────────────────────────────────────────────────

def test_large_db_recall_performance(tmp_db):
    """Recall should complete in reasonable time even with 500 entries."""
    conn = sqlite3.connect(str(tmp_db))
    for i in range(500):
        conn.execute(
            "INSERT INTO memories (text, tags, timestamp, kind) VALUES (?,?,?,?)",
            (f"Memory entry {i} with {['jazz','python','linux','work'][i%4]} content",
             f"tag_{i%4}", float(i), "memory")
        )
    conn.commit()
    conn.close()

    import time
    mem = Memory(db_path=str(tmp_db))
    start = time.time()
    result = mem.recall_memory("jazz music preferences", limit=10)
    elapsed = time.time() - start
    assert isinstance(result, list)
    assert elapsed < 5.0, f"Recall took too long: {elapsed:.2f}s"
