from __future__ import annotations

import pytest


def test_mk_accepts_meta_extra_and_merges_into_meta():
    from eli.execution.routing._helpers import _mk

    out = _mk(
        "CHAT",
        {"message": "hi"},
        0.92,
        matched_by="chat.example",
        meta_extra={"response_contract": "nonquick_continuity_diagnostic",
                    "requires_plan": True},
    )

    assert out["action"] == "CHAT"
    assert out["meta"]["matched_by"] == "chat.example"
    assert out["meta"]["response_contract"] == "nonquick_continuity_diagnostic"
    assert out["meta"]["requires_plan"] is True


def test_named_parameters_win_over_meta_extra():
    """meta_extra must not let a caller quietly redefine the audited fields."""
    from eli.execution.routing._helpers import _mk

    out = _mk("CHAT", {}, 0.5, matched_by="real.source",
              meta_extra={"matched_by": "spoofed"})

    assert out["meta"]["matched_by"] == "real.source"


@pytest.mark.parametrize("text", [
    "can you continuity",
    "can you chain of thought",
    "why are you giving me short answers",
])
def test_meta_continuity_diagnostic_guard_actually_fires(text):
    """Regression: this guard called _mk(meta_extra=...) while _mk had no such
    parameter, so it raised TypeError on every match. The priority pipeline
    swallowed it, silently downgrading these to bare CHAT with no contract."""
    import eli.execution.router_enhanced as R

    out = R._ROUTE_CORE(text)

    assert out["action"] == "CHAT"
    meta = out.get("meta") or {}
    assert meta.get("matched_by") == "chat.meta_continuity_diagnostic"
    assert meta.get("response_contract") == "nonquick_continuity_diagnostic"
    assert meta.get("requires_plan") is True
    assert meta.get("quick_direct_allowed") is False


def test_core_route_does_not_raise_on_continuity_vocabulary():
    """The whole trigger vocabulary must route, not raise."""
    import eli.execution.router_enhanced as R

    for token in ("one answer", "short answer", "lobotom", "continuity",
                  "awareness", "reasoning mode", "constitutional",
                  "chain of thought", "pipeline", "fallback",
                  "runtime audit", "what is with", "why are you"):
        R._ROUTE_CORE(f"can you {token}")
