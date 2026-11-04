"""A changed fact supersedes the old one and keeps it as dated history; both what held and what ELI knew are answerable."""
import sqlite3
import time

import pytest

from eli.memory import claims
from eli.memory.memory import Memory
from eli.memory.retrieval import _claim_hits


def _d(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d"))


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    claims.record(c, "work_schedule", "days", valid_from=_d("2026-01-01"), now=_d("2026-01-02"))
    claims.record(c, "work_schedule", "nights", valid_from=_d("2026-06-01"), now=_d("2026-06-03"))
    return c


def test_the_new_value_supersedes_and_the_old_is_history(conn):
    assert [c["value"] for c in claims.current(conn)] == ["nights"]
    assert [(c["value"], c["status"]) for c in claims.history(conn, "work_schedule")] == [("days", "superseded"), ("nights", "current")]


def test_what_held_in_a_period(conn):
    spring = claims.valid_during(conn, _d("2026-03-01"), _d("2026-04-01"))
    assert [c["value"] for c in spring] == ["days"]


def test_what_eli_knew_at_a_date(conn):
    assert [c["value"] for c in claims.known_at(conn, _d("2026-03-01"))] == ["days"]
    assert [c["value"] for c in claims.known_at(conn, _d("2026-07-01"))] == ["nights"]


def test_the_same_value_confirms_instead_of_duplicating(conn):
    claims.record(conn, "work_schedule", "Nights", now=_d("2026-07-01"), root_id=11)
    claims.record(conn, "work_schedule", "nights", now=_d("2026-07-02"), root_id=11)
    claims.record(conn, "work_schedule", "nights", now=_d("2026-07-03"), root_id=12)
    rows = claims.history(conn, "work_schedule")
    assert len(rows) == 2 and rows[-1]["confirmations"] == 2


def test_no_longer_retires_and_used_to_is_only_history(conn):
    claims.record_from_text(conn, "I used to work evenings", when=_d("2026-08-01"))
    assert [c["value"] for c in claims.current(conn)] == ["nights"]
    claims.record_from_text(conn, "I no longer work nights", when=_d("2026-08-15"))
    assert claims.current(conn) == []


def test_only_statements_are_read_not_questions(conn):
    assert claims.extract("Do I work nights?") == []
    assert claims.extract("I love pizza") == []
    assert {c["relation"] for c in claims.extract("I work at Acme Corp and my dog is called Max")} == {"employer", "dog_name"}


def test_deleting_the_source_withdraws_the_claim():
    c = sqlite3.connect(":memory:")
    claims.record(c, "lives_in", "Berlin", source_memory_id=7)
    assert claims.retract_from_memory(c, 7) == 1
    assert claims.current(c) == []


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    return Memory(db_path=tmp_path / "user.sqlite3")


def test_a_stored_statement_becomes_a_claim_and_a_change_supersedes(mem):
    mem.store_memory("I work days at the warehouse most weeks", source="user")
    mem.store_memory("I work nights now, the warehouse changed my shift", source="user")
    assert [c["value"] for c in mem.claims_current() if c["relation"] == "work_schedule"] == ["nights"]
    assert [c["value"] for c in mem.claim_history("work_schedule")] == ["days", "nights"]


def test_claims_reach_the_evidence_for_a_question_about_them(mem):
    mem.store_memory("I work nights now, the warehouse changed my shift", source="user")
    hits = _claim_hits(mem, "what do you know about my work schedule", None)
    assert hits and "nights" in hits[0]["text"] and hits[0]["id"].startswith("claim:")
    assert _claim_hits(mem, "what is the capital of France", None) == []


def test_a_period_question_gets_what_held_then(mem):
    now = time.time()
    c = mem._claims_conn()
    claims.record(c, "work_schedule", "days", valid_from=now - 200 * 86400, now=now - 199 * 86400)
    claims.record(c, "work_schedule", "nights", valid_from=now - 20 * 86400, now=now - 19 * 86400)
    c.commit()
    c.close()
    hits = _claim_hits(mem, "what was my job schedule then", (now - 120 * 86400, now - 100 * 86400))
    assert len(hits) == 1 and "days" in hits[0]["text"]


def test_a_claim_that_is_not_the_users_own_word_is_disputed_not_substituted():
    conn = sqlite3.connect(":memory:")
    claims.record(conn, "lives_in", "Berlin", origin="user_said", now=_d("2026-05-01"))
    claims.record(conn, "lives_in", "Oslo", origin="eli_said", now=_d("2026-06-01"))
    assert [c["value"] for c in claims.current(conn)] == ["Berlin"]
    conflict = claims.open_conflicts(conn)[0]
    assert [c["value"] for c in conflict["disputed"]] == ["Oslo"] and conflict["standing"][0]["contested"] == 1
    assert "Berlin" in claims.conflict_question(conflict) and "Oslo" in claims.conflict_question(conflict)


def test_the_user_settles_a_conflict():
    conn = sqlite3.connect(":memory:")
    claims.record(conn, "lives_in", "Berlin", origin="user_said", now=_d("2026-05-01"))
    claims.record(conn, "lives_in", "Oslo", origin="eli_said", now=_d("2026-06-01"))
    claims.resolve_conflict(conn, "lives_in", "Oslo", now=_d("2026-06-05"))
    assert [c["value"] for c in claims.current(conn)] == ["Oslo"] and claims.open_conflicts(conn) == []
    assert [c["status"] for c in claims.history(conn, "lives_in")] == ["superseded", "current"]


def test_hedged_and_hypothetical_statements_are_not_claims():
    for t in ("maybe I will work nights", "I might move to Berlin next year", "I am thinking of working at Acme Corp", "what if I work nights"):
        assert claims.extract(t) == []


def test_a_disputed_claim_reaches_the_evidence_marked(mem):
    mem.store_memory("I live in Berlin these days with my partner", source="user")
    c = mem._claims_conn()
    claims.record(c, "lives_in", "Oslo", origin="eli_said")
    c.commit()
    c.close()
    text = " ".join(h["text"] for h in _claim_hits(mem, "where do I live, what do you know about me", None))
    assert "Berlin" in text and "DISPUTED" in text


# "What am I watching?" is answered from the newest thing the user said or ELI saw, with dates.
#
# Live: the user said "MBW is on in the background" a week earlier and ELI's own pause replies named
# "Moorland Bird Watch — S2 E5", yet ELI answered with a show from a month before and then said there was no
# record of MBW. Nothing about what the user was watching ever became a claim: the extractor knew only
# jobs, homes and pets, and only read the messages the storage policy kept.
def _dt(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))


