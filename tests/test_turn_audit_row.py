"""Every CognitiveEngine.process() call runs as its own turn and writes one audit row.

The row is what "why did you do that?" is answered from: which rule routed the turn, what ran,
whether it went to the web and why. process() is the wrapper that keeps the turn's facts for
its whole length (a streamed reply included) and writes the row when the turn really ends.
"""
import gc
from pathlib import Path
from unittest.mock import patch

import pytest

from eli.kernel import request_context as rc
from eli.kernel.engine import MAIN_REPLY_DONE_SENTINEL, CognitiveEngine
from eli.runtime import orchestrator_audit_ledger as L


@pytest.fixture()
def audit_db(tmp_path, monkeypatch):
    # Rows an earlier test's turns queued (or left to a stream's finalizer) are written before this
    # test's database is set, not into it.
    gc.collect()
    L.flush(timeout=10.0)
    db = tmp_path / "orch.sqlite3"
    monkeypatch.setenv("ELI_ORCHESTRATOR_AUDIT_DB", str(db))
    yield db
    L.flush(timeout=10.0)


def _rows(db):
    assert L.flush(timeout=10.0)
    if not Path(db).exists():
        return []
    return list(reversed(L.recent_turns(limit=100, db_path=db)))


def _bare(session="gui-session", user="desktop"):
    eng = CognitiveEngine.__new__(CognitiveEngine)
    eng.session_id = session
    eng.user_id = user
    return eng


def _run(eng, impl, *args, **kwargs):
    with patch.object(CognitiveEngine, "_process_impl", impl):
        return eng.process(*args, **kwargs)


def test_a_command_turn_writes_one_row_with_its_own_identity(audit_db):
    eng = _bare()
    out = _run(eng, lambda self, *a, **k: {"ok": True, "action": "SET_TIMER", "response": "Timer set."},
               "set a timer", reasoning_mode="quick")
    assert out["action"] == "SET_TIMER"
    rows = _rows(audit_db)
    assert len(rows) == 1
    assert (rows[0]["action"], rows[0]["session_id"], rows[0]["user_id"], rows[0]["ok"]) == (
        "SET_TIMER", "gui-session", "desktop", 1)
    assert rows[0]["reasoning_mode"] == "quick" and rows[0]["request_id"].startswith("req-")


def test_the_row_says_what_the_pipeline_did(audit_db):
    def impl(self, *a, **k):
        rc.note_turn_fact("via", "chat.long_question_guard")
        rc.note_turn_fact("escalation", "searched the web, the results did not match the question, so declined to guess")
        rc.turn_facts_var.get().setdefault("executed_actions", []).append("WEB_SEARCH")
        return "I searched and found nothing that matched."

    _run(_bare(), impl, "what has been going on?")
    outcome = _rows(audit_db)[0]["outcome"]
    assert outcome.startswith("ok; routed by chat.long_question_guard")
    assert "searched the web" in outcome and "also executed: WEB_SEARCH" in outcome


def test_a_failed_action_and_an_exception_are_both_recorded(audit_db):
    eng = _bare()
    _run(eng, lambda self, *a, **k: {"ok": False, "action": "OPEN_APP", "error": "not_installed"}, "open cinejoy")

    def boom(self, *a, **k):
        raise ValueError("router exploded")

    with pytest.raises(ValueError):
        _run(eng, boom, "anything")
    rows = _rows(audit_db)
    assert [(r["ok"], r["outcome"]) for r in rows] == [(0, "not_installed"), (0, "error: ValueError: router exploded")]


def _streaming(*pieces):
    def impl(self, *a, **k):
        def gen():
            for p in pieces:
                yield p
        return gen()
    return impl


def test_a_streamed_reply_is_recorded_once_when_it_ends(audit_db):
    stream = _run(_bare(), _streaming("Hello ", "there.", MAIN_REPLY_DONE_SENTINEL), "hi", stream=True)
    assert _rows(audit_db) == []  # nothing until the stream is read
    assert "".join(p for p in stream if p != MAIN_REPLY_DONE_SENTINEL) == "Hello there."
    rows = _rows(audit_db)
    assert len(rows) == 1 and rows[0]["ok"] == 1 and rows[0]["action"] == "CHAT"


def test_the_turns_facts_last_for_the_whole_stream(audit_db):
    seen = []

    def impl(self, *a, **k):
        def gen():
            seen.append(rc.turn_facts_var.get()["user_input"])
            yield "one "
            rc.note_turn_fact("via", "fallback.chat")
            seen.append(rc.turn_facts_var.get()["user_input"])
            yield "two."
        return gen()

    assert "".join(_run(_bare(), impl, "tell me", stream=True)) == "one two."
    assert seen == ["tell me", "tell me"]
    assert rc.turn_facts_var.get() is None  # nothing left on the caller's own context
    assert "routed by fallback.chat" in _rows(audit_db)[0]["outcome"]


def test_an_empty_stream_a_closed_one_and_a_dropped_one(audit_db):
    eng = _bare()
    assert list(_run(eng, _streaming(), "hi", stream=True)) == []
    closed = _run(eng, _streaming("a", "b", "c"), "hi", stream=True)
    next(closed)
    closed.close()
    dropped = _run(eng, _streaming("a"), "hi", stream=True)
    del dropped
    gc.collect()
    assert [r["outcome"] for r in _rows(audit_db)] == ["empty_reply", "cancelled", "abandoned"]


def test_a_nested_turn_gets_its_own_row_linked_to_its_parent(audit_db):
    eng = _bare()

    def inner(self, text, **k):
        return {"ok": True, "action": "DATE", "response": "Saturday"}

    def outer(self, text, **k):
        with patch.object(CognitiveEngine, "_process_impl", inner):
            self.process("what day is it")
        return {"ok": True, "action": "MULTI_COMMAND", "response": "done"}

    _run(eng, outer, "what day is it and play something")
    rows = _rows(audit_db)
    assert [r["action"] for r in rows] == ["DATE", "MULTI_COMMAND"]
    assert rows[0]["parent_request_id"] == rows[1]["request_id"] and not rows[1]["parent_request_id"]


def test_a_turn_that_published_nothing_does_not_borrow_the_last_turns_action(audit_db):
    eng = _bare()

    def first(self, *a, **k):
        self._last_request_meta = {"action": "PLAY_MEDIA", "confidence": 0.9}
        return "Playing."

    _run(eng, first, "play something")
    _run(eng, lambda self, *a, **k: "Morning.", "good morning")
    rows = _rows(audit_db)
    assert rows[0]["action"] == "PLAY_MEDIA" and rows[1]["action"] == "UNKNOWN"


def test_a_follow_up_keeps_the_period_through_the_session_store():
    eng = _bare(session="s-1")
    eng._sticky_set("last_query_window", ((1.0, 2.0), 123.0))
    assert eng._sticky_get("last_query_window") == ((1.0, 2.0), 123.0)
    assert _bare(session="s-2")._sticky_get("last_query_window") is None
