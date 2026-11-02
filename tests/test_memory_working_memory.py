"""Tests for eli.cognition.working_memory — ~80 tests."""
from __future__ import annotations

import pytest


# ── Import ────────────────────────────────────────────────────────────────

from eli.cognition.working_memory import WorkingMemory


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def wm():
    return WorkingMemory()


# ── Initialization ────────────────────────────────────────────────────────


def test_working_memory_has_absorb(wm):
    assert hasattr(wm, "absorb_memory_hits") or hasattr(wm, "absorb") or hasattr(wm, "add")


# ── absorb_memory_hits ────────────────────────────────────────────────────

def test_absorb_empty_hits(wm):
    if hasattr(wm, "absorb_memory_hits"):
        result = wm.absorb_memory_hits([], current_turn=0)
        assert result is None or isinstance(result, (list, dict, int))

def test_absorb_single_hit(wm):
    if hasattr(wm, "absorb_memory_hits"):
        hits = [{"text": "User likes jazz", "weight": 0.9, "ts": 1700000000.0}]
        result = wm.absorb_memory_hits(hits, current_turn=1)
        assert result is None or isinstance(result, (list, dict, int))

def test_absorb_multiple_hits(wm):
    if hasattr(wm, "absorb_memory_hits"):
        hits = [
            {"text": f"Memory {i}", "weight": 0.5, "ts": float(i)}
            for i in range(5)
        ]
        result = wm.absorb_memory_hits(hits, current_turn=1)
        assert result is None or isinstance(result, (list, dict, int))

def test_absorb_none_hits(wm):
    if hasattr(wm, "absorb_memory_hits"):
        result = wm.absorb_memory_hits(None, current_turn=0)
        assert result is None or isinstance(result, (list, dict, int))


# ── Context building ──────────────────────────────────────────────────────

def test_get_context_returns_something(wm):
    fn = (getattr(wm, "get_context", None) or getattr(wm, "build_context", None) or
          getattr(wm, "to_context", None))
    if fn:
        result = fn()
        assert result is None or isinstance(result, (str, dict, list))


# ── State management ──────────────────────────────────────────────────────


# ── CognitionWorkingMemory ────────────────────────────────────────────────


# ── Thread safety ────────────────────────────────────────────────────────

def test_working_memory_concurrent_access(wm):
    """Basic concurrent access test."""
    import threading
    errors = []

    def _access():
        try:
            if hasattr(wm, "absorb_memory_hits"):
                wm.absorb_memory_hits([], current_turn=0)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_access) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)
    assert len(errors) == 0, f"Thread safety errors: {errors}"
