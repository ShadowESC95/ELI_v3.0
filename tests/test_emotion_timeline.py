"""Emotion timeline — persistence, baseline, and the proactive check-in gates.

Isolation: conftest points ELI_ARTIFACTS_DIR at the in-project .pytest_artifacts
tree, so these write to a throwaway user.sqlite3 and never touch the live store.
Each test clears the table first — run-length and baseline maths are counts, so
leftover rows from a sibling test would silently change the answer.
"""
import time

import pytest

from eli.cognition import emotion_timeline as et


def _clear():
    conn = et._connect()
    try:
        et._ensure(conn)
        conn.execute("DELETE FROM emotion_events")
        conn.commit()
    finally:
        conn.close()
    # The module dedupes identical consecutive reads; reset so tests are independent.
    et._last_key = ""
    et._last_key_ts = 0.0
    p = et._cooldown_path()
    if p.exists():
        p.unlink()


# Reads come turns apart, not milliseconds: the state behind them (eli/cognition/affect_state) has
# inertia, and reads a few milliseconds apart are one moment. The clock below stamps each new read
# five minutes after the last (a repeated text, the same turn read twice, keeps its moment), starting
# far enough back that a long baseline still lies inside the assessment's window.
_CLOCK = {"t": 0.0, "last_text": None}
_READ_GAP_S = 300.0


def _clock_now() -> float:
    return _CLOCK["t"]


@pytest.fixture(autouse=True)
def clean_slate(monkeypatch):
    _clear()
    _CLOCK["t"] = time.time() - _READ_GAP_S * 64
    _CLOCK["last_text"] = None
    real_record = et.record

    def spaced(detected, *args, **kwargs):
        text = kwargs.get("user_text", "")
        if text != _CLOCK["last_text"]:
            _CLOCK["t"] += _READ_GAP_S
            _CLOCK["last_text"] = text
        return real_record(detected, *args, **kwargs)

    # the timeline's clock is this one for the whole test: reads are stamped by it, and the
    # assessment, the window and the cooldown read "now" from it, as a turn does right after a read
    monkeypatch.setattr(et.time, "time", _clock_now)
    monkeypatch.setattr(et, "record", spaced)
    yield
    _clear()


def _seed(emotion, n, *, user_id="u1", confidence=0.8, action="", prefix="turn"):
    """Write n reads, each with distinct text so the dedupe guard lets them through."""
    for i in range(n):
        assert et.record(emotion, expressed="calm", confidence=confidence,
                         source="fused", user_text=f"{prefix}-{emotion}-{i}",
                         user_id=user_id, eli_prior_action=action)


# ── Storage ───────────────────────────────────────────────────────────────────
def test_record_and_read_back():
    assert et.record("irritated", expressed="calm", confidence=0.7, source="text",
                     user_text="ugh this again", user_id="u1")
    rows = et.recent(limit=5, user_id="u1")
    assert len(rows) == 1
    assert rows[0]["detected"] == "irritated"
    assert rows[0]["valence"] == "negative"
    assert rows[0]["user_text"] == "ugh this again"


def test_dedupe_blocks_double_write_for_one_turn():
    """_build_enhanced_system can run twice per turn; the read must count once."""
    assert et.record("sad", user_text="same utterance", user_id="u1", confidence=0.8)
    assert not et.record("sad", user_text="same utterance", user_id="u1", confidence=0.8)
    assert len(et.recent(user_id="u1")) == 1


def test_empty_emotion_is_not_recorded():
    assert not et.record("", user_text="x", user_id="u1")
    assert et.recent(user_id="u1") == []


def test_reads_come_back_newest_first_within_one_second():
    for i, emo in enumerate(["calm", "happy", "sad"]):
        et.record(emo, user_text=f"t{i}", user_id="u1", confidence=0.8)
    rows = et.recent(user_id="u1")
    assert [r["detected"] for r in rows] == ["sad", "happy", "calm"]


def test_user_scoping():
    _seed("happy", 2, user_id="alice")
    _seed("sad", 3, user_id="bob")
    assert len(et.recent(user_id="alice")) == 2
    assert len(et.recent(user_id="bob")) == 3


