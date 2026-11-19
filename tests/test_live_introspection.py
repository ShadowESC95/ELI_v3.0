"""Live introspection — what the identity summary is given as evidence about the user."""
from __future__ import annotations

from eli.runtime import live_introspection as li


def test_a_name_is_mined_only_from_what_the_one_extractor_takes_as_a_declaration(tmp_path, monkeypatch):
    """Its own patterns took "my name is not listed anywhere" for the name "not listed anywhere",
    and the identity summary was given that as evidence."""
    import sqlite3
    db = tmp_path / "user.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE conversation_turns (role TEXT, content TEXT, user_id TEXT, timestamp REAL)")
        conn.executemany("INSERT INTO conversation_turns VALUES ('user', ?, 'u1', ?)", [
            ("my name is not listed anywhere", 1.0),
            ("if my name is Bob then what", 2.0),
            ("call me Robin", 3.0),
        ])
    monkeypatch.setattr(li, "_conn", lambda: sqlite3.connect(db))
    monkeypatch.setattr(li, "_active_user_id", lambda: "u1")
    monkeypatch.setattr(li, "stored_user_name", lambda: "")
    facts = li.mine_user_fact_candidates()
    assert "preferred_name: Robin" in facts
    assert not [f for f in facts if f.startswith("name:")], facts
