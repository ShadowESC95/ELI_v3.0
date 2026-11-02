from __future__ import annotations


from eli.kernel.engine import CognitiveEngine

RUNTIME_QUERY = (
    "Who are you and what are you actually running on right now — "
    "model, context size, GPU layers, everything."
)


def test_nonquick_runtime_status_not_self_report_and_not_direct_surface():
    eng = CognitiveEngine()
    out = eng.process(RUNTIME_QUERY, reasoning_mode="constitutional_ai")

    assert isinstance(out, dict)
    assert out.get("action") == "RUNTIME_STATUS"
    assert out.get("action") != "SELF_REPORT"
    assert out.get("evidence_source") != "runtime_status_grounded_dynamic_evidence_v8"
