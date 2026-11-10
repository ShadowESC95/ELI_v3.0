"""The write path weighs a replacement instead of letting the last writer win.

`_supersede_single_valued` deleted unconditionally: whoever wrote last won. That
is the yes-man mechanism sitting in the data layer — it makes ASSERTION equal to
EVIDENCE, so a passing mention could silently overwrite something the user had
stated outright and reaffirmed a dozen times, with no record that it ever
happened.

The opposite failure is just as bad and easier to ship by accident: weights so
conservative that a genuine correction can never land. Both directions are pinned
here.

Corroboration is what makes any of it possible. `user_patterns` had no such
column, so every row looked equally supported whether it had been said once or
twenty times, and there was nothing to weigh a claim WITH.
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from eli.cognition import stance_store as SS
from eli.runtime.profile_extractor import (
    _insert_user_pattern, ensure_profile_tables,
)


@pytest.fixture()
def cur(tmp_path):
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    con = sqlite3.connect(str(db))
    yield con.cursor()
    con.commit()
    con.close()


def _role(cur):
    return cur.execute(
        "SELECT pattern_data, corroboration FROM user_patterns "
        "WHERE pattern_type='identity.role'").fetchall()


def _establish(cur, times=5, value="User is a physicist"):
    """Reaffirm on `times` separate days: corroboration counts days of use, not extraction passes."""
    import time as _t
    start = _t.time() - times * 86400
    for i in range(times):
        _insert_user_pattern(cur, "identity.role", value, ts_value=start + i * 86400, provenance="user_explicit")


# ── the migration ────────────────────────────────────────────────────────────

def test_the_columns_weighing_needs_exist(cur):
    cols = {r[1] for r in cur.execute("PRAGMA table_info(user_patterns)")}
    assert {"corroboration", "provenance"} <= cols


def test_belief_tables_are_created(cur):
    names = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"eli_stances", "belief_revisions"} <= names


def test_reaffirmation_corroborates(cur):
    _establish(cur, times=5)
    assert _role(cur) == [("User is a physicist", 5)]


# ── standing its ground ──────────────────────────────────────────────────────

def test_a_passing_mention_cannot_overwrite_an_established_fact(cur):
    _establish(cur)
    accepted = _insert_user_pattern(cur, "identity.role", "User is a chef",
                                    provenance="user_passing")
    assert accepted is False
    assert _role(cur) == [("User is a physicist", 5)]


def test_elis_own_inference_cannot_overwrite_the_user(cur):
    _establish(cur)
    assert _insert_user_pattern(cur, "identity.role", "User is a student",
                                provenance="inferred") is False
    assert _role(cur) == [("User is a physicist", 5)]


def test_reaffirmation_upgrades_provenance_but_never_downgrades_it(cur):
    """The SQL-backed reaffirmation path (as opposed to belief.py's in-memory
    corroborate(), which was already correct and tested) compared the NEW
    provenance's weight against ITSELF via a `(SELECT ?)` bound to the same
    parameter -- always False, so provenance here was never actually upgraded
    through this path despite the surrounding comment's claim. Not a
    single-valued type: this exercises the exists=True reaffirmation branch,
    not _supersede_single_valued."""
    def _prov(cur):
        return cur.execute(
            "SELECT provenance FROM user_patterns WHERE pattern_type='interest.topic'"
        ).fetchone()[0]

    _insert_user_pattern(cur, "interest.topic", "quantum computing",
                         provenance="user_passing")
    assert _prov(cur) == "user_passing"

    # Higher-weight reaffirmation of the SAME fact must upgrade provenance.
    _insert_user_pattern(cur, "interest.topic", "quantum computing",
                         provenance="user_explicit")
    assert _prov(cur) == "user_explicit"

    # Lower-weight reaffirmation must NOT downgrade it back.
    _insert_user_pattern(cur, "interest.topic", "quantum computing",
                         provenance="inferred")
    assert _prov(cur) == "user_explicit", "a weaker reaffirmation downgraded provenance"


def test_a_refused_claim_does_not_leave_both_values(cur):
    """The caller must honour the refusal. Inserting anyway would leave two
    values on a key that is single-valued by definition — worse than either
    outcome on its own."""
    _establish(cur)
    _insert_user_pattern(cur, "identity.role", "User is a chef",
                         provenance="user_passing")
    assert len(_role(cur)) == 1


# ── being movable ────────────────────────────────────────────────────────────

def test_a_stale_belief_yields_to_a_fresh_correction(cur):
    old = time.time() - 400 * 86400
    _establish(cur, times=2)
    cur.execute("UPDATE user_patterns SET timestamp=?, ts=? "
                "WHERE pattern_type='identity.role'", (old, old))

    assert _insert_user_pattern(cur, "identity.role", "User is an engineer",
                                provenance="user_explicit") is True
    assert _role(cur) == [("User is an engineer", 1)]


def test_the_first_value_is_simply_adopted(cur):
    assert _insert_user_pattern(cur, "identity.role", "User is a physicist",
                                provenance="user_explicit") is True


# ── the record ───────────────────────────────────────────────────────────────

def test_a_supersession_is_recorded_with_its_reasoning(cur):
    """`_supersede_single_valued` DELETES the row it replaces, deliberately —
    several consumers read those tables. So the only way the old value survives
    at all is the revision record."""
    old = time.time() - 400 * 86400
    _establish(cur, times=2)
    cur.execute("UPDATE user_patterns SET timestamp=?, ts=? "
                "WHERE pattern_type='identity.role'", (old, old))
    _insert_user_pattern(cur, "identity.role", "User is an engineer",
                         provenance="user_explicit")

    revs = SS.revisions_for(cur, "identity.role")
    assert revs, "the superseded value vanished without trace"
    assert revs[0]["old"] == "User is a physicist"
    assert revs[0]["new"] == "User is an engineer"
    assert revs[0]["reason"]


def test_holding_records_nothing(cur):
    """Only a change is a revision. Logging refusals would drown the audit."""
    _establish(cur)
    _insert_user_pattern(cur, "identity.role", "User is a chef",
                         provenance="user_passing")
    assert SS.revisions_for(cur, "identity.role") == []


# ── ELI's own stances ────────────────────────────────────────────────────────

def test_a_stance_survives_the_session_that_formed_it(cur):
    """ELI argued one position for four hours in a live transcript, then had no
    record it ever held it. A stance that does not outlive its context window is
    a mood."""
    SS.record_stance(cur, "machine consciousness",
                     "I have no phenomenal experience", provenance="inferred")
    held = SS.get_stance(cur, "machine consciousness")
    assert held is not None
    assert "phenomenal" in held.statement


def test_arguing_the_same_line_again_strengthens_it(cur):
    for _ in range(4):
        SS.record_stance(cur, "machine consciousness", "I am not conscious")
    held = SS.get_stance(cur, "machine consciousness")
    assert held.corroboration == 4
    assert held.weight() > 0


def test_revising_a_stance_keeps_the_one_it_replaced(cur):
    SS.record_stance(cur, "machine consciousness", "I am not conscious")
    SS.revise_stance(cur, "machine consciousness",
                     "The question is open; I conflated functional and phenomenal",
                     reason="Cornered on the explanatory gap.")

    now_held = SS.get_stance(cur, "machine consciousness")
    assert "question is open" in now_held.statement

    history = SS.stance_history(cur, "machine consciousness")
    assert len(history) == 2
    old = [h for h in history if not h["current"]][0]
    assert old["position"] == "I am not conscious"
    assert "explanatory gap" in old["revision_reason"]


def test_topics_are_matched_case_and_space_insensitively(cur):
    SS.record_stance(cur, "Machine  Consciousness", "I am not conscious")
    assert SS.get_stance(cur, "machine consciousness") is not None


def test_an_unheld_topic_returns_nothing(cur):
    assert SS.get_stance(cur, "nothing was ever said about this") is None


# ── where a fact came from, and when it was said ─────────────────────────────

def _style(cur):
    return [r[0] for r in cur.execute(
        "SELECT pattern_data FROM user_patterns WHERE pattern_type='preference.style'").fetchall()]


def test_a_passing_word_does_not_replace_the_answer_style_the_user_chose(cur, tmp_path):
    """Four keyword rules all wrote the single-valued preference.style at the same provenance as the
    onboarding answer, so 'that answer was generic' replaced the style the user had picked."""
    from eli.runtime.profile_extractor import write_patterns_from_turn
    db = tmp_path / "user.sqlite3"
    _insert_user_pattern(cur, "preference.style", "User prefers short answers by default.")
    cur.connection.commit()
    write_patterns_from_turn("that answer was generic and you should challenge me more", db_path=db)
    assert _style(cur) == ["User prefers short answers by default."]
    facets = {r[0] for r in cur.execute("SELECT pattern_type FROM user_patterns").fetchall()}
    assert {"preference.originality", "preference.challenge"} <= facets


def test_the_extractors_facts_carry_how_they_were_known(cur, tmp_path):
    from eli.runtime.profile_extractor import write_patterns_from_turn
    db = tmp_path / "user.sqlite3"
    write_patterns_from_turn("my name is Robin and I'm an engineer, be thorough please", db_path=db)
    prov = dict(cur.execute("SELECT pattern_type, provenance FROM user_patterns").fetchall())
    assert prov["identity.name"] == "user_explicit" and prov["identity.role"] == "user_explicit"
    assert prov["preference.detail"] == "observed"


def test_the_same_label_in_new_words_corroborates_it(cur):
    """Each row carries the user's quote after its label; whole-string comparison made every
    rephrasing a rival, so corroboration never built and single-valued keys flipped back and forth."""
    day = 86400
    t0 = time.time() - 3 * day
    _insert_user_pattern(cur, "preference.challenge", 'User wants assumptions challenged. Said: "correct me"',
                         ts_value=t0)
    _insert_user_pattern(cur, "preference.challenge", 'User wants assumptions challenged. Said: "challenge me"',
                         ts_value=t0 + day)
    rows = cur.execute("SELECT corroboration FROM user_patterns WHERE pattern_type='preference.challenge'").fetchall()
    assert rows == [(2,)]


def test_an_older_statement_cannot_replace_a_newer_value(cur):
    """The boot backfill replays old turns; weighed as if said now, an old role replaced the current one."""
    now = time.time()
    _insert_user_pattern(cur, "identity.role", "User is an engineer", ts_value=now, provenance="user_explicit")
    assert _insert_user_pattern(cur, "identity.role", "User is a student", ts_value=now - 90 * 86400,
                                provenance="user_explicit") is False
    assert [r[0] for r in _role(cur)] == ["User is an engineer"]


def test_replaying_an_old_statement_never_moves_a_fact_back_in_time(cur):
    now = time.time()
    _insert_user_pattern(cur, "interest.explicit", "User is interested in tide tables.", ts_value=now)
    _insert_user_pattern(cur, "interest.explicit", "User is interested in tide tables.", ts_value=now - 30 * 86400)
    ts = cur.execute("SELECT ts FROM user_patterns WHERE pattern_type='interest.explicit'").fetchone()[0]
    assert abs(ts - now) < 1


def test_the_boot_backfill_reads_each_turn_once(tmp_path):
    from eli.runtime.profile_extractor import backfill_user_patterns
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, session_id TEXT, user_id TEXT, "
                "role TEXT, content TEXT, ts REAL, timestamp REAL)")
    old = time.time() - 200 * 86400
    con.execute("INSERT INTO conversation_turns (session_id, user_id, role, content, ts) VALUES "
                "('s', '', 'user', 'I am a student', ?)", (old,))
    con.commit()
    assert backfill_user_patterns(db_path=db)["scanned"] == 1
    # the user has since said otherwise
    cur = con.cursor()
    _insert_user_pattern(cur, "identity.role", "User is an engineer.", provenance="user_explicit")
    con.commit()
    assert backfill_user_patterns(db_path=db)["scanned"] == 0
    assert [r[0] for r in con.execute("SELECT pattern_data FROM user_patterns "
                                      "WHERE pattern_type='identity.role'")] == ["User is an engineer."]
    con.close()


# ── a verdict is voiced, never applied silently ──────────────────────────────

@pytest.fixture()
def turn(tmp_path, monkeypatch):
    """A turn of the owner's, with the thoughts store in the test's own folder."""
    from eli.cognition import thoughts as th
    from eli.kernel import request_context as rc
    monkeypatch.setattr(th, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    facts = {"request_id": "req-belief"}
    token = rc.turn_facts_var.set(facts)
    yield facts
    rc.turn_facts_var.reset(token)


def test_holding_a_position_is_said_in_the_reply_and_kept_as_a_thought(cur, turn):
    """On HOLD the user's statement was dropped with a debug line: ELI never said it was keeping its
    position, nor why."""
    from eli.cognition import thoughts as th
    from eli.kernel.stages.prompt_assembly import _belief_line
    _establish(cur)
    assert _insert_user_pattern(cur, "identity.role", "User is a chef", provenance="user_passing") is False
    [verdict] = turn["belief_verdicts"]
    assert verdict["action"] == "hold" and verdict["standing"] == "User is a physicist" and verdict["reasons"]
    line = _belief_line()
    assert "You kept what you hold" in line and "User is a chef" in line
    [thought] = th.recent(5, kinds=["conflict"])
    assert thought["decision"] == "held" and thought["status"] == "open"


def test_a_question_is_asked_and_the_users_answer_settles_both_stores(cur, turn, monkeypatch):
    """On QUESTION the value was replaced silently. Now the reply asks, the claims spine holds both as
    disputed, and the user's answer to that question settles it in the profile and the spine alike."""
    from eli.cognition import thoughts as th
    from eli.memory import claims
    from eli.runtime import profile_extractor as pe
    from eli.kernel.stages.prompt_assembly import _belief_line
    _insert_user_pattern(cur, "identity.role", "User is a physicist", provenance="user_explicit")
    _insert_user_pattern(cur, "identity.role", "User is an engineer", provenance="user_explicit")
    assert turn["belief_verdicts"][-1]["action"] == "question" and "Ask the user" in _belief_line()
    assert {r["value"]: r["status"] for r in claims.history(cur, "identity.role")} == {
        "User is a physicist": "current", "User is an engineer": "disputed"}
    assert th.recent(5, kinds=["conflict"])[0]["decision"] == "raised"
    asked = "You said physicist before and engineer just now - User is a physicist or User is an engineer?"
    assert pe._answer_open_conflicts(cur, "user is a physicist, still", asked, "", None) == ["identity.role"]
    assert [r["value"] for r in claims.current(cur)] == ["User is a physicist"]
    assert [r[0] for r in _role(cur)] == ["User is a physicist"], "the profile takes the value that stands"
    assert th.recent(5, kinds=["conflict"])[0]["outcome"] == "done"