# ── Valence ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("emotion,expected", [
    ("sad", "negative"), ("angry", "negative"), ("irritated", "negative"),
    ("happy", "positive"), ("joyful", "positive"), ("ecstatic", "positive"),
    ("neutral", "neutral"), ("calm", "neutral"), ("professional", "neutral"),
    ("something_unknown", "neutral"),
])
def test_valence_classification(emotion, expected):
    assert et.valence_of(emotion) == expected


# ── The check-in gates ────────────────────────────────────────────────────────
def test_single_spike_does_not_trigger_checkin():
    """One bad sentence is not a mood — this is the guard against nagging. The state behind the
    reads stays neutral: one read moves it a little, not clear of neutral."""
    _seed("calm", 4)
    _seed("irritated", 1)
    a = et.assess(user_id="u1")
    assert a["state"] == "neutral"
    assert a["affect"]["valence"] < 0 and a["run_length"] == 0
    assert not a["should_checkin"]
    assert "spike" in a["reason"]


def test_sustained_negative_run_triggers_checkin():
    _seed("irritated", et.SUSTAINED_MIN_READS)
    a = et.assess(user_id="u1")
    assert a["state"] == "negative"
    assert a["run_length"] >= et.SUSTAINED_MIN_READS
    assert a["dominant"] == "irritated"
    assert a["should_checkin"]


def test_sustained_positive_run_triggers_checkin():
    _seed("joyful", et.SUSTAINED_MIN_READS)
    a = et.assess(user_id="u1")
    assert a["state"] == "positive"
    assert a["should_checkin"]


def test_low_confidence_run_does_not_trigger():
    _seed("sad", et.SUSTAINED_MIN_READS + 1, confidence=0.1)
    a = et.assess(user_id="u1")
    assert not a["should_checkin"]
    assert "confidence" in a["reason"]


def test_neutral_state_never_triggers():
    _seed("neutral", 6)
    a = et.assess(user_id="u1")
    assert a["state"] == "neutral"
    assert not a["should_checkin"]


def test_cooldown_blocks_a_second_checkin():
    _seed("angry", et.SUSTAINED_MIN_READS + 1)
    assert et.assess(user_id="u1")["should_checkin"]
    et.note_checkin("negative", "angry")
    a = et.assess(user_id="u1")
    assert not a["should_checkin"]
    assert "cooldown" in a["reason"]


def test_no_reads_is_reported_not_guessed():
    a = et.assess(user_id="nobody")
    assert not a["should_checkin"]
    assert a["state"] == ""
    assert "no reads" in a["reason"]


# ── Transition + antecedent ───────────────────────────────────────────────────
def test_transition_and_trigger_action_are_captured():
    """The whole point of 'was it something I did' — the action at the turn it turned."""
    _seed("calm", 3, action="CHAT")
    _seed("irritated", et.SUSTAINED_MIN_READS, action="OPEN_APP")
    a = et.assess(user_id="u1")
    assert a["transition"] == {"from": "neutral", "to": "negative"}
    assert a["trigger_action"] == "OPEN_APP"
    assert a["trigger_text"]


# ── Baseline ──────────────────────────────────────────────────────────────────
def test_baseline_is_not_credible_without_enough_history():
    _seed("happy", 3)
    b = et.baseline("u1")
    assert b["reads"] == 3
    assert not b["credible"]


def test_baseline_credible_and_dominant():
    _seed("calm", et.BASELINE_MIN_READS + 2)
    b = et.baseline("u1")
    assert b["credible"]
    assert b["dominant"] == "calm"
    assert b["negative_share"] == 0.0


def test_negative_run_is_unusual_for_a_normally_calm_user():
    _seed("calm", et.BASELINE_MIN_READS + 2, prefix="base")
    _seed("irritated", et.SUSTAINED_MIN_READS, prefix="now")
    a = et.assess(user_id="u1")
    assert a["baseline"]["credible"]
    assert a["unusual"]
    assert a["should_checkin"]


def test_negative_run_is_not_unusual_for_a_habitually_negative_user():
    """Someone whose baseline IS negative must not be flagged as a change."""
    _seed("irritated", et.BASELINE_MIN_READS + 2, prefix="base")
    a = et.assess(user_id="u1")
    assert a["baseline"]["credible"]
    assert not a["unusual"]


# ── Model-facing output ───────────────────────────────────────────────────────
def test_evidence_block_empty_when_no_checkin_warranted():
    _seed("calm", 2)
    assert et.evidence_block(user_id="u1") == ""