@pytest.mark.parametrize("said,expected", [
    ("Back from the shop, MBW is on in the background, and i am saying hello", ("watching", "MBW")),
    ("i already said that i am watching MBW!!!", ("watching", "MBW")),
    ("finished the washing up. I'm actually watching XR harbour walks", ("watching", "XR harbour walks")),
    ("you're back then. Still watching harbour walks XR, pal.", ("watching", "harbour walks XR")),
    ("all fine, still watching ocean diaries", ("watching", "ocean diaries")),
    ("i am still watching the lantern road now, it is on its 2nd film", ("watching", "the lantern road")),
    ("I've been playing Hollow Orchard every evening", ("playing", "Hollow Orchard")),
    ("I'm reading The Salt Almanac at the moment", ("reading", "The Salt Almanac")),
    ("the house is never quiet, and i am watching ocean diaries, you?", ("watching", "ocean diaries")),
])
def test_what_the_user_is_doing_now(said, expected):
    assert [(a["relation"], a["value"]) for a in claims.activities(said)] == [expected]


@pytest.mark.parametrize("said", [
    "Do you recall what I am watching at the moment?",
    "look again for which series i am currently watching please!!",
    "no, i want to know the newest show i am watching",
    "later i am going to play hollow orchard and watch something",
    "the long walk and watching it was a few days ago",
    "I'm not watching anything",
    "i played chess last night",
    "i am watching ____ and about to start playing ____.",
    "maybe i'm watching it later",
])
def test_questions_plans_and_the_past_are_not_claims(said):
    assert claims.activities(said) == []