def test_a_members_profile_facts_are_their_claims_not_the_owners(cur, monkeypatch):
    from eli.memory import claims, policy
    monkeypatch.setattr(policy, "rbac_members", lambda: frozenset({"member-b"}))
    monkeypatch.setattr(policy, "_active_user_id", lambda: "")
    _insert_user_pattern(cur, "identity.role", "User is a baker", provenance="user_explicit", user_id="member-b")
    assert claims.current(cur) == []
    assert [r["value"] for r in claims.current(cur, subject=claims.user_subject("member-b"))] == ["User is a baker"]


def test_a_fact_said_again_on_another_day_is_another_confirmation_of_its_claim(cur):
    from eli.memory import claims
    _establish(cur, times=3)
    [row] = claims.current(cur)
    assert row["confirmations"] == 3 and row["origin"] == "user_explicit"
    assert "you said so 3 times" in claims.basis(row)


def test_a_verdict_kept_in_the_same_database_never_waits_behind_the_profiles_own_write(tmp_path, monkeypatch):
    """The thoughts live in the same file as the profile: written on a second connection while the
    profile's write was open, the thought waited out the lock's timeout (seconds on the turn) and was
    lost. It is written beside the open write instead, as soon as that commits."""
    import threading
    from eli.cognition import thoughts as th
    from eli.kernel import request_context as rc
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    monkeypatch.setattr(th, "_default_db_path", lambda: db)
    con = sqlite3.connect(str(db), timeout=5.0)
    cur = con.cursor()
    _establish(cur)
    con.commit()
    token = rc.turn_facts_var.set({})
    try:
        # an earlier fact of the same message is written and not yet committed: the profile holds the lock
        _insert_user_pattern(cur, "interest.topic", "tide tables", provenance="user_passing")
        started = time.monotonic()
        assert _insert_user_pattern(cur, "identity.role", "User is a chef", provenance="user_passing") is False
        took = time.monotonic() - started
    finally:
        rc.turn_facts_var.reset(token)
    con.commit()
    for t in [t for t in threading.enumerate() if t.name == "eli-write-beside"]:
        t.join()
    con.close()
    assert took < 5.0, f"the profile write waited {took:.1f}s behind its own lock"
    assert [t["decision"] for t in th.recent(5, kinds=["conflict"], db_path=db)] == ["held"]


def test_the_backfill_reads_only_the_users_own_words_as_facts_about_them(tmp_path):
    """Memory rows whose tags merely mentioned 'user' were replayed as the user's words: ELI's own
    reflection telemetry became a claim about the user (preference.challenge)."""
    from eli.runtime.profile_extractor import backfill_user_patterns
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, value TEXT, content TEXT, source TEXT, "
                "tags TEXT, kind TEXT, user_id TEXT, ts REAL, timestamp REAL)")
    now = time.time()
    con.execute("INSERT INTO memories (text, source, tags, kind, ts) VALUES (?, 'eli_reflection', "
                "'eli_insight,auto,user_model', 'insight', ?)",
                ("User correction/challenge signals: 1 recent events; challenge me on my assumptions", now - 60))
    con.execute("INSERT INTO memories (text, source, tags, kind, ts) VALUES (?, 'user', 'user', 'memory', ?)",
                ("I'm an engineer, be thorough please", now - 30))
    con.commit()
    assert backfill_user_patterns(db_path=db)["scanned"] == 2
    kinds = {r[0] for r in con.execute("SELECT pattern_type FROM user_patterns")}
    assert "identity.role" in kinds and "preference.challenge" not in kinds
    con.close()