def test_evidence_block_states_measurement_and_delegates_wording():
    _seed("irritated", et.SUSTAINED_MIN_READS + 1, action="RUN_CMD")
    block = et.evidence_block(user_id="u1")
    assert block
    assert "irritated" in block
    # It must hand the decision to the model, not script a reply.
    assert "your own words" in block
    assert "Never quote these numbers" in block
    # And it must not contain a ready-made sentence for ELI to parrot.
    assert "You seem" not in block


def test_trend_line_reports_sequence_oldest_to_newest():
    for i, emo in enumerate(["calm", "irritated", "angry"]):
        et.record(emo, user_text=f"s{i}", user_id="u1", confidence=0.8)
        time.sleep(0.002)
    line = et.trend_line(user_id="u1")
    assert "calm → irritated → angry" in line


def test_trend_line_silent_on_all_neutral():
    _seed("neutral", 4)
    assert et.trend_line(user_id="u1") == ""


def test_trend_line_needs_more_than_one_read():
    _seed("sad", 1)
    assert et.trend_line(user_id="u1") == ""


# ── Kill switch ───────────────────────────────────────────────────────────────
def test_disabled_records_nothing_and_asserts_nothing(monkeypatch):
    monkeypatch.setenv("ELI_EMOTION_MEMORY", "0")
    assert not et.enabled()
    assert not et.record("angry", user_text="off", user_id="u1")
    assert et.recent(user_id="u1") == []
    a = et.assess(user_id="u1")
    assert not a["should_checkin"]
    assert a["reason"] == "disabled"


# ── the state behind the reads (eli/cognition/affect_state) ──────────────────────────────────
# The reads are observations of a valence that moves with inertia and fades when nothing is heard;
# a Kalman filter keeps it with its uncertainty, and the time constant is this user's own.

def _reads(labels, *, confidence=0.8, gap_s=60.0, now=None, arousal=None):
    """Reads `gap_s` apart, the last one at `now`, as a turn assesses right after its read."""
    import time as _t
    now = now if now is not None else _t.time()
    return [{"ts": now - gap_s * (len(labels) - 1 - i), "valence": v, "detected": v, "confidence": confidence,
             "arousal": arousal} for i, v in enumerate(labels)]


def test_one_read_is_a_spike_three_are_a_state_and_the_filter_says_how_sure():
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    one = af.estimate(rows=_reads(["negative"], now=now), now=now)
    three = af.estimate(rows=_reads(["negative"] * 3, now=now), now=now)
    assert one.label == "neutral" and abs(one.valence) < af.STATE_MIN
    assert three.label == "negative" and three.valence < -af.STATE_MIN and three.z_valence <= -af.Z_SIG
    assert three.var_valence < one.var_valence, "more reads, less doubt"
    assert 0.9 < three.confidence <= 1.0 and three.run_length == 3


def test_reads_the_reader_was_unsure_of_move_the_state_less():
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    sure = af.estimate(rows=_reads(["negative"] * 4, confidence=0.9, now=now), now=now)
    unsure = af.estimate(rows=_reads(["negative"] * 4, confidence=0.3, now=now), now=now)
    assert sure.valence < unsure.valence < 0 and unsure.label == "neutral"


def test_the_state_fades_toward_neutral_when_nothing_is_heard():
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    rows = _reads(["negative"] * 3, gap_s=600.0, now=now)
    then = af.estimate(rows=rows, now=now)
    later = af.estimate(rows=rows, now=now + 6 * 3600.0)
    assert then.label == "negative" and later.label == "neutral" and abs(later.valence) < abs(then.valence)
    assert later.var_valence > then.var_valence, "doubt grows back while nothing is heard"


def test_arousal_is_read_from_the_voice_only_and_missing_reads_are_simply_not_observed():
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    rows = _reads(["positive"] * 3, now=now, arousal=0.8)
    rows[1]["arousal"] = None                       # a typed turn: no voice, nothing of arousal
    state = af.estimate(rows=rows, now=now)
    assert state.arousal > 0.3 and state.var_arousal < af.FLOOR_PRIOR
    assert af.estimate(rows=_reads(["positive"] * 3, now=now), now=now).arousal == 0.0


