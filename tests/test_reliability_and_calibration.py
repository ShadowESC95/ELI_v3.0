"""Measured reliability per action, success predictions scored against what happened, and honest absence wording."""
import pytest

from eli.runtime import evidence_ledger as led
from eli.runtime.evidence_arbitration import EvidenceState, absence_statement


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    return path


def _run(db, action, ok, t, predicted=None):
    payload = {"ok": ok}
    if predicted is not None:
        payload["predicted"] = predicted
    led.record_event("tool_execution", action=action, subject=f"s{t}", payload=payload, outcome="ok" if ok else "failed", db_path=db, timestamp=t)


def test_no_prediction_without_history(db):
    assert led.predict_success("SCREENSHOT", db_path=db) is None


def test_reliability_follows_recent_outcomes(db):
    import time
    now = time.time()
    for i in range(6):
        _run(db, "SCREENSHOT", False, now - 100 + i * 15)
    for i in range(6):
        _run(db, "OPEN_APP", True, now - 100 + i * 15)
    assert led.predict_success("SCREENSHOT", db_path=db) < 0.25 < 0.75 < led.predict_success("OPEN_APP", db_path=db)
    assert [r["action"] for r in led.unreliable_actions(db_path=db)] == ["SCREENSHOT"]


def test_recent_runs_count_more_than_old_ones(db):
    import time
    now = time.time()
    for i in range(6):
        _run(db, "GPU_STATUS", False, now - 25 * 86400 + i * 15)
    for i in range(6):
        _run(db, "GPU_STATUS", True, now - 3600 + i * 15)
    assert led.predict_success("GPU_STATUS", db_path=db) > 0.7


def test_calibration_scores_predictions_against_outcomes(db):
    import time
    now = time.time()
    for i in range(10):
        _run(db, "A", True, now - 500 + i * 15, predicted=0.9)
    for i in range(10):
        _run(db, "B", i % 2 == 0, now - 300 + i * 15, predicted=0.9)
    rep = led.calibration_report(db_path=db)
    assert rep["n"] == 20 and 0.0 < rep["brier"] < 0.5
    band = [b for b in rep["bands"] if b["range"].startswith("0.80")][0]
    assert band["predicted"] == 0.9 and band["observed"] == 0.75


def test_absence_is_worded_by_why_it_is_absent():
    assert "looked for" in absence_statement(EvidenceState.NOT_FOUND, "the lighthouse code")
    assert "have not looked" in absence_statement(EvidenceState.NOT_INSPECTED, "your screen")
    assert "failed" in absence_statement(EvidenceState.INSPECTION_FAILED, "the GPU")
    assert absence_statement("something else", "x") == ""


def test_the_scope_of_a_search_is_stated_so_a_denial_covers_only_that(monkeypatch):
    from eli.cognition import memory_diag
    rec = memory_diag.retrieval_record(2, 0, 0, 0, None, searched={"semantic": 0, "exact": 1, "claims": 0, "archive": None})
    text = memory_diag.block(rec)
    assert "archive: not searched" in text and "exact codes/phrases 1" in text and "Only say nothing is stored for this scope" in text


def test_the_archive_is_searched_when_live_evidence_is_thin(tmp_path, monkeypatch):
    import sqlite3
    from eli.memory.memory import Memory
    from eli.memory.retrieval import retrieve_for_turn
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    mem.store_memory("The old warehouse alarm code was ZEBRA-4410 before the refit last year", source="assistant", kind="fact", importance=0.2)
    c = sqlite3.connect(mem.db_path)
    c.execute("update memories set timestamp = timestamp - 4000 * 86400, ts = ts - 4000 * 86400, last_seen = last_seen - 4000 * 86400, event_ts = event_ts - 4000 * 86400")
    c.commit()
    c.close()
    mem.apply_weight_decay()
    assert mem.archive_faded()["archived"] == 1
    res = retrieve_for_turn(mem, "what was the old warehouse alarm code", use_cache=False, rerank=False)
    assert res.searched["archive"] == 1
    assert [h for h in res.semantic_hits if str(h.get("id")).startswith("archive:")]


