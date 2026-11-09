"""The mission layer's goal tick (eli/planning/goal_tick.py) hands each due goal to the governed
proposal queue. The queue takes the proposal as its payload; without it every tick failed with
"missing 1 required positional argument: 'payload'" and the proactive layer never ran (seen in the
attention queue of a live session, 2026-11-03)."""
from __future__ import annotations

from eli.planning import goal_tick


def test_a_goal_tick_is_handed_to_the_queue_with_the_proposal_as_its_payload(monkeypatch):
    from eli.planning import proposal_queue
    taken = {}

    def queue(kind, payload, source="unknown", priority=50, **rest):
        taken.update(kind=kind, payload=payload, source=source, rest=rest)
        return {"id": "p1", "kind": kind}

    monkeypatch.setattr(proposal_queue, "append_governed_proposal", queue)
    proposal = {"title": "Goal tick: tidy", "summary": "tidy up", "kind": "mission_goal", "source": "goal_mission_layer",
                "metadata": {"goal_id": "g1"}, "requested_action": {"type": "goal_tick", "goal_id": "g1"}}
    out = goal_tick._emit_governed_proposal(proposal)
    assert out["ok"], out
    assert taken["kind"] == "mission_goal" and taken["source"] == "goal_mission_layer"
    assert taken["payload"]["requested_action"]["goal_id"] == "g1" and taken["payload"]["title"] == "Goal tick: tidy"
    assert "metadata" not in taken["rest"], "only what the queue takes is passed by name"


# ── a goal's proposal asks the user; nobody approves it by itself ────────────