def test_the_newest_wins_and_an_older_statement_is_history():
    conn = sqlite3.connect(":memory:")
    claims.record(conn, "watching", "Coastal Paths", valid_from=_dt("2026-09-01 20:00"))
    claims.record(conn, "watching", "MBW", valid_from=_dt("2026-10-01 09:00"))
    claims.record(conn, "watching", "The Lantern Road", valid_from=_dt("2026-08-25 14:00"))   # read back late
    assert [c["value"] for c in claims.current(conn)] == ["MBW"]
    old = [c for c in claims.history(conn, "watching") if c["value"] == "The Lantern Road"][0]
    assert old["status"] == "superseded" and old["valid_to"] == _dt("2026-10-01 09:00")


def test_what_eli_saw_playing_replaces_what_was_said_and_initials_are_the_same_show():
    conn = sqlite3.connect(":memory:")
    claims.record(conn, "watching", "Coastal Paths", valid_from=_dt("2026-09-01 20:00"))
    claims.record(conn, "watching", "Moorland Bird Watch", origin="observed", valid_from=_dt("2026-10-01 15:00"))
    assert [c["value"] for c in claims.current(conn)] == ["Moorland Bird Watch"] and claims.open_conflicts(conn) == []
    claims.record(conn, "watching", "MBW", valid_from=_dt("2026-10-08 21:00"))          # the same show, confirmed
    assert [c["value"] for c in claims.current(conn)] == ["Moorland Bird Watch"]


def test_an_episode_in_a_player_names_the_show():
    assert claims.observed_show("Moorland Bird Watch — S1 E10 – The Final") == "Moorland Bird Watch"
    assert claims.observed_show("Moorland Bird Watch S02E05") == "Moorland Bird Watch"
    assert claims.observed_show("Orlo — Trouble") == ""


def test_history_is_read_in_order_and_answers_the_question(mem):
    conn = mem._get_connection()
    for role, text, when in [
        ("user", "I'm actually watching Coastal Paths", "2026-09-01 20:12"),
        ("user", "Back from the shop, MBW is on in the background", "2026-10-01 09:15"),
        ("assistant", "Paused — chromium (Moorland Bird Watch — S2 E5 – Owl Week)", "2026-10-01 22:43"),
        ("user", "Do you recall what I am watching at the moment?", "2026-10-08 21:38"),
    ]:
        conn.execute("INSERT INTO conversation_turns (session_id, user_id, role, content, ts, timestamp) "
                     "VALUES ('s', 'u', ?, ?, ?, ?)", (role, text, _dt(when), _dt(when)))
    conn.commit()
    conn.close()
    assert mem.backfill_claims()["claims"] == 3
    assert mem.backfill_claims().get("skipped") == "current"
    hits = _claim_hits(mem, "what show am I currently watching", None)
    assert len(hits) == 1 and "watching: MBW" in hits[0]["text"] and "since 2026-10-01" in hits[0]["text"]


def test_a_message_said_in_passing_becomes_a_claim(mem):
    mem.add_conversation_turn("user", "just saying hello, MBW is on in the background")
    assert [c["value"] for c in mem.claims_current() if c["relation"] == "watching"] == ["MBW"]


def test_how_sure_a_claim_is_follows_what_bore_it_out(conn):
    """One statement of the user's: 0.67; each confirmation from another root raises it, never to 1;
    a contradiction pulls both sides back, and the user's own word stays ahead of what was observed."""
    [c] = claims.current(conn)
    assert c["confidence"] == pytest.approx(0.6667, abs=1e-3)
    claims.record(conn, "work_schedule", "nights", now=_d("2026-07-01"), root_id=11)
    claims.record(conn, "work_schedule", "nights", now=_d("2026-07-02"), root_id=12)
    [c] = claims.current(conn)
    assert c["confirmations"] == 2 and c["confidence"] == pytest.approx(0.75, abs=1e-3)
    assert "sure 0.75: you said so twice" in claims.describe(c)
    claims.record(conn, "work_schedule", "days", now=_d("2026-07-03"), origin="observed")
    [standing] = claims.current(conn)
    disputed = conn.execute("SELECT confidence FROM memory_claims WHERE status = 'disputed'").fetchone()[0]
    assert standing["contested"] and disputed < 0.5 < standing["confidence"] < 0.75
    assert "contradicted" in claims.describe(standing)


