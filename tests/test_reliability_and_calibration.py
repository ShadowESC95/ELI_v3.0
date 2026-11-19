"""Measured reliability per action, success predictions scored against what happened."""
import pytest

from eli.runtime import evidence_ledger as led


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    return path


def _run(db, action, ok, t, predicted=None, outcome=None):
    """One run as the executor records it: the tool_execution row (with the prediction made before it)
    and the executor_action row the one reliability estimator reads (eli/runtime/action_outcomes)."""
    from eli.runtime import action_outcomes
    payload = {"ok": ok}
    if predicted is not None:
        payload["predicted"] = predicted
    outcome = outcome or ("ok" if ok else "failed")
    led.record_event("tool_execution", action=action, subject=f"s{t}", payload=payload, outcome=outcome, db_path=db, timestamp=t)
    led.record_event("executor_action", source="executor.post_dispatch", action=action, subject=f"s{t}",
                     payload={"args": {}, "result": {"ok": ok}}, outcome=outcome, db_path=db, timestamp=t)
    action_outcomes.forget()


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


def test_a_run_that_changed_nothing_counts_against_the_action_in_the_prediction_too(db):
    """Two estimators of 'will this action work' disagreed: the outcome report counted a run that
    changed nothing as a failure, the executor's prediction (tool_execution rows) ignored it, so ELI
    predicted success for what its own report said usually fails. One estimator now serves both."""
    import time
    from eli.runtime import action_outcomes
    now = time.time()
    for i in range(6):
        _run(db, "OPEN_APP", True, now - 100 + i * 15, outcome="no_effect")
    for i in range(6):
        _run(db, "DATE", True, now - 100 + i * 15)
    predicted = led.predict_success("OPEN_APP", db_path=db)
    assert predicted is not None and predicted < 0.5
    assert predicted == pytest.approx(round(action_outcomes.reliability("OPEN_APP", db_path=db)["p"], 4))
    assert [r["action"] for r in led.unreliable_actions(db_path=db)] == ["OPEN_APP"]


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
    assert rep["n"] == 12 and rep["ece_fitted"] < rep["ece_score"] and rep["brier_fitted"] < rep["brier_score"]
    # the calibration error a reply carries is that of the chances as they were given, each before its
    # own outcome was known (prequential), not of the fit on the outcomes it was fitted to
    assert rep["n_given"] == 12 and rep["ece"] < rep["ece_score"]
    assert p.ece == pytest.approx(rep["ece"]) and rep["bands"] != [{"range": "0.90-1.00", "n": 12, "predicted": 0.9,
                                                                    "observed": 0.5}]


def test_the_calibration_error_is_of_the_chances_as_given_not_the_fit_on_its_own_rows(db):
    """The figure shipped with each reply (meta.calibration.ece) was measured on the rows the fit was
    trained on, while the chance actually given, stored with every figure, was never read."""
    for i in range(10):
        ep._record_figure("answer", ep.Probability(p=0.95, kind="answer", score=0.6), f"given{i}", "", None, "", "",
                          db)
        ep.settle(f"given{i}", right=i % 2 == 0, db_path=db)
    rep = ep.report("answer", db_path=db)
    assert rep["bands"] == [{"range": "0.90-1.00", "n": 10, "predicted": 0.95, "observed": 0.5}]
    assert rep["ece"] == pytest.approx(0.45) and rep["ece_score"] == pytest.approx(0.1)
    assert ep.calibrate("answer", 0.6, db_path=db).ece == pytest.approx(0.45)
    line = [x for x in ep.measured_lines(db_path=db) if x.startswith("- replies to you")][0]
    assert "off by 0.45" in line and "10 scored as given" in line


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


def test_a_plain_no_denies_what_it_speaks_of_and_agreement_is_no_denial():
    """'no, I live in Lisbon now' is no verdict on a whole reply, but it denies what it goes on to say
    (a claim about the user); 'No I think you're right' denies nothing."""
    from eli.cognition.correction_patterns import denies
    for said in ("no, I live in Lisbon now", "Nope. Tuesdays, not Mondays", "ok. no, that's not correct"):
        assert denies(said), said
    for said in ("No I think you're right", "no, that's right", "I know nothing about it", "nobody asked",
                 "no-one told me", "thanks"):
        assert not denies(said), said


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



def test_one_reader_says_what_a_message_says_of_the_last_reply():
    """correction_patterns is the one reader: the complaint (self_claims), redo (action_commitment)
    and challenge (diagnostic_patterns) words live there, and those modules hand on to it."""
    from eli.cognition import correction_patterns as cp
    cases = {"no, that's not true": cp.CORRECTION, "you're not qwen": cp.CORRECTION,
             "you misunderstood me": cp.CORRECTION, "you did not open the player": cp.REDO,
             "try that again": cp.REDO, "you keep forgetting things": cp.COMPLAINT,
             "what the hell": cp.COMPLAINT, "you lost the thread": cp.COMPLAINT,
             "thanks, spot on": cp.ENDORSEMENT, "what is the weather like": cp.NONE, "": cp.NONE,
             "thanks, but you did not open it": cp.REDO}
    assert {said: cp.reply_verdict(said) for said in cases} == cases
    import eli.cognition.self_claims as sc
    import eli.runtime.action_commitment as ac
    import eli.runtime.diagnostic_patterns as dp
    assert sc.complains_about_eli is cp.complains_about_eli
    assert ac.is_redo_directive is cp.is_redo_directive and ac._REDO_RE is cp.REDO_RE
    assert dp.is_user_challenge is cp.is_user_challenge
    for mod in (sc, ac, dp):
        assert not hasattr(mod, "_COMPLAINT_RE") and not hasattr(mod, "_USER_CHALLENGE_RE")


