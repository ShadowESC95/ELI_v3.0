"""The model's own confidence in the tokens of a reply (gguf_inference._TokenConfidence).

A user-facing generation carries a logits processor that reads, step by step, how sure the model
was of each token it chose; the summary goes onto the turn's facts, and from there into the reply's
calibrated chance (epistemics.FEATURES). A grammar-held call and background work carry none."""
from __future__ import annotations

import pytest

from eli.cognition import gguf_inference as g
from eli.kernel.request_context import turn_facts_var
from tests.test_gguf_kv_slot_recovery import _Llm, _patch_helpers


class _Seen(_Llm):
    def __init__(self):
        super().__init__(fail_times=0)
        self.kwargs = []

    def __call__(self, prompt, **kw):
        self.kwargs.append(kw)
        processors = kw.get("logits_processor")
        if processors:
            import numpy as np
            logits = np.array([1.0, 0.0, -1.0], dtype=np.float32)
            for step in range(4):                  # the processor sees each step's logits and the token chosen
                for p in processors:
                    p(np.array([7] + [0] * step), logits)
        if kw.get("stream"):
            return iter([{"choices": [{"text": "a"}]}, {"choices": [{"text": "b"}]}])
        return {"choices": [{"text": "ab"}], "usage": {"completion_tokens": 2}}


def _invoke(llm, **over):
    kw = dict(temperature=0.7, max_tokens=64, top_p=0.9, top_k=40, repeat_penalty=1.1, stop=[], stream=False, grammar=None)
    kw.update(over)
    return g._safe_invoke_llm(llm, "prompt", **kw)


def test_a_user_facing_generation_puts_its_token_confidence_on_the_turn(monkeypatch):
    _patch_helpers(monkeypatch, [])
    llm = _Seen()
    token = turn_facts_var.set({"request_id": "t"})
    try:
        _invoke(llm)
        facts = turn_facts_var.get()
    finally:
        turn_facts_var.reset(token)
    assert llm.kwargs[0].get("logits_processor") is not None
    gen = facts["generation"]
    assert gen["tokens"] == 3 and gen["calls"] == 1 and gen["mean_logprob"] < 0 and 0 <= gen["low_share"] <= 1
    assert set(gen) >= {"mean_logprob", "min_logprob", "low_share", "mean_entropy", "first_entropy"}


def test_the_streaming_path_settles_the_figures_when_the_stream_ends(monkeypatch):
    _patch_helpers(monkeypatch, [])
    llm = _Seen()
    token = turn_facts_var.set({"request_id": "t"})
    try:
        chunks = list(_invoke(llm, stream=True))
        facts = turn_facts_var.get()
    finally:
        turn_facts_var.reset(token)
    assert [c["choices"][0]["text"] for c in chunks] == ["a", "b"]
    assert facts["generation"]["tokens"] == 3


def test_a_second_call_in_the_turn_counts_and_serves_its_own_figures(monkeypatch):
    _patch_helpers(monkeypatch, [])
    llm = _Seen()
    token = turn_facts_var.set({"request_id": "t"})
    try:
        _invoke(llm)
        _invoke(llm)
        assert turn_facts_var.get()["generation"]["calls"] == 2
    finally:
        turn_facts_var.reset(token)


def test_a_grammar_held_call_and_background_work_carry_no_recorder(monkeypatch):
    _patch_helpers(monkeypatch, [])
    llm = _Seen()
    token = turn_facts_var.set({"request_id": "t"})
    try:
        _invoke(llm, grammar=object())
        assert "logits_processor" not in llm.kwargs[-1] and "generation" not in turn_facts_var.get()
        monkeypatch.setattr(g, "is_background_inference", lambda: True)
        monkeypatch.setattr(g, "foreground_recently_active", lambda *_a, **_k: False, raising=False)
        _invoke(llm)
        assert "logits_processor" not in llm.kwargs[-1] and "generation" not in turn_facts_var.get()
    finally:
        turn_facts_var.reset(token)


def test_the_reply_s_assessment_carries_the_token_figures(monkeypatch):
    """engine._publish_last_response_meta hands the turn's generation figures to epistemics.assess."""
    from eli.cognition import epistemics as ep
    seen = {}

    def fake_assess(kind, score, *, request_id, features=None, class_="", db_path=None):
        seen["features"] = dict(features or {})
        return ep.Probability(p=float(score), kind=kind, score=float(score), class_=class_)

    monkeypatch.setattr(ep, "assess", fake_assess)
    from eli.kernel.engine import CognitiveEngine
    eng = CognitiveEngine()
    token = turn_facts_var.set({"request_id": "req-9", "generation": {
        "tokens": 40, "calls": 1, "mean_logprob": -0.6, "min_logprob": -4.1, "low_share": 0.2,
        "mean_entropy": 1.1, "first_entropy": 2.0}})
    try:
        eng._publish_last_response_meta({"request_id": "req-9"}, action="CHAT", result_action="CHAT",
                                        confidence=0.8, response="the kettle takes ninety seconds")
    finally:
        turn_facts_var.reset(token)
    feats = seen.get("features") or {}
    assert feats.get("mean_logprob") == -0.6 and feats.get("min_logprob") == -4.1 and feats.get("tokens") == 40
    assert feats.get("first_entropy") == 2.0 and feats.get("low_share") == 0.2