def test_exact_codes_are_looked_up_as_written(tmp_path, monkeypatch):
    from eli.memory.memory import Memory
    from eli.memory.retrieval import retrieve_for_turn
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    mem.store_memory("The validation lighthouse code is ORCHID-7319 and the bird is a silver kestrel", source="user")
    res = retrieve_for_turn(mem, "which fact mentions ORCHID-7319", use_cache=False, rerank=False)
    assert res.searched["semantic"] + res.searched["exact"] >= 1
    assert "orchid-7319" in " ".join(str(h.get("text") or "") for h in res.semantic_hits).lower()


# ── a figure that means something: calibrated chances (eli/cognition/epistemics.py) ────────────
# The scores ELI gives a reply are engineering indices, not chances of being right. A chance is what
# they become once they are checked against what the next turn said: of the answers given "0.9",
# what share proved right. With nothing recorded the chance is the score; it moves as outcomes say.

from eli.cognition import epistemics as ep


def _settled(db, *, score, right, n, prefix, class_=""):
    for i in range(n):
        ep.assess("answer", score, request_id=f"{prefix}{i}", class_=class_, db_path=db)
        ep.settle(f"{prefix}{i}", right=right(i), db_path=db)


def test_with_nothing_recorded_the_chance_is_the_score(db):
    p = ep.calibrate("answer", 0.8, db_path=db)
    assert p.p == pytest.approx(0.8) and p.n == 0 and p.ece is None and (p.slope, p.shift) == (1.0, 0.0)


def test_scores_that_prove_too_sure_are_brought_down_and_the_error_measured(db):
    _settled(db, score=0.9, right=lambda i: i % 2 == 0, n=12, prefix="r")     # "0.9" right half the time
    p = ep.calibrate("answer", 0.9, db_path=db)
    assert p.n == 12 and 0.45 < p.p < 0.75
    rep = ep.report("answer", db_path=db)
    assert rep["n"] == 12 and rep["ece_calibrated"] < rep["ece"] and rep["brier_calibrated"] < rep["brier"]
    assert rep["bands"][0]["predicted"] == 0.9 and rep["bands"][0]["observed"] == 0.5


def test_scores_that_prove_right_are_left_about_where_they_are(db):
    _settled(db, score=0.8, right=lambda i: i != 0, n=10, prefix="a")           # "0.8" right nine times in ten
    assert abs(ep.calibrate("answer", 0.8, db_path=db).p - 0.8) < 0.1


def test_a_class_with_outcomes_of_its_own_is_fitted_on_them_else_on_the_kind(db):
    _settled(db, score=0.9, right=lambda i: True, n=8, prefix="g", class_="grounded")
    _settled(db, score=0.9, right=lambda i: False, n=8, prefix="c", class_="chat")
    assert ep.calibrate("answer", 0.9, class_="grounded", db_path=db).p > 0.8
    assert ep.calibrate("answer", 0.9, class_="chat", db_path=db).p < 0.6
    whole = ep.calibrate("answer", 0.9, class_="other", db_path=db)
    assert whole.class_ == "" and whole.n == 16, "a class with no outcomes yet falls back to the kind"


def test_a_figure_nothing_settled_yet_does_not_count(db):
    ep.assess("answer", 0.7, request_id="open", db_path=db)
    assert ep.calibrate("answer", 0.7, db_path=db).n == 0


def test_the_scoring_rules_and_the_entropy_of_a_vote():
    assert ep.brier([(1.0, 1.0), (0.0, 0.0)]) == 0.0 and ep.brier([(1.0, 0.0)]) == 1.0
    assert ep.ece([(0.9, 1.0)] * 9 + [(0.9, 0.0)]) == pytest.approx(0.0, abs=1e-9)   # 0.9 given, 0.9 right
    assert ep.ece([(0.9, 0.0)] * 10) == pytest.approx(0.9, abs=1e-9)
    assert ep.entropy([5]) == 0.0 and ep.entropy([1, 1, 1, 1, 1]) == pytest.approx(1.0)
    assert 0.0 < ep.entropy([3, 2]) < ep.entropy([2, 2, 1]) < 1.0
    assert ep.fit([]) == (1.0, 0.0)


def test_the_next_turn_says_whether_the_last_answer_was_right():
    from eli.cognition.correction_patterns import is_answer_correction, is_endorsement
    for said in ("thanks", "Thanks, now search for the other one", "perfect", "yes that's right", "spot on"):
        assert is_endorsement(said) and not is_answer_correction(said), said
    for said in ("no, that's not true", "yes", "ok", "what about the other one", "right, so what is the capital"):
        assert not is_endorsement(said), said


