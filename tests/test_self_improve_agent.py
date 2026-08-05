"""SELF_IMPROVE routed through the coding agent (decompose→solve→verify, propose-only)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import eli.runtime.self_improvement as SI


def _fake_cr(solved=True, score=0.97):
    return MagicMock(solved=solved, score=score, plan={"approach": "targeted patch"},
                     message="solved", code="def f():\n    return 1\n")


def test_propose_via_agent_orchestrates_failures():
    eng = SI.get_self_improvement()
    fails = [{"error": 'File "eli/x.py" boom', "user_input": "do x"},
             {"error": "err2", "user_input": "do y"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", return_value=_fake_cr()):
            r = eng.propose_via_agent(max_items=2)
    assert r["ok"] and r["count"] == 2
    assert all(p["verified"] for p in r["proposals"])
    # ran as a parallel DAG layer (orchestrated, not a sequential loop)
    assert r["orchestration"]["layers"] == [["fix_0", "fix_1"]]


def test_propose_via_agent_no_failures():
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=[]):
        r = eng.propose_via_agent()
    assert r["ok"] and r["proposals"] == [] and "no recent failures" in r.get("reason", "")


def test_build_fix_task_inlines_named_file():
    eng = SI.get_self_improvement()
    task = eng._build_fix_task({"error": 'File "eli/core/dag.py", line 1\nBoom', "user_input": "x"})
    assert "Fix the bug" in task and "eli/core/dag.py" in task


def test_self_improve_action_propose_mode_is_backgrounded():
    """propose mode must return IMMEDIATELY with a job id, never run inline.

    Running the coding agent on the chat thread is what froze the GUI for 2+ hours:
    it fans out to tens of sequential 30-500s model calls with nothing emitted until
    it returns."""
    from eli.execution.executor_enhanced import execute
    fails = [{"error": "boom", "user_input": "do x"}]
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", return_value=_fake_cr()):
            r = execute("SELF_IMPROVE", {"mode": "propose"})
    assert r["ok"] and r.get("evidence_source") == "coding_agent"
    assert r.get("backgrounded") is True
    assert isinstance(r.get("job_id"), int)
    assert "background job" in r["content"].lower()


def test_analyze_mode_never_invokes_the_coding_agent():
    """The regression guard for the 2-hour freeze.

    analyze_and_improve() used to call the coding agent whenever the model happened
    to be loaded, so a read-sounding 'analyze' detonated the full repair stack."""
    eng = SI.get_self_improvement()
    fails = [{"error": "boom", "user_input": "do x"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch.object(eng, "propose_via_agent",
                          side_effect=AssertionError("coding agent must not run for analyze")):
            with patch("eli.cognition.gguf_inference.is_loaded", return_value=True):
                out = eng.analyze_and_improve()
    assert out["proposals_made"] == 0


def test_repair_opt_in_still_reaches_the_coding_agent():
    """repair=True must still reach the agent — the fix bounds the path, not removes it."""
    import uuid
    eng = SI.get_self_improvement()
    # A unique failure so it can't be suppressed as already-investigated.
    fails = [{"error": f"boom-{uuid.uuid4().hex[:8]}", "user_input": "do x"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch.object(eng, "propose_via_agent",
                          return_value={"ok": True, "proposals": []}) as spy:
            with patch("eli.cognition.gguf_inference.is_loaded", return_value=True):
                eng.analyze_and_improve(repair=True)
    assert spy.called, "repair=True must invoke the coding-agent proposer"


def test_self_improve_detects_propose_intent_from_text():
    from eli.execution.executor_enhanced import execute
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=[]):
        # default mode=analyze, but the raw text asks to propose fixes → routes to propose
        r = execute("SELF_IMPROVE",
                    {"_raw_user_text": "improve your code: propose verified fixes for the failing tests"})
    assert r["ok"] and r.get("evidence_source") == "coding_agent"
