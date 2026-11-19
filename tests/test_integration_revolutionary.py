"""Integration tests — live-path wiring for voice, memory audit, observability."""
from __future__ import annotations

from eli.execution.router_enhanced import route
from eli.runtime.personal_memory_render import render_belief_audit, render_profile_summary


def test_router_memory_audit_belief_question():
    r = route("why did you think I am a developer")
    assert str(r.get("action") or "").upper() == "MEMORY_AUDIT"
    assert r.get("args", {}).get("query")


def test_personal_memory_render_belief_audit():
    text = render_belief_audit("runtime status")
    assert "Memory audit" in text


def test_observability_failure_report():
    from eli.core.observability import observe, recent_failure_report
    try:
        raise ValueError("integration probe")
    except ValueError as e:
        observe(__name__, "integration test", exc=e)
    report = recent_failure_report(limit=4)
    assert "integration probe" in report