def test_the_time_constant_and_the_wander_are_this_users_own():
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    turning = af.estimate(rows=_reads(["negative"] * 4 + ["positive"] * 4 + ["negative"] * 4, gap_s=120.0, now=now), now=now)
    steady = af.estimate(rows=_reads(["negative"] * 12, gap_s=1800.0, now=now), now=now)
    assert (turning.tau_minutes, turning.q_scale) != (steady.tau_minutes, steady.q_scale)
    assert turning.tau_minutes <= steady.tau_minutes and steady.q_scale <= turning.q_scale
    assert af.identify(af.observations(_reads(["negative"] * 2, now=now))) == \
        (af.TAU_GRID_MINUTES[len(af.TAU_GRID_MINUTES) // 2], af.Q_GRID[len(af.Q_GRID) // 2]), "too few reads to tell"


def test_a_mood_that_turns_after_a_long_calm_is_seen_within_a_few_reads():
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    state = af.estimate(rows=_reads(["neutral"] * 22 + ["negative"] * 3, gap_s=300.0, now=now), now=now)
    assert state.label == "negative" and state.run_length == 3


def test_the_assessment_carries_the_state_and_still_gates_a_checkin():
    _seed("irritated", 3)
    out = et.assess(user_id="u1")
    assert out["state"] == "negative" and out["should_checkin"] and out["run_length"] == 3
    assert out["affect"]["valence"] < 0 and out["affect"]["reads"] == 3 and 0 < out["confidence"] <= 1
    assert "±" in out["reason"]


def _mixed(spec, *, gap_s=120.0, now):
    """Reads from [(label, confidence), ...], `gap_s` apart, the last at `now`."""
    return [{"ts": now - gap_s * (len(spec) - 1 - i), "valence": v, "detected": v, "confidence": c,
             "arousal": None} for i, (v, c) in enumerate(spec)]


def test_turns_with_nothing_read_do_not_pin_the_state_to_neutral():
    """A turn with no sign is kept as neutral at sureness 0. Its noise was the smallest there is,
    so twenty such turns made eight angry reads in a row barely move the state."""
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    rows = _mixed([("neutral", 0.0)] * 20 + [("negative", 0.7)] * 8, now=now)
    state = af.estimate(rows=rows, now=now)
    assert state.label == "negative" and state.reads == 8
    alone = af.estimate(rows=rows[20:], now=now)
    assert abs(state.valence - alone.valence) < 1e-9, "a turn with nothing read is no observation"


def test_a_read_the_reader_was_barely_sure_of_says_little():
    """Read as seeing the state whole, a read at sureness 0.1 had the noise of one at 0.9 (c(1 − c)):
    twenty barely-sure neutral reads held off two sure angry ones that alone make a state."""
    from eli.cognition import affect_state as af
    now = 1_700_000_000.0
    two = [("negative", 0.8)] * 2
    assert af.estimate(rows=_mixed(two, now=now), now=now).label == "negative"
    after_weak = af.estimate(rows=_mixed([("neutral", 0.1)] * 20 + two, now=now), now=now)
    after_sure = af.estimate(rows=_mixed([("neutral", 0.9)] * 20 + two, now=now), now=now)
    assert after_weak.label == "negative"
    assert abs(after_sure.valence) < abs(after_weak.valence), "sure neutral reads say more than unsure ones"


def test_a_turn_with_no_sign_is_kept_as_nothing_read_not_as_the_standing_mood(monkeypatch):
    """With nothing read the tone falls back to the affect state; recorded as a read, the state was
    fed its own output and stayed negative for every calm turn after."""
    from eli.cognition import tone_adaptor as ta
    monkeypatch.setattr(ta, "detect_voice_emotion", lambda: (None, 0.0))
    _seed("angry", 3, confidence=0.7)
    assert et.assess(user_id="u1")["state"] == "negative"
    for i in range(10):
        ta.build_situation_brief(f"the bus timetable for stop {i}", user_id="u1", include_voice_cue=False)
    newest = et.recent(limit=10, user_id="u1")
    assert len(newest) == 10
    assert all(r["detected"] == "neutral" and float(r["confidence"]) == 0.0 for r in newest)
    from eli.cognition import affect_state as af
    assert af.estimate(user_id="u1").reads == 3, "the three reads are all the state rests on"
