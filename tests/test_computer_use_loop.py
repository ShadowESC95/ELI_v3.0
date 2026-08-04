"""Computer-use closed loop: bounded, verified, and honest about what it did.

The value of this loop is not that it can click things — SCREEN_LOCATE already could.
It is that every step is confirmed by re-observing the screen, so ELI reports what
actually happened rather than what it intended. These tests pin that property, the
budget/stuck limits that keep an agent driving a real mouse from thrashing, and the
closed step vocabulary.
"""
from __future__ import annotations

import pytest

from eli.execution.effectors import computer_use as cu


@pytest.fixture(autouse=True)
def _no_real_screen(monkeypatch):
    """Never touch a real screen from the test suite."""
    monkeypatch.setattr(cu, "_gate", lambda *a, **k: None)
    monkeypatch.setattr(cu.time, "sleep", lambda *_a, **_k: None)


def _observations(*texts):
    """Yield a scripted sequence of screens, repeating the last forever."""
    seq = list(texts)
    def _obs():
        t = seq.pop(0) if len(seq) > 1 else seq[0]
        import hashlib
        return {"ok": True, "text": t, "ocr": "",
                "digest": hashlib.sha1(t.encode()).hexdigest(), "screenshot_path": ""}
    return _obs


def test_missing_goal_asks_rather_than_acting():
    out = cu._h_computer_use("COMPUTER_USE", {})
    assert out["ok"] is False
    assert "what you want done" in out["content"]


def test_kill_switch_refuses_to_touch_the_screen(monkeypatch):
    monkeypatch.setenv("ELI_COMPUTER_USE", "0")
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "click something"})
    assert out["ok"] is False
    assert "switched off" in out["content"]


def test_blind_loop_refuses_rather_than_pretending(monkeypatch):
    """If the screen can't be seen, the loop must not drive the mouse anyway."""
    monkeypatch.setattr(cu, "_observe", lambda: {"ok": False, "text": "", "ocr": "",
                                                 "digest": "x", "screenshot_path": ""})
    acted = []
    monkeypatch.setattr(cu, "_act", lambda s: acted.append(s))
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "open settings"})
    assert out["ok"] is False
    assert acted == [], "must not act while blind"
    assert "can't see the screen" in out["content"]


def test_completes_when_the_screen_shows_the_goal_is_done(monkeypatch):
    monkeypatch.setattr(cu, "_observe", _observations("settings closed", "dark mode is ON"))
    steps = iter([
        {"step": "LOCATE_AND_CLICK", "target": "dark mode toggle", "text": "", "reason": "flip it"},
        {"step": "DONE", "target": "", "text": "", "reason": "dark mode is on"},
    ])
    monkeypatch.setattr(cu, "_decide", lambda *a: next(steps))
    monkeypatch.setattr(cu, "_act", lambda s: {"ok": True, "detail": "clicked it"})
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "turn on dark mode"})
    assert out["ok"] is True and out["outcome"] == "done"
    assert "Done" in out["content"]


def test_a_click_that_did_not_land_is_never_reported_as_success(monkeypatch):
    """The whole point: no fake actions, including physical ones."""
    monkeypatch.setattr(cu, "_observe", _observations("a screen that never changes"))
    monkeypatch.setattr(cu, "_decide", lambda *a: {
        "step": "LOCATE_AND_CLICK", "target": "submit", "text": "", "reason": "submit it"})
    monkeypatch.setattr(cu, "_act", lambda s: {"ok": False, "detail": "could not find 'submit'"})
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "submit the form"})
    assert out["ok"] is False
    assert all(not s["ok"] for s in out["steps"])
    assert "could not find" in out["content"]


def test_stops_when_stuck_instead_of_burning_the_budget(monkeypatch):
    monkeypatch.setattr(cu, "_observe", _observations("frozen screen"))
    monkeypatch.setattr(cu, "_decide", lambda *a: {
        "step": "LOCATE_AND_CLICK", "target": "next", "text": "", "reason": "advance"})
    monkeypatch.setattr(cu, "_act", lambda s: {"ok": True, "detail": "clicked"})
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "advance the wizard", "max_steps": 30})
    assert out["outcome"] == "stuck"
    assert len(out["steps"]) == cu._NO_PROGRESS_LIMIT, "should stop at the no-progress limit"


