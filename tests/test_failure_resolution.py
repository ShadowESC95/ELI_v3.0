"""Resolved failures are hidden from the live Self-Improve panel but preserved;
unresolved failures are never omitted."""
from __future__ import annotations
import sqlite3
import pytest
from eli.memory.memory import Memory


def _mem(tmp_path):
    m = Memory(db_path=str(tmp_path / "user.sqlite3"))
    conn = m._get_connection()
    # ensure failures table exists with the columns the helper uses
    conn.execute("CREATE TABLE IF NOT EXISTS failures(id INTEGER PRIMARY KEY, "
                 "user_input TEXT, command TEXT, error TEXT, occurrence_count INT, "
                 "timestamp REAL, ts REAL, status TEXT)")
    rows = [
        ("q1", "MESSAGE_TIME_QUERY", "cannot access local variable 'get_memory'", 3),
        ("q2", "NEWS_SEARCH", "Unsupported executor action: NEWS_SEARCH", 2),
        ("q3", "PLAY_MEDIA", "I couldn't reach Spotify to play x", 1),  # unresolved
    ]
    for ui, cmd, err, n in rows:
        conn.execute("INSERT INTO failures(user_input,command,error,occurrence_count,timestamp,ts,status)"
                     " VALUES(?,?,?,?,?,?, 'open')", (ui, cmd, err, n, 1.0, 1.0))
    conn.commit(); conn.close()
    return m


def test_mark_resolved_hides_only_fixed(tmp_path):
    m = _mem(tmp_path)
    assert len(m.get_recent_failures()) == 3
    n = m.mark_failure_resolved(error_like="%get_memory%")
    assert n == 1
    n2 = m.mark_failure_resolved(error_like="%Unsupported executor action%")
    assert n2 == 1
    live = m.get_recent_failures()
    errs = [f["error"] for f in live]
    assert len(live) == 1                                   # unresolved kept
    assert "Spotify" in errs[0]
    assert not any("get_memory" in e for e in errs)         # resolved hidden
    # history preserved (not deleted)
    conn = m._get_connection()
    total = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    conn.close()
    assert total == 3


def test_mark_resolved_by_id_and_noop(tmp_path):
    m = _mem(tmp_path)
    assert m.mark_failure_resolved(id=999) == 0     # nonexistent
    assert m.mark_failure_resolved() == 0           # no criteria
    assert m.mark_failure_resolved(id=3) == 1       # the Spotify row, by id
    assert len(m.get_recent_failures()) == 2


def test_one_fault_hit_by_differently_worded_requests_is_one_recurring_failure(tmp_path):
    """Failures were keyed by the exact (sentence, error) pair, so a code fault hit from three wordings
    stayed three rows of one occurrence each, and nothing that looks for recurring faults saw it."""
    import json
    m = Memory(db_path=str(tmp_path / "agent.sqlite3"), db_type="agent")
    for said, path in (("open my notes", "/tmp/a.txt"), ("show the notes file", "/home/x/b.txt"),
                       ("can you open notes", "/srv/c.txt")):
        m.log_failure(said, error=f"FileNotFoundError: cannot open '{path}' (errno 2)", context={"action": "OPEN_FILE"})
    m.log_failure("open the music", error="FileNotFoundError: cannot open '/tmp/song.mp3' (errno 2)",
                  context={"action": "PLAY_MEDIA"})
    conn = sqlite3.connect(m.db_path)
    try:
        rows = conn.execute("SELECT command, occurrence_count, user_input, examples FROM failures ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [(r[0], r[1]) for r in rows] == [("OPEN_FILE", 3), ("PLAY_MEDIA", 1)], "the action's fault, counted"
    assert rows[0][2] == "can you open notes" and json.loads(rows[0][3]) == [
        "open my notes", "show the notes file", "can you open notes"], "the requests that hit it, as examples"
