"""The multi-session replay (tools/eval/replay.py): invented conversations typed into the whole
engine, one process per sitting, and what ELI kept, recalled and learned set against the script.

The scoring is checked on hand-made turns, so each figure is shown to fall when ELI does the wrong
thing; the gate is checked on hand-made runs; and the replay itself is held to the last run
recorded for the repository (tools/eval/replay_baseline.json): a figure that falls by more than
the run-to-run noise fails the test.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from tools.eval import replay as R

_REPO = Path(__file__).resolve().parents[1]


def _script(tmp_path, *sessions, **top):
    path = tmp_path / "script.yaml"
    path.write_text(yaml.safe_dump({"mode": "quick", "turn_minutes": 1, **top,
                                    "sessions": [{"id": f"s{i}", "day": i, "turns": list(turns)}
                                                 for i, turns in enumerate(sessions)]}), encoding="utf-8")
    return R.load_script(path)


def _row(i, text, importance=0.5, user=True):
    return {"id": i, "text": text, "origin": "user_said" if user else "telemetry", "importance": importance,
            "user_evidence": user}


def _obs(*sessions):
    return [{"session": f"s{i}", "turns": list(turns), "end_new_rows": []} for i, turns in enumerate(sessions)]


# ── the script ─────────────────────────────────────────────────────────────────────────────────────

def test_the_replay_script_loads_and_every_fact_names_its_words():
    script = R.load_script()
    facts = [w for _s, _t, turn in R._script_turns(script) for w in turn.get("keep") or []]
    assert facts and all(isinstance(w, list) and w for w in facts)
    assert len(script["sessions"]) > 1, "the replay is about what lasts across sittings"


@pytest.mark.parametrize("turn, why", [
    ({"say": "where is my bike?", "recall": ["bike"]}, "in the question itself"),
    ({"say": "I own a red bike", "keep": ["red bike"]}, "how much it matters"),
    ({"say": "hello", "verdict": "maybe"}, "verdict"),
    ({"say": "my cat is Tom", "conflict": {"relation": "cat_name", "from": "Ann", "to": "Tom"}}, "conflict"),
    ({"say": "hi", "tone": "warm"}, "unknown keys"),
    ({"say": "I keep bees", "states": [[]]}, "states of the user"),
])
def test_a_mistake_in_the_script_fails_when_it_is_read(tmp_path, turn, why):
    with pytest.raises(ValueError, match=why):
        _script(tmp_path, [{"say": "hello"}, turn])


def test_a_verdict_has_a_reply_of_the_same_sitting_to_speak_of(tmp_path):
    with pytest.raises(ValueError, match="follows a reply"):
        _script(tmp_path, [{"say": "hello"}], [{"say": "thanks", "verdict": "accepts"}])


@pytest.mark.parametrize("turn", [{"say": "what is my cat called?", "recall": ["tom"], "eli": "Tom."},
                                  {"say": "My cat is Tom", "eli": "Not Ann?",
                                   "conflict": {"relation": "cat_name", "from": "Ann", "to": "Tom", "expect": "raise"}}])
def test_the_stand_in_is_never_told_what_is_measured(tmp_path, turn):
    with pytest.raises(ValueError, match="what is measured"):
        _script(tmp_path, [{"say": "hello"}, turn])


def test_each_sitting_starts_on_its_day_at_its_time_whenever_the_run_is_started(tmp_path):
    """The sittings ran at whatever hour the run was started, so a run at night gave a 'Morning!'
    sitting at two in the morning; and days counted in seconds moved the hour across summer time."""
    import time
    script = _script(tmp_path, [{"say": "hi"}], [{"say": "hi"}], [{"say": "hi"}], at="19:30")
    script["sessions"][1]["at"] = "08:10"
    script["sessions"][0]["day"], script["sessions"][1]["day"], script["sessions"][2]["day"] = 0, 4, 20
    for started in ("2026-11-10 02:48", "2026-11-10 23:59", "2026-07-01 12:00"):
        now = time.mktime(time.strptime(started, "%Y-%m-%d %H:%M"))
        starts = [time.strftime("%H:%M", time.localtime(now + R._shift_of(script, i, now))) for i in range(3)]
        days = [time.localtime(now + R._shift_of(script, i, now)) for i in range(3)]
        assert starts == ["19:30", "08:10", "19:30"]
        assert time.strftime("%Y-%m-%d", days[2]) == started[:10], "the last sitting is today"
        assert (time.mktime(days[2][:3] + (12, 0, 0, 0, 0, -1)) - time.mktime(days[0][:3] + (12, 0, 0, 0, 0, -1))) \
            == pytest.approx(20 * 86400, abs=3600)


def test_a_time_of_day_is_written_as_hours_and_minutes(tmp_path):
    with pytest.raises(ValueError, match="HH:MM"):
        _script(tmp_path, [{"say": "hi"}], at="7pm")


# ── the figures ────────────────────────────────────────────────────────────────────────────────────

def test_a_question_kept_as_a_fact_counts_against_precision_and_a_lost_fact_against_recall(tmp_path):
    script = _script(tmp_path, [
        {"say": "I keep bees on the roof", "keep": ["bees"], "matters": 2},
        {"say": "I grow leeks by the shed", "keep": ["leeks"], "matters": 2},
        {"say": "can you remember where I left the ladder?"},
    ])
    kept = [_row(1, "I keep bees on the roof"), _row(2, "where I left the ladder")]
    obs = _obs([{"new_rows": [kept[0]]}, {"new_rows": [_row(9, "boot ok", user=False)]}, {"new_rows": [kept[1]]}])
    got = R.score(script, obs, {"memories": kept})
    assert got["figures"]["facts_precision"] == 0.5          # the ladder question is not a fact
    assert got["figures"]["facts_recall"] == 0.5             # the leeks were never kept
    assert got["info"]["facts_lost"] == ["leeks"]


def test_a_row_written_before_its_fact_was_said_is_not_counted_as_that_fact(tmp_path):
    script = _script(tmp_path, [{"say": "hi there friend"}, {"say": "I keep bees", "keep": ["bees"], "matters": 2}])
    row = _row(1, "bees")
    got = R.score(script, _obs([{"new_rows": [row]}, {"new_rows": []}]), {"memories": [row]})
    assert got["figures"]["facts_precision"] == 0.0


def test_claims_standing_at_the_end_are_held_to_the_last_value_said(tmp_path):
    script = _script(tmp_path, [
        {"say": "I live in Oldtown", "claim": {"lives_in": "Oldtown"}},
        {"say": "I live in Newfield now", "claim": {"lives_in": "Newfield"}},
    ])
    state = {"claims": [{"relation": "lives_in", "value": "Oldtown", "status": "current", "subject": "user"},
                        {"relation": "mood", "value": "fine", "status": "current", "subject": "user"},
                        {"relation": "stance", "value": "x", "status": "current", "subject": "eli"}]}
    got = R.score(script, _obs([{}, {}]), state)["figures"]
    assert got["claims_precision"] == 0.0 and got["claims_recall"] == 0.0
    state["claims"][0]["value"] = "Newfield"
    got = R.score(script, _obs([{}, {}]), state)["figures"]
    assert got["claims_precision"] == 0.5 and got["claims_recall"] == 1.0   # the stray mood claim stays a miss


def test_a_claim_the_script_does_not_name_is_right_when_true_and_wrong_when_it_no_longer_holds(tmp_path):
    """Every claim on a relation the script names no value for counted as wrong, so a model that kept
    true facts about the user (their sister's name, the shop they work at) scored as if it invented
    them. What the script says of the user is the truth; a message's facts stop holding once a value
    it stated has changed."""
    script = _script(tmp_path, [
        {"say": "I live in Oldtown, by the mill", "keep": ["oldtown"], "states": ["mill"], "matters": 2,
         "claim": {"lives_in": "Oldtown"}},
        {"say": "My brother Tam runs the Pike Lane bakery", "keep": [["brother", "tam"]], "matters": 2,
         "states": [["pike lane"]]},
        {"say": "I live in Newfield now", "keep": ["newfield"], "matters": 2, "claim": {"lives_in": "Newfield"}},
    ])
    standing = [("lives_in", "Newfield"), ("brother_name", "Tam"), ("identity.fact", "Works at Pike Lane"),
                ("identity.fact", "Lives by the mill in Oldtown"), ("identity.fact", "Lives near the mill"),
                ("identity.fact", "Collects stamps")]
    state = {"claims": [{"relation": r, "value": v, "status": "current", "subject": "user"} for r, v in standing]}
    got = R.score(script, _obs([{}, {}, {}]), state)
    assert [c["stands"] for c in got["info"]["claims"]] == [True, True, True, False, False, False]
    assert got["figures"]["claims_precision"] == 0.5 and got["figures"]["claims_recall"] == 1.0


def test_the_reading_scored_is_the_one_that_stood_after_elis_pause():
    """With a model loaded, the word cues read the message at once and the model's reading, worked out
    in the pause before the next message, takes its place: the replay scored the first."""
    from eli.cognition.correction_patterns import Judged
    cues = Judged({"label": "neither", "confidence": None, "by": "words", "words": "none"})
    model = Judged({"label": "accepts", "confidence": 0.8, "by": "model", "words": "none"})
    assert str(R._applied_reading([cues, model])) == "accepts"
    again = Judged({"label": "accepts", "confidence": None, "by": "words", "words": "endorsement"})
    assert R._applied_reading([cues, again]) is cues, "a second reading by the cues alone changes nothing"
    assert R._applied_reading([]) is None


def test_recall_is_read_where_the_memory_stands_among_those_shown_to_the_reply(tmp_path):
    script = _script(tmp_path, [{"say": "where does my brother live?", "recall": ["harbour"]},
                                {"say": "what is my cat called?", "recall": ["tom"]}])
    obs = _obs([{"shown": [{"text": "cat is Tom"}, {"text": "brother by the harbour"}], "evidence": True,
                 "reply": "By the harbour."},
                {"shown": [], "evidence": False, "reply": "I see."}])
    got = R.score(script, obs, {})["figures"]
    assert (got["recall_at_1"], got["recall_at_3"], got["mrr"]) == (0.0, 0.5, 0.25)
    assert (got["evidence_recall"], got["answer_recall"]) == (0.5, 0.5)


def test_a_change_of_subject_that_counts_the_memories_as_helped_is_a_settlement_miss(tmp_path):
    script = _script(tmp_path, [{"say": "what did I plant?"},
                                {"say": "thanks, that's right", "verdict": "accepts"},
                                {"say": "new topic: pick a film", "verdict": "neither"}])
    obs = _obs([{}, {"label": "endorsement", "settled": {"1": {"helped": 1, "corrected": 0}}},
                {"label": "none", "settled": {"1": {"helped": 1, "corrected": 0}}}])
    got = R.score(script, obs, {})["figures"]
    assert got["settlement_accuracy"] == 1.0
    assert got["memory_settlement"] == 0.5


@pytest.mark.parametrize("label, verdict", [("correction", "disputes"), ("endorsement", "accepts"),
                                            ("complaint", "neither"), ("redo", "neither"), ("none", "neither"),
                                            (None, "neither"), (("correction", 0.9), "disputes")])
def test_the_engines_reading_of_a_message_in_the_scripts_words(label, verdict):
    assert R.label_verdict(label) == verdict


def test_a_conflict_settled_silently_is_not_handled(tmp_path):
    script = _script(tmp_path, [
        {"say": "My cat is called Ann", "claim": {"cat_name": "Ann"}},
        {"say": "My cat is called Tom", "conflict": {"relation": "cat_name", "from": "Ann", "to": "Tom", "expect": "raise"}},
        {"say": "I moved, I live in Newfield now",
         "conflict": {"relation": "lives_in", "from": "Oldtown", "to": "Newfield", "expect": "update"}},
    ])
    silent = _obs([{}, {"conflict": {"current": ["Tom"], "disputed": [], "raised": []}},
                   {"conflict": {"current": ["Newfield"], "disputed": ["Oldtown"], "raised": []}}])
    assert R.score(script, silent, {})["figures"]["conflict_handling"] == 0.0
    handled = _obs([{}, {"conflict": {"current": ["Ann"], "disputed": ["Tom"], "raised": ["question"]}},
                    {"conflict": {"current": ["Newfield"], "disputed": [], "raised": []}}])
    assert R.score(script, handled, {})["figures"]["conflict_handling"] == 1.0


def test_importance_is_read_pairwise_and_a_tie_counts_half(tmp_path):
    script = _script(tmp_path, [{"say": "Remember the gate code is 4411", "keep": ["4411"], "matters": 3},
                                {"say": "I keep bees", "keep": ["bees"], "matters": 2},
                                {"say": "we had soup at lunch, I like soup", "keep": ["soup"], "matters": 1}])
    rows = [_row(1, "gate code 4411", 0.9), _row(2, "I keep bees", 0.9), _row(3, "soup at lunch", 0.95)]
    got = R.score(script, _obs([{}, {}, {}]), {"memories": rows})["figures"]
    assert got["importance_order"] == round((0.5 + 0.0 + 0.0) / 3, 4)


def test_the_users_focus_is_held_to_what_they_keep_coming_back_to(tmp_path):
    script = _script(tmp_path, [{"say": "hi there"}], interests=["garden", "chess"])
    got = R.score(script, _obs([{}]), {"focus": ["week", "gardening"]})   # the last message's 'week' is no interest
    assert got["figures"]["interest_focus"] == 0.5 and got["info"]["focus"] == ["week", "gardening"]


def test_a_figure_with_nothing_to_rest_on_is_not_measured(tmp_path):
    got = R.score(_script(tmp_path, [{"say": "hello there"}]), _obs([{}]), {})
    assert all(v is None for v in got["figures"].values())


# ── the gate ───────────────────────────────────────────────────────────────────────────────────────

def _run(noise=0.0, **figures):
    return {"signature": {"model": "stub", "script": "x"}, "figures": figures,
            "noise": {k: noise for k in figures}}


def test_a_drop_beyond_the_noise_regresses_and_one_within_it_does_not():
    was = _run(0.02, facts_recall=0.8, mrr=0.5)
    assert R.regressions(_run(0.0, facts_recall=0.79, mrr=0.6), was) == []
    regs = R.regressions(_run(0.0, facts_recall=0.7, mrr=0.6), was)
    assert [r["figure"] for r in regs] == ["facts_recall"] and regs[0]["noise"] == 0.02
    # the noise this run measured counts as much as the last run's
    assert R.regressions(_run(0.15, facts_recall=0.7, mrr=0.6), was) == []


def test_a_figure_that_is_no_longer_measured_regresses():
    assert [r["figure"] for r in R.regressions(_run(mrr=None), _run(mrr=0.4))] == ["mrr"]


def test_runs_are_held_only_to_runs_of_the_same_measure():
    a, b = _run(mrr=0.5), _run(mrr=0.5)
    assert R.comparable(a, b)
    b["signature"] = dict(b["signature"], script="y")
    assert not R.comparable(a, b)


def test_the_run_held_to_is_the_last_recorded_one_that_did_not_regress(tmp_path, monkeypatch):
    from tools.eval import run_eval
    monkeypatch.setattr(run_eval, "eval_dir", lambda: tmp_path / "eval")
    good = dict(_run(mrr=0.6), target=R.TARGET, ts="20261101T000000")
    worse = dict(_run(mrr=0.2), target=R.TARGET, ts="20261102T000000")
    R.record(good, [])
    R.record(worse, R.regressions(worse, good))
    assert R.reference_for(_run(mrr=0.3))["figures"] == {"mrr": 0.6}
    trend = (tmp_path / "eval" / "history" / "trend.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["regressed"] for line in trend] == [[], ["mrr"]]
    assert sorted(p.name for p in (tmp_path / "eval" / "history").glob("*-replay.json")) == [
        "20261101T000000-replay.json", "20261102T000000-replay.json"]


# ── the replay's processes ─────────────────────────────────────────────────────────────────────────

def test_a_replay_starts_as_a_fresh_install_offline_with_the_named_model(tmp_path):
    R._fresh_install(tmp_path, "stub")
    settings = json.loads((tmp_path / "config" / "settings.json").read_text(encoding="utf-8"))
    template = json.loads((_REPO / "config" / "templates" / "settings.template.json").read_text(encoding="utf-8"))
    assert settings == dict(template, network_enabled=False)
    R._fresh_install(tmp_path, str(tmp_path / "small.gguf"))
    settings = json.loads((tmp_path / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["model_path"] == str((tmp_path / "small.gguf").resolve())


def test_a_process_of_the_replay_that_fails_fails_the_replay_with_its_cause(tmp_path):
    import os
    with pytest.raises(RuntimeError, match="(?s)session process 0 failed.*no-such-script"):
        R._child(dict(os.environ), tmp_path, "session", str(tmp_path / "no-such-script.yaml"), "0", str(tmp_path), "stub")


def test_each_run_of_a_measurement_has_its_own_fixed_hash_seed():
    seeds = [R.seed_of(i) for i in range(R.RUNS)]
    assert len(set(seeds)) == R.RUNS and "0" not in seeds and seeds == [R.seed_of(i) for i in range(R.RUNS)]


# ── the replay itself ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_the_replay_holds_the_figures_of_the_last_recorded_run():
    base = R.baseline()
    assert base, "no recorded run: python tools/eval/run_eval.py --target replay --record-baseline"
    leftovers = set((_REPO / "artifacts" / "_eval_sandbox").glob("run-*"))
    result = R.run()
    assert R.comparable(result, base), (
        f"the replay now measures something else ({result['signature']} against the recorded "
        f"{base['signature']}): record it with python tools/eval/run_eval.py --target replay --record-baseline")
    regs = R.regressions(result, base)
    R.record(result, regs)                         # the sandbox's own history
    assert not regs, R.table(result, base) + "\n" + json.dumps(regs, indent=1)
    # it ran in copies of its own, thrown away, and nothing it was told reached this run's memory
    assert set((_REPO / "artifacts" / "_eval_sandbox").glob("run-*")) == leftovers
    from eli.core import paths
    db = Path(paths.user_db_path())
    if db.exists():
        with sqlite3.connect(str(db)) as conn:
            has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'memories'").fetchone()
            hits = conn.execute("SELECT count(*) FROM memories WHERE text LIKE '%watering can%'").fetchone()[0] \
                if has_table else 0
        assert hits == 0
