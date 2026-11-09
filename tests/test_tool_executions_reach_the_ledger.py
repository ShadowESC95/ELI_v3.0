"""Every executed action is recorded in the evidence ledger (names of args, not values)."""
import pytest

from eli.execution import executor_enhanced as EX
from eli.runtime import evidence_ledger as led


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    db = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: db)
    monkeypatch.delenv("ELI_LEDGER_TOOL_EVENTS", raising=False)
    return db


def _events(db):
    return [e for e in led.recent_events(limit=50, db_path=db) if e.get("event_type") == "tool_execution"]


def test_an_executed_action_is_recorded(ledger):
    EX._record_tool_execution("WRITE_NOTE", {"text": "secret plan", "title": "t"}, {"ok": True, "response": "Wrote note"})
    (ev,) = _events(ledger)
    assert ev["action"] == "WRITE_NOTE" and ev["outcome"] == "ok"


def test_argument_values_are_never_stored(ledger):
    EX._record_tool_execution("WRITE_NOTE", {"text": "secret plan"}, {"ok": True})
    import json
    assert "secret plan" not in json.dumps(_events(ledger))
    assert _events(ledger)[0]["subject"] == "text"


def test_a_failure_is_recorded_as_failed(ledger):
    EX._record_tool_execution("OPEN_APP", {"app": "x"}, {"ok": False, "response": "not found"})
    assert _events(ledger)[0]["outcome"] == "failed"


def test_a_play_that_played_nothing_is_a_failure(ledger):
    # "opened the search but nothing played" was ok, so failed plays counted as successes
    EX._record_tool_execution("PLAY_MEDIA", {"query": "x"}, {"ok": True, "played": False, "search_only": True})
    assert _events(ledger)[0]["outcome"] == "failed"


def test_chat_and_the_off_switch_record_nothing(ledger, monkeypatch):
    EX._record_tool_execution("CHAT", {}, {"ok": True})
    monkeypatch.setenv("ELI_LEDGER_TOOL_EVENTS", "0")
    EX._record_tool_execution("TIME", {}, {"ok": True})
    assert _events(ledger) == []


def test_execute_itself_records_the_call(ledger):
    EX.execute("TIME", {})
    assert any(e["action"] == "TIME" for e in _events(ledger))


def _runs_of(db, action):
    return [e for e in led.recent_events(limit=50, db_path=db)
            if e.get("event_type") in ("tool_execution", "executor_action") and e.get("action") == action]


def test_a_run_answered_above_the_base_is_recorded_once_and_is_on_the_turn(ledger, monkeypatch):
    # the portable, voice, schema, media and middleware layers answer some actions themselves; whichever
    # layer answers, the run is checked, recorded and post-dispatched once, by the outermost execute
    from eli.kernel import request_context as rc
    monkeypatch.setattr(EX, "_ELI_EXECUTOR_CANONICAL_BASE",
                        lambda action, args=None, *a, **k: {"ok": True, "action": action, "response": "Tiled 2 windows."})
    facts = {}
    token = rc.turn_facts_var.set(facts)
    try:
        out = EX.execute("TILE_WINDOWS", {})
    finally:
        rc.turn_facts_var.reset(token)
    assert out["ok"] is True
    kinds = sorted(e["event_type"] for e in _runs_of(ledger, "TILE_WINDOWS"))
    assert kinds == ["executor_action", "tool_execution"]
    assert facts.get("executed_actions") == ["TILE_WINDOWS"]


def test_a_layer_handing_the_same_run_back_to_execute_is_still_one_run(ledger, monkeypatch):
    calls = []

    def layer(action, args=None, *a, **k):
        calls.append(action)
        if len(calls) == 1:
            return EX.execute(action, args)
        return {"ok": True, "action": action, "response": "Tiled."}

    monkeypatch.setattr(EX, "_ELI_EXECUTOR_CANONICAL_BASE", layer)
    EX.execute("TILE_WINDOWS", {"layout": "grid"})
    assert len(calls) == 2
    assert sorted(e["event_type"] for e in _runs_of(ledger, "TILE_WINDOWS")) == ["executor_action", "tool_execution"]


def test_a_run_through_the_base_is_recorded_once(ledger):
    EX.execute("TIME", {})
    assert sorted(e["event_type"] for e in _runs_of(ledger, "TIME")) == ["executor_action", "tool_execution"]


def test_what_each_run_said_is_on_the_turn(ledger, monkeypatch):
    """A claim about the state a run left ("shuffle is off now") is held to what the run said; the
    turn kept only the action names, so the check had nothing to read."""
    from eli.kernel import request_context as rc
    said = {"TILE_WINDOWS": {"ok": True, "action": "TILE_WINDOWS", "response": "Tiled 2 windows."},
            "PLAY_MEDIA": {"ok": True, "action": "PLAY_MEDIA", "played": False, "content": "Opened the search only."}}
    monkeypatch.setattr(EX, "_ELI_EXECUTOR_CANONICAL_BASE", lambda action, args=None, *a, **k: dict(said[action]))
    facts = {}
    token = rc.turn_facts_var.set(facts)
    try:
        EX.execute("TILE_WINDOWS", {})
        EX.execute("PLAY_MEDIA", {"query": "a song"})
    finally:
        rc.turn_facts_var.reset(token)
    assert facts["action_results"] == [
        {"action": "TILE_WINDOWS", "ok": True, "said": "Tiled 2 windows."},
        {"action": "PLAY_MEDIA", "ok": False, "said": "Opened the search only."}]
