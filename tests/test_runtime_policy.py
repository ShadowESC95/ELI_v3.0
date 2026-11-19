"""Tests for eli.runtime.control_contracts and the output governor."""
from __future__ import annotations

import pytest

from eli.runtime.control_contracts import (
    CONTROL_ACTIONS,
    build_control_evidence,
    finalise_control_result,
    normalise_action,
    is_control_action,
)


# ── CONTROL_ACTIONS ───────────────────────────────────────────────────────


def test_memory_recall_is_control():
    assert "MEMORY_RECALL" in CONTROL_ACTIONS

def test_runtime_status_is_control():
    assert "RUNTIME_STATUS" in CONTROL_ACTIONS

def test_memory_status_is_control():
    assert "MEMORY_STATUS" in CONTROL_ACTIONS

@pytest.mark.parametrize("action", [
    "RUNTIME_STATUS", "MEMORY_STATUS", "COGNITION_STATUS",
    "USER_IDENTITY_SUMMARY", "SELF_REPORT", "MEMORY_RECALL",
])
def test_standard_actions_in_control(action):
    assert action in CONTROL_ACTIONS


# ── normalise_action ──────────────────────────────────────────────────────

def test_normalise_action_uppercase():
    assert normalise_action("chat") == "CHAT"

def test_normalise_action_already_upper():
    assert normalise_action("CHAT") == "CHAT"

def test_normalise_action_strips_whitespace():
    assert normalise_action("  CHAT  ") == "CHAT"


# ── is_control_action ─────────────────────────────────────────────────────

def test_is_control_action_true():
    assert is_control_action("RUNTIME_STATUS")


def test_is_control_action_memory_recall():
    assert is_control_action("MEMORY_RECALL")

def test_is_control_action_unknown():
    assert not is_control_action("NOT_A_REAL_ACTION_XYZ")


class _DummyEngine:
    def __init__(self):
        self.stored = []
        self._last_request_meta = {
            "request_id": "req-000001",
            "route_action": "RUNTIME_STATUS",
            "result_action": "CHAT",
            "confidence": 0.99,
            "confidence_label": "very high",
            "agents_used": ["introspection"],
            "evidence_used": True,
            "grounded": True,
        }

    def _store_assistant_turn(self, text):
        self.stored.append(text)


class _DummyBus:
    aggregated_confidence = 0.54
    agents_used = ["file_code", "introspection", "reflection"]
    orchestrator_plan = None


def test_last_response_control_uses_trace_not_clarifier():
    engine = _DummyEngine()
    evidence = build_control_evidence(
        engine,
        "EXPLAIN_LAST_RESPONSE",
        {},
        "What's your confidence in your last response and which agents contributed?",
    )

    result = finalise_control_result(
        engine,
        "What's your confidence in your last response and which agents contributed?",
        "EXPLAIN_LAST_RESPONSE",
        evidence,
        bus_result=_DummyBus(),
        synthesized_text="What specific information were you looking for?",
    )

    text = result["content"]
    assert "What specific information" not in text
    assert "Last-response trace:" in text
    assert "RUNTIME_STATUS" in text
    assert "introspection" in text


def test_cognition_runtime_uses_grounded_synthesis_when_valid():
    """Non-quick reasoning modes are honoured: a synthesis that is grounded
    in the evidence (no fabricated paths, no scaffolding leakage, no
    evasive phrasing) is the user-facing answer. Quick mode supplies an
    empty synthesized_text and falls back to compact evidence — that path
    is covered by `test_cognition_runtime_control_falls_back_when_synthesis_is_evasive`.
    """
    engine = _DummyEngine()
    evidence = {
        "ok": True,
        "action": "EXPLAIN_COGNITION_RUNTIME",
        "content": "Cognition runtime surface:\n- live_orchestration_surface: /repo/eli/kernel/engine.py\n- pipeline_stage_count: 12",
        "response": "Cognition runtime surface:\n- live_orchestration_surface: /repo/eli/kernel/engine.py\n- pipeline_stage_count: 12",
        "report": {"ok": True},
        "evidence_source": "executor",
    }

    result = finalise_control_result(
        engine,
        "Explain your cognition pipeline",
        "EXPLAIN_COGNITION_RUNTIME",
        evidence,
        bus_result=_DummyBus(),
        synthesized_text="I route the request through my live orchestration surface and use the 12-stage cognition pipeline from the evidence.",
    )

    text = result["content"]
    assert "I route the request" in text
    assert "12-stage cognition pipeline" in text


def test_cognition_runtime_control_falls_back_when_synthesis_is_evasive():
    engine = _DummyEngine()
    evidence = {
        "ok": True,
        "action": "EXPLAIN_COGNITION_RUNTIME",
        "content": "Cognition runtime surface:\n- live_orchestration_surface: /repo/eli/kernel/engine.py\n- pipeline_stage_count: 12",
        "response": "Cognition runtime surface:\n- live_orchestration_surface: /repo/eli/kernel/engine.py\n- pipeline_stage_count: 12",
        "report": {"ok": True},
        "evidence_source": "executor",
    }

    result = finalise_control_result(
        engine,
        "Explain your cognition pipeline",
        "EXPLAIN_COGNITION_RUNTIME",
        evidence,
        bus_result=_DummyBus(),
        synthesized_text="What specific information were you looking for?",
    )

    text = result["content"]
    assert text.startswith("Cognition runtime surface:")
    assert "What specific information" not in text


