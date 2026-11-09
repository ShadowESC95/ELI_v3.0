"""personal_memory_surface.is_personal_memory_query — routes "what do you know about
me" style asks to the grounded personal-memory answer (not a web/chat guess). Pure.
"""
from __future__ import annotations

from eli.runtime.personal_memory_surface import is_personal_memory_query


def test_personal_queries_match():
    assert is_personal_memory_query("what do you know about me?") is True
    assert is_personal_memory_query("what do you remember about me") is True


def test_non_personal_do_not_match():
    assert is_personal_memory_query("what's the weather in Dublin") is False
    assert is_personal_memory_query("") is False
    assert is_personal_memory_query(None) is False


# ── an answer about "me" shows only the asker's rows ──────────────────────────
import sqlite3

import pytest


@pytest.fixture()
def two_users(tmp_path, monkeypatch):
    """One user.sqlite3 holding the owner's facts and messages and another API user's message."""
    from eli.kernel import request_context as rc, state
    from eli.runtime import personal_memory_clean_response as clean, personal_memory_surface as surface
    owner = state.get_active_user_id()
    db = tmp_path / "db" / "user.sqlite3"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, value TEXT, content TEXT, tags TEXT, kind TEXT,
                               source TEXT, ts REAL, timestamp REAL);
        CREATE TABLE user_patterns (id INTEGER PRIMARY KEY, pattern_type TEXT, pattern_data TEXT, ts REAL,
                                    timestamp REAL);
        CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts REAL, timestamp REAL,
                                         user_id TEXT);
    """)
    con.execute("INSERT INTO memories(text, kind, source, ts) VALUES ('My favourite colour is green.', 'fact', 'user', 1)")
    con.execute("INSERT INTO user_patterns(pattern_type, pattern_data, ts) VALUES ('preference.drink', 'User prefers oolong tea', 1)")
    con.execute("INSERT INTO conversation_turns(role, content, ts, user_id) VALUES ('user', 'my name is Rowan', 1, ?)", (owner,))
    con.execute("INSERT INTO conversation_turns(role, content, ts, user_id) VALUES ('user', 'my name is Tamsin', 2, 'api-user-b')")
    con.commit()
    con.close()
    monkeypatch.setattr(clean, "USER_DB", db)
    monkeypatch.setattr(clean, "AGENT_DB", tmp_path / "db" / "agent.sqlite3")
    monkeypatch.setattr(surface, "_artifacts_dir", lambda: tmp_path)
    monkeypatch.setattr(state, "load_user_profile",
                        lambda user_id=None: {"name": "Rowan"} if user_id in (None, owner) else {})

    def asked_by(uid):
        return rc.user_id_var.set(uid)
    return asked_by, rc.user_id_var


def test_another_user_is_not_shown_the_owners_facts_or_messages(two_users):
    from eli.runtime.personal_memory_clean_response import build_clean_personal_memory_response
    from eli.runtime.personal_memory_surface import personal_memory_surface
    asked_by, var = two_users
    token = asked_by("api-user-b")
    try:
        clean = build_clean_personal_memory_response("what do you know about me in depth")
        surface = personal_memory_surface("who am i")
    finally:
        var.reset(token)
    for out in (clean, surface):
        assert "green" not in out and "oolong" not in out and "Rowan" not in out
    assert "Tamsin" in surface


def test_the_owner_still_sees_their_own(two_users):
    from eli.runtime.personal_memory_clean_response import build_clean_personal_memory_response
    from eli.runtime.personal_memory_surface import personal_memory_surface
    clean = build_clean_personal_memory_response("what do you know about me")
    surface = personal_memory_surface("who am i")
    assert "green" in clean and "Rowan" in clean
    assert "Rowan" in surface and "oolong" in surface and "Tamsin" not in surface
