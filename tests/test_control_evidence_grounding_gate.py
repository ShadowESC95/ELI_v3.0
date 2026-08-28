"""Integration: control_contracts prefers deterministic_grounding_gate for surface actions."""
from __future__ import annotations

from unittest.mock import MagicMock

from eli.runtime.control_contracts import build_control_evidence


def test_self_report_uses_deterministic_grounding_gate():
    engine = MagicMock()
    result = build_control_evidence(
        engine,
        "SELF_REPORT",
        {},
        "who are you",
        intent={"reasoning_mode": "quick"},
    )
    assert result.get("ok") is True
    assert result.get("evidence_source") == "deterministic_grounding_gate"
    text = str(result.get("response") or result.get("content") or "")
    assert text.strip()
    assert not text.strip().startswith("{")
    assert "generator object" not in text