# ─────────────────────────────────────────────────────────────────────
# Output governor: validate_against_evidence
# ─────────────────────────────────────────────────────────────────────

from pathlib import Path as _GovernorPath  # noqa: E402

from eli.cognition.output_governor import validate_against_evidence


def _foreign_home_path(suffix: str = "bin/script.py") -> str:
    """Build `/home/<other_user>/...` guaranteed not to match the live user.

    Used so governor fixtures stay portable: no test ever pins a literal
    home directory under a specific username, which would only catch
    hallucinations on one developer's machine. The cross-user check in the
    governor resolves the live user via `Path.home().name` at runtime.
    """
    live = (_GovernorPath.home().name or "_unknown_") + "_NOT"
    return f"/home/{live}/{suffix}"


def test_governor_passes_grounded_text():
    r = validate_against_evidence(
        "ELI is loaded with n_ctx: 8192 and the model at /repo/models/foo.gguf.",
        "n_ctx: 8192\nmodel_path: /repo/models/foo.gguf",
    )
    assert r["ok"]
    assert not r["unsafe"]
    assert r["violations"] == []


def test_governor_flags_generic_fabricated_signature():
    """Generic system-prefix and filename signatures are flagged on any host."""
    r = validate_against_evidence(
        "Resolved paths:\n- /usr/local/lib/eli/packages/gpu_status.so\n- All checks pass.",
        "project_root: /repo",
    )
    assert r["unsafe"]
    kinds = {v["kind"] for v in r["violations"]}
    assert "fabricated_signature" in kinds
    assert "gpu_status.so" not in r["sanitized"]


def test_governor_flags_cross_user_home_path():
    """Paths under `/home/<other_user>/` not in evidence are catastrophic.
    Detector resolves the live user at runtime so the governor stays portable."""
    foreign = _foreign_home_path()
    r = validate_against_evidence(
        f"Resolved paths:\n- {foreign}\n- All checks pass.",
        "project_root: /repo",
    )
    assert r["unsafe"]
    kinds = {v["kind"] for v in r["violations"]}
    assert "fabricated_signature" in kinds
    assert foreign not in r["sanitized"]


def test_governor_passes_live_user_home_path_in_evidence():
    """Paths under the live user's home directory that are also in evidence pass cleanly."""
    own = f"/home/{_GovernorPath.home().name}/proj/example/file.py"
    r = validate_against_evidence(
        f"The file lives at {own}.",
        f"path: {own}",
    )
    assert r["ok"]
    assert not r["unsafe"]


def test_governor_flags_scaffolding_leakage():
    r = validate_against_evidence(
        "1. Detailed Pipeline Explanation\n   Core Idea: Provide a detailed.\n   Feasibility: 8/10",
        "evidence: any",
    )
    assert r["unsafe"]
    kinds = {v["kind"] for v in r["violations"]}
    assert "scaffolding_leakage" in kinds


def test_governor_flags_fabricated_runtime_value():
    r = validate_against_evidence(
        "The model is loaded with n_ctx=99999 layers.",
        "n_ctx: 8192",
    )
    assert any(v["kind"] == "fabricated_runtime_value" for v in r["violations"])


def test_governor_flags_evasive_phrase_as_unsafe():
    r = validate_against_evidence(
        "What specific information were you looking for?",
        "evidence available",
    )
    assert r["unsafe"]
    kinds = {v["kind"] for v in r["violations"]}
    assert "evasive_phrase" in kinds


def test_governor_flags_truncated_output():
    truncated = "This is the answer and it goes on for many words but then suddenly cuts mid-thoug"
    r = validate_against_evidence(truncated, "evidence")
    kinds = {v["kind"] for v in r["violations"]}
    assert "truncated" in kinds


def test_governor_strip_silent_removes_violating_lines():
    foreign = _foreign_home_path("bin/foo.py")
    out = (
        "Resolved paths:\n"
        f"- {foreign}\n"
        "- /repo/eli/kernel/engine.py\n"
        "All checks passed."
    )
    ev = "project_root: /repo\n/repo/eli/kernel/engine.py"
    r = validate_against_evidence(out, ev, mode="strip_silent")
    assert foreign not in r["sanitized"]
    assert "/repo/eli/kernel/engine.py" in r["sanitized"]
    assert "All checks passed." in r["sanitized"]


def test_governor_mark_inline_wraps_violators():
    foreign = _foreign_home_path("bin/foo.py")
    r = validate_against_evidence(
        f"I checked {foreign} earlier.",
        "project_root: /repo",
        mode="mark_inline",
    )
    assert "<unverified:" in r["sanitized"]


def test_governor_empty_output_is_unsafe():
    r = validate_against_evidence("", "evidence here")
    assert r["unsafe"]
    assert r["violations"][0]["kind"] == "empty"
