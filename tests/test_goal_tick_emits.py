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
