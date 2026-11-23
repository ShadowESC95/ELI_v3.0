"""Tests for eli.cognition.working_memory — ~80 tests."""
from __future__ import annotations

import pytest


# ── Import ────────────────────────────────────────────────────────────────

from eli.cognition.working_memory import WorkingMemory


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def wm():
    return WorkingMemory()


# ── Initialization ────────────────────────────────────────────────────────


# ── absorb_memory_hits ────────────────────────────────────────────────────


# ── Context building ──────────────────────────────────────────────────────


# ── State management ──────────────────────────────────────────────────────


# ── CognitionWorkingMemory ────────────────────────────────────────────────


# ── Thread safety ────────────────────────────────────────────────────────

def test_working_memory_concurrent_access(wm):
    """Basic concurrent access test."""
    import threading
    errors = []

    def _access():
        try:
            if hasattr(wm, "absorb_memory_hits"):
                wm.absorb_memory_hits([], current_turn=0)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_access) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)
    assert len(errors) == 0, f"Thread safety errors: {errors}"


# ── one working memory per user and conversation ──────────────────────────

def test_pins_are_kept_and_restored_for_their_own_user_only(tmp_path):
    """persist() emptied one table and wrote this object's pins with no user, and restore() loaded
    them all, so after a restart one user's pinned facts were restored into whoever talked next."""
    db = str(tmp_path / "wm.sqlite3")
    a = WorkingMemory(user_id="member-a", session_id="s1")
    a.pin("the boathouse key is under the blue pot", source="user_explicit", importance=0.95)
    a.persist(db)
    owner = WorkingMemory(user_id="", session_id="s2")
    owner.pin("the allotment opens at eight", source="user_explicit", importance=0.95)
    owner.persist(db)          # replaces only the owner's own rows
    assert WorkingMemory(user_id="member-b").restore(db) == 0
    back = WorkingMemory(user_id="member-a", session_id="s3")
    assert back.restore(db) == 1 and "boathouse" in back.context_block()
    mine = WorkingMemory(user_id="")
    mine.restore(db)
    assert "allotment" in mine.context_block() and "boathouse" not in mine.context_block()


def test_a_table_from_before_pins_had_a_user_is_carried_over_as_the_owners(tmp_path):
    import sqlite3
    import time
    db = str(tmp_path / "wm.sqlite3")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE working_memory_pins (key TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT, "
                "importance REAL, hit_count INTEGER, saved_at REAL)")
    con.execute("INSERT INTO working_memory_pins VALUES ('k', 'the shed code is written on the calendar', "
                "'user_explicit', 0.95, 2, ?)", (time.time(),))
    con.commit()
    con.close()
    assert WorkingMemory(user_id="member-a").restore(db) == 0
    owner = WorkingMemory(user_id="")
    assert owner.restore(db) == 1 and "shed code" in owner.context_block()


def test_the_engine_keeps_one_working_memory_per_user_and_conversation(monkeypatch):
    """One WorkingMemory on the process-wide engine went into every caller's prompt."""
    monkeypatch.setattr("eli.memory.policy.rbac_members", lambda: frozenset({"member-a", "member-b"}), raising=False)
    from eli.kernel import request_context as rc
    from eli.kernel.engine import CognitiveEngine
    eng = CognitiveEngine()

    def as_user(user, session, fn):
        tokens = (rc.user_id_var.set(user), rc.session_id_var.set(session))
        try:
            return fn()
        finally:
            rc.session_id_var.reset(tokens[1])
            rc.user_id_var.reset(tokens[0])

    as_user("member-a", "s1", lambda: eng._working_memory.absorb_user_message(
        "remember that the boathouse key is under the blue pot"))
    assert "boathouse" in as_user("member-a", "s1", lambda: eng._working_memory.context_block())
    assert "boathouse" not in as_user("member-b", "s2", lambda: eng._working_memory.context_block())
    assert "boathouse" not in as_user("member-b", "s1", lambda: eng._working_memory.context_block())
    assert "boathouse" not in eng._working_memory.context_block()          # the desktop's own
    as_user("member-b", "s2", lambda: eng._working_memory.advance_turn())
    assert as_user("member-a", "s1", lambda: eng._working_memory._turn) == 0, "another session moved the counter"
    assert as_user("member-a", "s1", lambda: eng._engagement) is not eng._engagement


# ── what is pinned: statements, not questions or denials ─────────────────────

@pytest.mark.parametrize("said", [
    "do you remember what I said about the trip yesterday?",
    "I can't remember where I left the charger",
    "I don't remember the address",
    "remember where I parked?",
    "I'd like to book a table for two",
    "what do I like to eat?",
])
def test_a_question_or_a_denial_pins_nothing(wm, said):
    wm.absorb_user_message(said)
    assert wm.context_block() == "", said


@pytest.mark.parametrize("said,kept", [
    ("remember that my locker is number 42", "my locker is number 42"),
    ("please note that the boiler service is due in May", "the boiler service is due in May"),
    ("ok, remember I park on level 3", "I park on level 3"),
    ("can you remember that my sister is called Ana?", "my sister is called Ana"),
])
def test_a_request_to_keep_a_statement_pins_the_statement_as_said(wm, said, kept):
    wm.absorb_user_message(said)
    assert f"• {kept} (user_explicit)" in wm.context_block()


def test_a_taste_is_pinned_as_the_graph_reads_it(wm):
    wm.absorb_user_message("I don't like mushrooms")
    assert "User dislikes mushrooms" in wm.context_block()
