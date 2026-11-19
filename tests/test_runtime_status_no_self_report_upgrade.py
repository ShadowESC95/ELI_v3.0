"""A question about what ELI is running on is RUNTIME_STATUS in every reasoning mode.

Quick returns the live runtime evidence through the canonical contract surface. Every other mode
goes through the full synthesis pipeline (never the old direct "v8" surface, never SELF_REPORT);
with no model loaded, as here, that pipeline fails closed and still says so as RUNTIME_STATUS.
"""
from __future__ import annotations

import pytest

from eli.kernel.engine import CognitiveEngine

RUNTIME_QUERY = (
    "Who are you and what are you actually running on right now — "
    "model, context size, GPU layers, everything."
)
NONQUICK = ("chain_of_thought", "self_consistency", "tree_of_thoughts", "constitutional_ai")


@pytest.fixture(scope="module")
def answers():
    """One engine, the same question once in each mode."""
    eng = CognitiveEngine()
    return {mode: eng.process(RUNTIME_QUERY, reasoning_mode=mode) for mode in ("quick",) + NONQUICK}


def _visible(out):
    return str(out.get("content") or out.get("response") or "")


def _names_the_runtime(text):
    low = text.lower()
    return "model" in low and ("context" in low or "ctx" in low) and "gpu" in low


def test_quick_returns_the_live_evidence(answers):
    out = answers["quick"]
    assert isinstance(out, dict)
    assert out.get("ok") is True
    assert out.get("action") == "RUNTIME_STATUS"
    assert out.get("evidence_source") == "runtime_status_quick_canonical_contract"
    assert _names_the_runtime(_visible(out))
    # nothing was synthesised for Quick
    assert out.get("synthesis_validated") in (None, False)
    assert (out.get("report") or {}).get("synthesis_validated") in (None, False)


@pytest.mark.parametrize("mode", NONQUICK)
def test_every_other_mode_stays_runtime_status_through_the_full_pipeline(answers, mode):
    out = answers[mode]
    assert isinstance(out, dict)
    assert out.get("action") == "RUNTIME_STATUS"
    assert out.get("evidence_source") != "runtime_status_grounded_dynamic_evidence_v8"
    assert str(out.get("source") or "").startswith("runtime_status_nonquick_full_pipeline")
    text = _visible(out)
    assert text.strip()
    if (out.get("report") or {}).get("synthesis_validated") is True:      # a model answered
        assert _names_the_runtime(text)
