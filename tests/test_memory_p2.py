"""Tests for P2 memory conflict resolution and audit."""
from __future__ import annotations

import tempfile
from pathlib import Path

from eli.memory.conflict_resolution import find_conflicts, parse_fact, resolve_conflicts
from eli.runtime.memory_audit import explain_belief


def test_parse_fact_extracts_subject_value():
    assert parse_fact("My favorite color is blue") == ("favorite color", "blue")
    assert parse_fact("name: Alice") == ("name", "alice")


def test_conflict_detection_and_resolution():
    with tempfile.TemporaryDirectory() as td:
        from eli.memory.memory import Memory
        db = Path(td) / "mem.sqlite3"
        mem = Memory(db_path=str(db))
        mem.store_memory("My favorite color is blue", source="user", kind="memory")
        conflicts = find_conflicts(mem, "My favorite color is red")
        assert len(conflicts) == 1
        assert conflicts[0].old_value == "blue"
        assert conflicts[0].new_value == "red"
        result = resolve_conflicts(mem, conflicts)
        assert result["resolved"] == 1


def test_explain_belief_returns_text():
    text = explain_belief("runtime status")
    assert "Memory audit" in text
    assert isinstance(text, str)