# ── the chance a claim is right (eli/memory/claims.chance, kind "claim" in epistemics) ────────────
@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    from eli.runtime import evidence_ledger as led
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    return path


def test_the_chance_is_the_sureness_until_claims_of_that_origin_were_checked(conn, ledger):
    from eli.cognition import epistemics as ep
    row = claims.current(conn)[0]
    assert claims.chance(row).p == pytest.approx(row["confidence"]) and claims.chance(row).n == 0
    # replies rested on inferred claims that the user kept correcting: an inference's chance comes down
    for i in range(12):
        ep.assess(ep.CLAIM, 0.6, request_id=ep.claim_request_id(100 + i, f"turn{i}"), class_="inferred")
        ep.settle(ep.claim_request_id(100 + i, f"turn{i}"), i % 4 == 0, how="correction", kind=ep.CLAIM)
    inferred = dict(row, origin="inferred", confidence=0.6)
    prob = claims.chance(inferred)
    assert prob.n == 12 and prob.p < 0.6 and prob.class_ == "inferred"
    said = claims.chance(row)
    assert said.class_ == "" and said.p > prob.p, "no outcomes of the user's own word yet: fitted on the kind, and still ahead"
    assert "chance" in claims.basis(inferred) and "12 such claims were checked" in claims.basis(inferred)
    assert ep.outcomes(ep.ANSWER) == [], "a claim's outcome is never an answer's"


def test_a_late_report_of_an_earlier_value_is_as_sure_as_its_source(conn):
    cid = claims.record(conn, "work_schedule", "evenings", valid_from=_d("2026-03-01"), now=_d("2026-07-01"),
                        origin="document", confidence=0.99)
    row = next(r for r in claims.history(conn, "work_schedule") if r["id"] == cid)
    assert row["status"] == "superseded" and row["confidence"] == claims.sureness("document", 1) != 0.99


def test_the_user_settling_a_conflict_settles_the_figures_given_for_both_claims(ledger):
    from eli.cognition import epistemics as ep
    c = sqlite3.connect(":memory:")
    first = claims.record(c, "lives_in", "Berlin", origin="user_said")
    second = claims.record(c, "lives_in", "Lisbon", origin="document")       # disputed: the user's word stands
    ep.assess(ep.CLAIM, 0.6, request_id=ep.claim_request_id(first, "t1"), class_="user_said")
    ep.assess(ep.CLAIM, 0.4, request_id=ep.claim_request_id(second, "t1"), class_="document")
    claims.resolve_conflict(c, "lives_in", "Lisbon")
    outcomes = {rid: y for rid, y in ((p[0]["request_id"], p[1]["outcome"]) for p in
                                      ep._ledger().assessment_pairs("claim", since=0, assessed_type="claim_assessed",
                                                                    outcome_type="claim_outcome"))}
    assert outcomes[ep.claim_request_id(second, "t1")] == "ok" and outcomes[ep.claim_request_id(first, "t1")] == "failed"


def test_a_claim_reaches_the_evidence_scored_by_its_chance_and_assessed_for_the_turn(mem, ledger):
    from eli.cognition import epistemics as ep
    from eli.kernel.request_context import request_id_var, turn_facts_var
    mem.store_memory("I work nights now, the warehouse changed my shift", source="user")
    t1 = request_id_var.set("turn-9")
    t2 = turn_facts_var.set({"request_id": "turn-9"})
    try:
        from eli.memory.retrieval import _claim_hits
        hits = _claim_hits(mem, "what shift do i work", None)
        facts = turn_facts_var.get()
    finally:
        request_id_var.reset(t1)
        turn_facts_var.reset(t2)
    assert hits and hits[0]["kind"] == "claim" and hits[0]["verification_status"] == "verified"
    assert hits[0]["score"] == hits[0]["chance"] == pytest.approx(claims.sureness("user_said", 1))
    used = facts["claims_used"]
    assert len(used) == 1 and next(iter(used.values()))["request_id"].endswith(":turn-9")
    assert len(ep._ledger().open_assessments("claim", assessed_type="claim_assessed", outcome_type="claim_outcome",
                                             subject_prefix="claim:")) == 1