def test_the_tone_profile_counts_corrections_with_the_same_reader():
    """Its own list read 'actually' or 'i said' anywhere as a correction."""
    from eli.cognition.tone_analyzer import analyze_turns
    sig = analyze_turns(["actually I think tea is nicer", "i said that to my friend", "you misunderstood me",
                         "try that again"])
    assert sig["correction_rate"] == 0.5


def test_a_figure_is_settled_once_and_a_later_verdict_takes_the_place_of_the_earlier(db):
    """settle() wrote a row every time: a figure settled twice counted twice (once right, once wrong),
    and a reversal inside the ledger's dedup window was dropped as a repeat."""
    ep.assess("act", 0.5, request_id="act:7", db_path=db)
    assert ep.settle("act:7", True, how="stood", kind="act", db_path=db)
    assert ep.settle("act:7", True, how="stood", kind="act", db_path=db)
    assert [y for _s, y, _f in ep.outcomes("act", db_path=db)] == [1.0]
    assert ep.settle("act:7", False, how="undone", kind="act", db_path=db)     # within seconds: not a repeat
    assert [y for _s, y, _f in ep.outcomes("act", db_path=db)] == [0.0], "the later verdict stands, once"
    assert ep.base_rate("act", db_path=db) == (pytest.approx(1 / 3), 1)


def test_settling_one_kind_keeps_the_other_kinds_fits(db, monkeypatch):
    """Every settle cleared every kind's fit, and routes are settled each turn, so no fit outlived a
    turn and each reply re-read the whole ledger."""
    ep.assess("answer", 0.8, request_id="a1", db_path=db)
    ep.settle("a1", True, db_path=db)
    ep.calibrate("answer", 0.8, db_path=db)
    reads = []
    real = led.assessment_pairs
    monkeypatch.setattr(led, "assessment_pairs", lambda *a, **k: reads.append(a[0]) or real(*a, **k))
    ep.assess("route", 0.8, request_id="route:r1", db_path=db)
    ep.settle("route:r1", True, kind="route", db_path=db)
    ep.calibrate("answer", 0.8, db_path=db)
    assert "answer" not in reads, "the answer fit was kept"
    names = {r[1] for r in led._connect(db).execute("PRAGMA index_list(runtime_events)").fetchall()}
    assert "idx_runtime_events_request" in names, "figures and outcomes are joined by request id on an index"


def test_a_reply_with_no_token_figures_is_not_read_as_the_surest_reply(db):
    """A missing token figure was imputed as log-probability 0 and entropy 0 (certainty), so replies
    with no figures borrowed the confidence of the surest ones."""
    for i in range(12):       # replies with token figures: sure ones right, unsure ones wrong
        sure = i % 2 == 0
        ep.assess("answer", 0.8, request_id=f"t{i}", features={"mean_logprob": -0.1 if sure else -2.0}, db_path=db)
        ep.settle(f"t{i}", sure, db_path=db)
    for i in range(12):       # replies without any (the fast path): mostly wrong
        ep.assess("answer", 0.8, request_id=f"n{i}", features={}, db_path=db)
        ep.settle(f"n{i}", i % 4 == 0, db_path=db)
    fit = ep.weights_for("answer", db_path=db)
    assert "mean_logprob" in fit["scale"]["missing"] and f"mean_logprob{ep.MISSING}" in ep.weight_names(fit)
    sure = ep.calibrate("answer", 0.8, features={"mean_logprob": -0.1}, db_path=db).p
    none = ep.calibrate("answer", 0.8, features={}, db_path=db).p
    assert none < sure and none < 0.5, "missing is its own sign, not the surest value"
    assert fit["scale"]["sd"]["mean_logprob"] == pytest.approx(0.95), "each feature is standardised"


def test_a_reply_is_no_surer_than_the_claims_it_rested_on_once_the_outcomes_say_so(db):
    """The claims a reply rested on, how far its samples agreed and the path it took were recorded
    with every reply and read by nothing."""
    assert {"claims_min_p", "agreement", "entropy", "grounded", "memory_ctx", "bus_fast"} <= set(ep.features_of("answer"))
    for i in range(16):
        weak = i % 2 == 0
        ep.assess("answer", 0.8, request_id=f"c{i}", features={"claims_min_p": 0.3 if weak else 0.9}, db_path=db)
        ep.settle(f"c{i}", not weak, db_path=db)
    weak = ep.calibrate("answer", 0.8, features={"claims_min_p": 0.3}, db_path=db)
    strong = ep.calibrate("answer", 0.8, features={"claims_min_p": 0.9}, db_path=db)
    assert weak.p < 0.5 < strong.p and weak.weights["claims_min_p"] > 0.5


def test_a_class_with_few_outcomes_is_drawn_toward_its_kind(db):
    """One outcome of a class threw away the hundreds of its kind: the class was fitted on its own row."""
    _settled(db, score=0.6, right=lambda i: i % 10 != 0, n=60, prefix="k", class_="chat")
    _settled(db, score=0.6, right=lambda i: False, n=1, prefix="g", class_="grounded")
    alone = ep.fit_model([(0.6, 0.0, {})], ep.features_of("answer"))
    lone = ep.apply_fit(alone, 0.6, {})
    pooled = ep.calibrate("answer", 0.6, class_="grounded", db_path=db)
    assert pooled.class_ == "grounded" and pooled.n == 1
    assert pooled.p > lone + 0.2 and pooled.p > 0.6, "one wrong reply does not outweigh its kind's sixty"
