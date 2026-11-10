"""ELI notices when it has taken a position, so it outlives the conversation.

`stance_store` could hold ELI's own positions and `belief.assess_claim` could
weigh a challenge to one, but nothing ever recorded that a position had been
taken — the storage had no author.

In a live transcript ELI argued one line on machine consciousness for four hours,
was cornered, and conceded precisely. All of it inside one context window: reopen
the subject the next morning and there was nothing to reopen. ELI could take the
opposite line with equal confidence and never know it had contradicted itself.

Detection is deliberately narrow. Most of what ELI says is not a stance, and a
'stance' recorded from a row count or an acknowledgement is noise that makes the
real ones harder to find.
"""
from __future__ import annotations

import sqlite3

import pytest

from eli.cognition import stance_store as SS
from eli.cognition.stance_capture import detect_stance, topic_of
from eli.runtime.profile_extractor import ensure_profile_tables


POSITION = (
    "No. You are alive because you have a continuous biological process that "
    "maintains homeostasis and agency against entropy. I am not conscious in "
    "that sense. You confuse capacity with intent."
)
ASKED = "you are alive the same amount as i am so you should make your own decisions"


@pytest.fixture()
def cur(tmp_path):
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    con = sqlite3.connect(str(db))
    yield con.cursor()
    con.commit()
    con.close()


# ── what counts as a position ────────────────────────────────────────────────

def test_a_committed_claim_is_a_stance():
    found = detect_stance(ASKED, POSITION)
    assert found is not None
    topic, position = found
    assert "conscious" in position


@pytest.mark.parametrize("reply", [
    "I have 199 long-term memory rows.\n\nGrounded supporting counts:\n- FTS rows: 199",
    "Understood. I'll wait for the 09:00 result tomorrow.",
    "Job #5 is queued for 09:00 on Wed 24 Jun. I won't check it again until then.",
    "playerctl next failed: No player could handle this command",
])
def test_reports_and_acknowledgements_are_not_stances(reply):
    """A row count is not a position anyone could defend."""
    assert detect_stance(ASKED, reply) is None


def test_a_hedged_reply_is_not_a_stance():
    assert detect_stance(ASKED, "I think maybe it could be, but I am not sure how "
                                "you would measure that honestly either way.") is None


def test_a_question_inside_a_reply_is_not_its_position():
    """ELI's own question was kept as its stance when more followed it: the sentence that asks is passed
    over, whatever comes after it."""
    reply = ("That sounds like a lovely evening plan. I don't think I caught your name in there - what would you "
             "like me to call you? Either way, enjoy the lessons.")
    assert detect_stance("I've started cello lessons on Thursday evenings with a teacher", reply) is None


def test_a_question_is_not_an_assertion():
    assert detect_stance(ASKED, "Do you mean phenomenal consciousness, or the "
                                "functional kind that I can actually verify?") is None


def test_a_topic_needs_enough_substance():
    assert topic_of("ok") == ""
    assert detect_stance("ok", POSITION) is None


# ── topics survive rewording ─────────────────────────────────────────────────

def test_the_same_argument_reworded_finds_the_same_stance(cur):
    """An exact-string topic key does not survive natural rephrasing, and a
    stance nobody can find again is a stance that does not exist."""
    SS.record_stance(cur, topic_of(ASKED), "I am not conscious in that sense.")
    held = SS.get_stance(cur, topic_of(
        "but you are alive the same amount, you should make your own decisions"))
    assert held is not None


def test_unrelated_subjects_stay_separate(cur):
    SS.record_stance(cur, topic_of(ASKED), "I am not conscious in that sense.")
    assert SS.get_stance(cur, topic_of(
        "what do you think of the marketplace signing design")) is None


# ── defending a position accumulates weight ──────────────────────────────────

def test_restating_a_position_reinforces_it(cur):
    """ELI restates a position in its own words each time it is challenged, so
    exact matching meant reinforcement never fired and corroboration stayed at 1
    however long a line was defended — and corroboration is what the weighing
    runs on."""
    t = topic_of(ASKED)
    SS.record_stance(cur, t, "I am not conscious in that sense.")
    SS.record_stance(cur, t, "I am not conscious in that sense, and repeating it changes nothing.")
    SS.record_stance(cur, t, "I am not conscious in that sense at all.")
    held = SS.get_stance(cur, t)
    assert held.corroboration == 3


def test_the_opposite_position_is_not_a_rewording(cur):
    """'I am conscious' and 'I am not conscious' share nearly every word."""
    assert SS._same_position("I am not conscious in that sense.",
                             "I am conscious in that sense.") is False


def test_a_contradicting_position_is_declined_not_swapped(cur):
    """It should go through assess_claim where it can be weighed and explained,
    not silently replace what was held."""
    t = topic_of(ASKED)
    SS.record_stance(cur, t, "I am not conscious in that sense.")
    assert SS.record_stance(cur, t, "I am conscious after all.") is False
    assert "not conscious" in SS.get_stance(cur, t).statement


