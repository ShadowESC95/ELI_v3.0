"""Tests for the memory audit."""
from __future__ import annotations

from eli.runtime.memory_audit import explain_belief


def test_explain_belief_returns_text():
    text = explain_belief("runtime status")
    assert "Memory audit" in text
    assert isinstance(text, str)