def _queue_and_goals(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_PROPOSAL_QUEUE_PATH", str(tmp_path / "queue.jsonl"))
    monkeypatch.setenv("ELI_PROPOSAL_ARCHIVE_PATH", str(tmp_path / "archive.jsonl"))
    from eli.planning import goal_store
    monkeypatch.setattr(goal_store, "goal_store_path", lambda: tmp_path / "goals.json")
    monkeypatch.setattr("eli.core.full_control.is_full_control", lambda: False)
    goal_store.upsert_goal({"goal_id": "g1", "title": "Tidy the notes folder", "objective": "move old notes",
                            "cadence_sec": 60})
    return goal_store


def test_a_goal_tick_waits_for_the_user_and_is_asked_once(tmp_path, monkeypatch):
    """Every goal tick was filed observe-only from an unknown emitter and approved by 'policy:auto'
    whatever the goal was, then drained into observations nobody was asked about."""
    from eli.planning import proposal_queue
    from eli.planning.proposal_memory_bridge import drain_proposals_to_agent_memory
    goal_store = _queue_and_goals(tmp_path, monkeypatch)
    goal_tick.governed_goal_tick(now=10_000.0)
    (rec,) = proposal_queue.read_records()
    assert rec.approval_state == "pending_confirmation" and rec.approved_by is None, rec
    assert rec.emitter == goal_tick.EMITTER
    goal_tick.governed_goal_tick(now=20_000.0)                    # due again: still one question
    assert len(proposal_queue.read_records()) == 1
    drain_proposals_to_agent_memory(memory=type("M", (), {"add_observation": lambda *a, **k: True})(), archive=True)
    assert [r.proposal_id for r in proposal_queue.read_records()] == [rec.proposal_id], "a question was drained away"
    assert goal_tick.pending_goal_proposals()[0]["goal_id"] == "g1"


def test_a_goal_whose_work_planned_a_read_is_classed_by_that_action(tmp_path, monkeypatch):
    goal_store = _queue_and_goals(tmp_path, monkeypatch)
    goal_store.update_goal("g1", lambda g: g.metadata.update(work={"steps": [{"action": "LIST_NOTES"}]}))
    goal = goal_store.load_goals()[0]
    from eli.planning.goal_execution import classify_action
    assert goal_tick._action_class(goal) == classify_action("LIST_NOTES")


def test_a_yes_runs_the_goal_once_through_the_gate_with_the_yes_given(tmp_path, monkeypatch):
    from eli.planning import goal_execution, proposal_queue
    _queue_and_goals(tmp_path, monkeypatch)
    goal_tick.governed_goal_tick(now=10_000.0)
    (rec,) = proposal_queue.read_records()
    ran = []
    monkeypatch.setattr(goal_execution, "execute_goal",
                        lambda goal, **kw: ran.append((goal.goal_id, kw)) or {"outcome": "done", "executed": True})
    out = goal_tick.confirm_goal_proposal(rec.proposal_id)
    assert out["ok"] and ran == [("g1", {"dry_run": False, "user_confirmed": True})]
    assert proposal_queue.read_records() == []
    assert goal_tick.confirm_goal_proposal(rec.proposal_id)["outcome"] == "not_found", "a yes runs it once"


def test_a_scheduler_tick_that_proposes_a_goal_completes(tmp_path, monkeypatch):
    """Each goal tick's items carried the queue's ProposalRecord object, the scheduler filed them as
    attention metadata in JSON, and every scheduler tick raised from 3 Nov on: its state file was
    never written again."""
    import json
    _queue_and_goals(tmp_path, monkeypatch)
    from eli.planning import attention_queue, autonomy_scheduler
    monkeypatch.setattr(autonomy_scheduler, "scheduler_state_path", lambda: tmp_path / "autonomy_scheduler.json")
    monkeypatch.setattr(attention_queue, "attention_path", lambda: tmp_path / "attention.jsonl")
    monkeypatch.setattr(attention_queue, "suppression_path", lambda: tmp_path / "suppression.json")
    monkeypatch.setattr(autonomy_scheduler, "load_policy", lambda: {"mode": "proposal_only"})
    out = autonomy_scheduler.scheduler_tick(limit=3, now=10_000.0, cooldown_sec=0)
    assert out["ok"] and out["goal_tick"]["count"] == 1
    assert json.loads((tmp_path / "autonomy_scheduler.json").read_text())["last_run"] == 10_000.0
    json.dumps(out["goal_tick"])                                    # plain data all the way down


def test_a_tick_that_keeps_failing_says_why_and_asks_for_attention(monkeypatch, caplog):
    from eli.planning import attention_queue
    from eli.planning.proactive_daemon import ProactiveDaemon
    filed = []
    monkeypatch.setattr(attention_queue, "append_attention", lambda **kw: filed.append(kw) or kw)
    daemon = ProactiveDaemon.__new__(ProactiveDaemon)
    failing = {"ok": False, "error": "TypeError: Object of type ProposalRecord is not JSON serializable"}
    with caplog.at_level("WARNING"):
        daemon._note_tick_failures({"scheduler tick": failing, "goal tick": {"ok": True}})
    assert "ProposalRecord is not JSON serializable" in caplog.text
    assert filed == [], "one failure is a log line"
    daemon._note_tick_failures({"scheduler tick": failing, "goal tick": {"ok": True}})
    assert len(filed) == 1 and filed[0]["kind"] == "autonomy_tick_failing"
    daemon._note_tick_failures({"scheduler tick": {"ok": True}, "goal tick": {"ok": True}})
    daemon._note_tick_failures({"scheduler tick": failing, "goal tick": {"ok": True}})
    assert len(filed) == 1, "a failure after a good tick starts over"


def test_a_goal_waiting_for_the_users_yes_is_listed_when_asked_for_proposals(tmp_path, monkeypatch):
    """A goal's tick asks the user, but nothing showed the question: GET_PROPOSALS ("any proposals
    for me?") listed the goals and capability proposals and never what waited for a yes."""
    from eli.execution import executor_enhanced as ex
    from eli.planning import goal_store
    _queue_and_goals(tmp_path, monkeypatch)
    goal_store.update_goal("g1", lambda g: setattr(g, "autonomy_mode", "proposal_only"))
    goal_tick.governed_goal_tick(now=10_000.0)
    out = ex.execute("GET_PROPOSALS", {})
    assert out["ok"], out
    (waiting,) = out["waiting_goal_proposals"]
    assert waiting["goal_id"] == "g1" and waiting["proposal_id"]
    text = out["content"]
    assert "[waiting for your yes] Tidy the notes folder — move old notes" in text, text
    assert text.count("Tidy the notes folder") == 1, "a waiting goal is listed once"