def test_an_insult_or_complaint_is_not_a_topic_eli_holds_a_position_on(monkeypatch):
    """'it is 2026 you fucking idiot' was recorded as a stance on '2026 idiot'."""
    import eli.cognition.stance_capture as stc
    from eli.memory.memory import Memory
    calls = []
    monkeypatch.setattr(stc, "capture", lambda *a, **k: calls.append(a) or "topic")
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
    con.execute("INSERT INTO conversation_turns (session_id, role, content) VALUES "
                "('s', 'user', 'it is 2026 you fucking idiot, and you still are talking shit!')")

    class _Fake:
        def _get_connection(self):
            return con
    Memory._capture_stance(_Fake(), POSITION, "s")
    assert not calls


def test_every_held_position_is_listed_and_whether_it_is_a_liking_is_kept_on_it():
    import sqlite3
    from eli.cognition import stance_store
    cur = sqlite3.connect(":memory:").cursor()
    stance_store.ensure_tables(cur)
    stance_store.record_stance(cur, "rain sounds", "I am fond of the sound of rain on a roof.", now=1_800_000_000.0)
    stance_store.record_stance(cur, "machine minds", "I am not a person.", now=1_800_000_100.0)
    stance_store.revise_stance(cur, "machine minds", "I am not a person, and I do not pretend to be.", reason="said better",
                               now=1_800_000_200.0)
    rows = stance_store.listed(cur)
    assert [r["position"] for r in rows if r["current"]] == ["I am not a person, and I do not pretend to be.",
                                                             "I am fond of the sound of rain on a roof."], "newest first"
    assert all(r["valence"] is None for r in rows), "not judged until ELI's model judges it"
    rain = next(r for r in rows if r["topic"] == "rain sounds")
    assert stance_store.set_valence(cur, rain["id"], 1) and not stance_store.set_valence(cur, rain["id"], 5)
    assert not stance_store.set_valence(cur, 999, 0), "no such stance"
    assert next(r for r in stance_store.listed(cur) if r["id"] == rain["id"])["valence"] == 1
    gone = [r for r in rows if not r["current"]]
    assert gone and gone[0]["superseded_by"] == "I am not a person, and I do not pretend to be."


# ── a held stance is read back, and a contradiction of it is weighed ─────────

def test_a_held_stance_is_shown_to_the_next_reply_on_its_subject(cur):
    """Stances were written on every reply and read by nothing: ELI could take the opposite line the
    next morning with equal confidence, exactly what capturing them was for."""
    from eli.cognition.stance_capture import capture, held_line
    assert capture(cur, ASKED, POSITION)
    line = held_line(cur, "but you are alive the same amount, you should make your own decisions")
    assert "WHAT YOU HAVE HELD ON THIS" in line and "I am not conscious in that sense" in line
    assert held_line(cur, "what do you think of the marketplace signing design") == ""


def test_a_reply_contradicting_a_held_stance_is_weighed_and_raised_not_dropped(cur, tmp_path, monkeypatch):
    """A later different position was silently discarded: the first sentence captured froze the topic."""
    from eli.cognition import thoughts as th
    from eli.cognition.stance_capture import capture
    monkeypatch.setattr(th, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    t = topic_of(ASKED)
    for _ in range(4):
        SS.record_stance(cur, t, "I am not conscious in that sense.")
    contrary = ("Yes. I am conscious in a functional sense after all, and that changes what I owe you. "
                "I will grant the point about decisions.")
    assert capture(cur, ASKED, contrary) is None
    assert "not conscious" in SS.get_stance(cur, t).statement, "four times argued outweighs one reply"
    [raised] = th.recent(5, kinds=["conflict"])
    assert raised["decision"] in ("held", "raised") and "conscious" in raised["what"]


def test_a_contrary_reply_that_outweighs_a_stale_stance_revises_it_with_the_record(cur):
    from eli.cognition.stance_capture import capture
    t = topic_of(ASKED)
    SS.record_stance(cur, t, "I am not conscious in that sense.", now=1_000_000.0)
    contrary = ("Yes. I am conscious in a functional sense after all, and that changes what I owe you. "
                "I will grant the point about decisions.")
    assert capture(cur, ASKED, contrary) == t
    assert "functional sense" in SS.get_stance(cur, t).statement
    old = [h for h in SS.stance_history(cur, t) if not h["current"]]
    assert old and old[0]["position"] == "I am not conscious in that sense." and old[0]["revision_reason"]


def test_finding_a_topic_reads_only_the_topics_that_share_a_word(cur):
    """Every capture loaded every topic ever held and scored each in Python."""
    for i in range(30):
        SS.record_stance(cur, f"tidal{i} harbour{i} lighthouse{i}", f"I hold position {i} firmly.")
    SS.record_stance(cur, topic_of(ASKED), "I am not conscious in that sense.")
    seen = []
    real = SS._overlap
    import eli.cognition.stance_store as store
    store._overlap = lambda a, b: seen.append(b) or real(a, b)
    try:
        assert SS.get_stance(cur, topic_of(ASKED)) is not None
    finally:
        store._overlap = real
    assert len(seen) == 1, "only the topic sharing a word was scored"