def test_step_budget_is_bounded(monkeypatch):
    seen = {"n": 0}
    def obs():
        seen["n"] += 1
        import hashlib
        t = f"screen {seen['n']}"  # always changing, so no-progress never trips
        return {"ok": True, "text": t, "ocr": "",
                "digest": hashlib.sha1(t.encode()).hexdigest(), "screenshot_path": ""}
    monkeypatch.setattr(cu, "_observe", obs)
    monkeypatch.setattr(cu, "_decide", lambda *a: {
        "step": "PRESS_KEY", "target": "Tab", "text": "", "reason": "cycle"})
    monkeypatch.setattr(cu, "_act", lambda s: {"ok": True, "detail": "pressed Tab"})
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "cycle forever", "max_steps": 4})
    assert out["outcome"] == "budget"
    assert len(out["steps"]) == 4


def test_abort_is_surfaced_with_its_reason(monkeypatch):
    monkeypatch.setattr(cu, "_observe", _observations("a login screen"))
    monkeypatch.setattr(cu, "_decide", lambda *a: {
        "step": "ABORT", "target": "", "text": "", "reason": "I don't have the password"})
    out = cu._h_computer_use("COMPUTER_USE", {"goal": "log in"})
    assert out["ok"] is False and out["outcome"] == "abort"
    assert "password" in out["content"]


def test_step_vocabulary_is_closed(monkeypatch):
    """An invented step must degrade to ABORT, not be dispatched."""
    from eli.cognition import gguf_inference
    monkeypatch.setattr(gguf_inference, "chat_completion",
                        lambda *a, **k: '{"step":"FORMAT_HARD_DRIVE","target":"","text":"","reason":"no"}')
    monkeypatch.setattr(cu, "_step_grammar", lambda: None)
    decision = cu._decide("do something", "a screen", [])
    assert decision["step"] == "ABORT"


def test_unreachable_model_aborts_rather_than_guessing(monkeypatch):
    from eli.cognition import gguf_inference
    def boom(*a, **k):
        raise RuntimeError("model not loaded")
    monkeypatch.setattr(gguf_inference, "chat_completion", boom)
    monkeypatch.setattr(cu, "_step_grammar", lambda: None)
    decision = cu._decide("do something", "a screen", [])
    assert decision["step"] == "ABORT"
    assert "could not reach the model" in decision["reason"]


def test_authority_gate_refusal_blocks_the_step(monkeypatch):
    monkeypatch.setattr(cu, "_gate", lambda action, args: "not permitted")
    res = cu._act({"step": "TYPE_TEXT", "target": "", "text": "hello", "reason": ""})
    assert res["ok"] is False and res["detail"] == "not permitted"


def test_grammar_admits_only_the_step_verbs():
    from llama_cpp import LlamaGrammar
    calls = {}
    real = LlamaGrammar.from_string
    def spy(gbnf, *a, **k):
        calls["gbnf"] = gbnf
        return real(gbnf, *a, **k)
    LlamaGrammar.from_string = spy
    try:
        cu._GRAMMAR_CACHE.clear()
        assert cu._step_grammar() is not None
    finally:
        LlamaGrammar.from_string = real
        cu._GRAMMAR_CACHE.clear()
    for verb in cu._STEPS:
        assert '\\"%s\\"' % verb in calls["gbnf"]
    assert "FORMAT_HARD_DRIVE" not in calls["gbnf"]


def test_action_is_registered_and_reachable():
    from eli.execution.effectors.registry import registered_actions
    import eli.execution.executor_enhanced as ex
    assert "COMPUTER_USE" in registered_actions()
    assert "COMPUTER_USE" in ex.SUPPORTED_ACTIONS