def test_an_aside_a_new_request_or_the_users_news_settles_nothing():
    """These settle every figure ELI learns from: an opening 'cool' or 'great' (sarcastic, or a new
    request) settled the last reply right, and plain sentences settled it wrong with a surprise."""
    from eli.cognition.correction_patterns import is_answer_correction, is_endorsement
    for said in ("ok great", "great, another wrong answer", "cool, what's the weather tomorrow",
                 "got it wrong again didn't you", "nice try", "ta-da, finished the essay",
                 "got it, now play some music"):
        assert not is_endorsement(said), said
    for said in ("Thanks! I never knew that", "I didn't have time to try it yet", "I've been through a lot today",
                 "what do you mean by entropy here?", "No I think you're right"):
        assert not is_answer_correction(said), said
    for said in ("that's not what I asked", "no, the river is longer than that", "I never said that",
                 "where did you get that from?", "you lied"):
        assert is_answer_correction(said), said


# ── the model's own confidence in the tokens of a reply, read beside the score ──────────────────
# gguf_inference records, for each user-facing generation, how sure the model was of the tokens it
# chose (epistemics.FEATURES); the fit weighs each figure by what the outcomes have shown it to tell.

def test_with_no_features_the_fit_is_the_score_alone():
    weights, shift = ep.fit_rows([])
    assert weights == [1.0] + [0.0] * len(ep.FEATURES) and shift == 0.0
    assert ep.apply_rows(0.7, None, weights, shift) == pytest.approx(0.7)
    assert ep.apply_rows(0.7, {"mean_logprob": -2.0}, weights, shift) == pytest.approx(0.7)


def test_a_token_figure_that_tells_right_from_wrong_gains_weight(db):
    # the same score every time; the replies the model was unsure of token by token proved wrong
    for i in range(16):
        sure = i % 2 == 0
        ep.assess("answer", 0.8, request_id=f"tok{i}", features={"mean_logprob": -0.3 if sure else -2.5},
                  db_path=db)
        ep.settle(f"tok{i}", right=sure, db_path=db)
    sure = ep.calibrate("answer", 0.8, features={"mean_logprob": -0.3}, db_path=db)
    unsure = ep.calibrate("answer", 0.8, features={"mean_logprob": -2.5}, db_path=db)
    assert sure.p > 0.8 > unsure.p and unsure.p < 0.5
    assert sure.weights["mean_logprob"] > 0.5
    assert ep.report("answer", db_path=db)["weights"]["mean_logprob"] == pytest.approx(sure.weights["mean_logprob"])


def test_a_token_figure_that_tells_nothing_keeps_no_weight(db):
    for i in range(16):
        ep.assess("answer", 0.8, request_id=f"same{i}", features={"mean_entropy": 1.0 + (i % 3) * 0.1}, db_path=db)
        ep.settle(f"same{i}", right=i % 4 != 0, db_path=db)
    p = ep.calibrate("answer", 0.8, features={"mean_entropy": 1.1}, db_path=db)
    assert abs(p.weights["mean_entropy"]) < 0.5
    assert abs(p.weights["min_logprob"]) < 1e-6 and abs(p.weights["low_share"]) < 1e-6, "never seen: the prior's zero"


def test_the_token_recorder_settles_each_chosen_token_one_step_later():
    import math
    import numpy as np
    from eli.cognition.gguf_inference import _TokenConfidence
    rec = _TokenConfidence()
    logits = np.array([2.0, 1.0, 0.0, -1.0], dtype=np.float32)
    rec(np.array([5]), logits)                       # step 1: the prompt's last token in, nothing settled
    assert rec.logprobs == [] and len(rec.entropies) == 1
    rec(np.array([5, 1]), logits)                    # step 2: token 1 was chosen from step 1's distribution
    lse = math.log(sum(math.exp(v) for v in logits))
    assert rec.logprobs == [pytest.approx(1.0 - lse)]
    rec(np.array([5, 1, 0]), logits)                 # step 3: token 0, the likeliest
    s = rec.summary()
    assert s["tokens"] == 2 and s["min_logprob"] == pytest.approx(1.0 - lse) and s["max_logprob" if False else "mean_logprob"] < 0
    assert s["low_share"] == 0.5 and s["first_entropy"] == pytest.approx(s["mean_entropy"])
    assert _TokenConfidence().summary() == {}
