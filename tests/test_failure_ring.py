"""Tests for eli.core.failure_ring — structured failure observability."""
from __future__ import annotations

import eli.core.failure_ring as fr


def setup_function():
    fr.clear()


def test_record_and_recent():
    fr.record("test.mod", "context", exc_type="ValueError", exc_msg="bad")
    items = fr.recent(limit=10)
    assert len(items) == 1
    assert items[0]["module"] == "test.mod"
    assert items[0]["exc_type"] == "ValueError"
    assert items[0]["exc_msg"] == "bad"


def test_ring_buffer_respects_capacity():
    for i in range(fr.capacity() + 10):
        fr.record("mod", f"ctx-{i}", exc_msg=str(i))
    items = fr.recent(limit=fr.capacity() + 5)
    assert len(items) == fr.capacity()
    assert items[-1]["exc_msg"] == str(fr.capacity() + 9)


def test_record_suppressed_captures_exception():
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        fr.record_suppressed(__name__, "unit test", exc=e)
    items = fr.recent()
    assert len(items) == 1
    assert items[0]["exc_type"] == "RuntimeError"
    assert "boom" in items[0]["exc_msg"]


def test_stats():
    fr.record("a", exc_msg="1")
    fr.record("b", exc_msg="2")
    s = fr.stats()
    assert s["count"] == 2
    assert s["by_module"]["a"] == 1
