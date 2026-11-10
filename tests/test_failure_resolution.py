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


def test_an_improvement_not_found_again_is_closed_and_one_found_again_reopens(tmp_path):
    """Every improvement was written 'pending' and nothing ever closed one: the prompt counted every
    suggestion ever made, fixed or not."""
    import time as _time
    m = Memory(db_path=str(tmp_path / "agent.sqlite3"), db_type="agent")
    m.log_improvement("code_quality", "long function in module a")
    m.log_improvement("code_quality", "unused import in module b")
    assert m.close_improvements("code_quality", ["unused import in module b"]) == 1
    open_now = [i["description"] for i in m.get_recent_improvements(open_only=True)]
    assert open_now == ["unused import in module b"]
    m.log_improvement("code_quality", "long function in module a")
    assert len(m.get_recent_improvements(open_only=True)) == 2, "found again: open again"
    conn = sqlite3.connect(m.db_path)
    conn.execute("UPDATE improvements SET timestamp = ?, ts = ?", (_time.time() - 365 * 86400,) * 2)
    conn.commit()
    conn.close()
    assert m.tidy_learning_tables()["improvements_closed"] == 2
    assert m.get_recent_improvements(open_only=True) == [] and len(m.get_recent_improvements()) == 2

def test_the_daemons_rescan_closes_what_it_no_longer_finds_and_the_prompt_counts_only_open_ones(tmp_path, monkeypatch):
    """The code-quality scan wrote what it found every ten minutes and closed nothing; the self-status
    prompt then told the model every row ever written was a pending suggestion."""
    from eli.planning import proactive_daemon as pd
    m = Memory(db_path=str(tmp_path / "agent.sqlite3"), db_type="agent")
    source = tmp_path / "pkg" / "execution" / "executor_enhanced.py"
    source.parent.mkdir(parents=True)
    source.write_text("def run():\n    # TODO: say why\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(pd, "_owner_away", lambda: False)        # the scan alone, no self-maintenance
    daemon = pd.ProactiveDaemon.__new__(pd.ProactiveDaemon)
    daemon.package_root, daemon.agent_mem = tmp_path / "pkg", m
    assert [i["type"] for i in daemon.analyze_code_quality()] == ["todos"]
    assert len(m.get_recent_improvements(open_only=True)) == 1
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    assert daemon.analyze_code_quality() == []
    assert m.get_recent_improvements(open_only=True) == [] and m.get_recent_improvements()[0]["status"] == "obsolete"
    m.log_improvement("bug", "Investigate failure: open the notes → KeyError: 'name'", area="executor")
    from types import SimpleNamespace
    from eli.kernel.stages import prompt_assembly as pa
    monkeypatch.setattr("eli.memory.memory.get_agent_memory", lambda db_path=None: m)
    monkeypatch.setattr(pa, "resolve_db_paths", lambda: SimpleNamespace(agent_db=m.db_path, user_db="", memory_db=""))

    class Engine(pa.PromptAssemblyMixin):
        def _runtime_memory_snapshot(self):
            return {}

        def _is_grounded_status_query(self, text):
            return False

        def _intent_requires_grounding(self, intent, text):
            return False

        def _recent_topic_summary(self, text):
            return ""
    told = Engine()._build_grounded_evidence_context("show me your improvement log")
    assert "- improvements table: 1 open proposals" in told and "Investigate failure: open the notes" in told

