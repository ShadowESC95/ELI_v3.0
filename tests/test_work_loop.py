"""Sustained work across sessions — and knowing when to stop.

goal_execution runs a goal once; that is a tick, not work. This loop accumulates
progress in the goal's own durable metadata so a multi-step goal actually finishes and
survives a restart. Stopping matters as much as starting: these tests pin the attempt
budget, the no-progress detector, and the rule that completion is judged on what
actually happened rather than on having tried.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from eli.planning import work_loop as wl


@dataclass
class _Goal:
    goal_id: str = "g1"
    title: str = "tidy the desk"
    objective: str = "list my notes"
    priority: float = 0.9
    success_criteria: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    """Persist into the object itself, so tests exercise the record without a DB."""
    saved = {}
    def fake_upsert(goal):
        saved["goal"] = goal
        return goal
    monkeypatch.setattr("eli.planning.goal_store.upsert_goal", fake_upsert)
    return saved


def _step(monkeypatch, outcome, action="LIST_NOTES", detail="did the thing"):
    monkeypatch.setattr("eli.planning.goal_execution.execute_goal",
                        lambda g, **k: {"outcome": outcome, "action": action,
                                        "detail": detail,
                                        "executed": outcome == "done"})
    monkeypatch.setattr("eli.planning.goal_execution.plan_goal",
                        lambda g: {"ok": True, "action": action, "args": {},
                                   "confidence": 0.95, "objective": g.objective})


def test_a_successful_step_with_no_criteria_completes_the_goal(monkeypatch):
    _step(monkeypatch, "done")
    g = _Goal()
    out = wl.advance_goal(g)
    assert out["outcome"] == "done" and out["status"] == "done"
    assert g.metadata["work"]["status"] == "done"


def test_progress_persists_on_the_goal_so_it_survives_a_restart(monkeypatch):
    _step(monkeypatch, "failed")
    g = _Goal()
    wl.advance_goal(g)
    assert g.metadata["work"]["attempts"] == 1
    # a fresh object carrying the same metadata resumes where it left off
    g2 = _Goal(metadata=dict(g.metadata))
    wl.advance_goal(g2)
    assert g2.metadata["work"]["attempts"] == 2, "attempts must accumulate across objects"


def test_the_attempt_budget_is_enforced(monkeypatch):
    _step(monkeypatch, "failed")
    g = _Goal(metadata={"work": {"attempts": wl._MAX_ATTEMPTS, "steps": [],
                                 "status": "active", "no_progress": 0}})
    out = wl.advance_goal(g)
    assert out["status"] == "stopped"
    assert "budget" in out["detail"]


def test_consecutive_nothing_steps_stop_the_goal(monkeypatch):
    _step(monkeypatch, "failed")
    g = _Goal()
    for _ in range(wl._NO_PROGRESS_LIMIT):
        wl.advance_goal(g)
    assert g.metadata["work"]["status"] == "stopped"
    assert "achieved nothing" in g.metadata["work"]["stop_reason"]


def test_a_finished_goal_is_not_worked_again(monkeypatch):
    _step(monkeypatch, "done")
    ran = []
    monkeypatch.setattr("eli.planning.goal_execution.execute_goal",
                        lambda g, **k: ran.append(g) or {"outcome": "done"})
    g = _Goal(metadata={"work": {"status": "done", "attempts": 1, "steps": [],
                                 "no_progress": 0}})
    out = wl.advance_goal(g)
    assert out["outcome"] == "done" and ran == []


def test_criteria_are_judged_on_what_actually_happened(monkeypatch):
    """A goal with criteria is not complete merely because a step succeeded."""
    _step(monkeypatch, "done")
    monkeypatch.setattr("eli.cognition.gguf_inference.chat_completion",
                        lambda *a, **k: "NO — the notes were listed but not tidied")
    g = _Goal(success_criteria=["the desk is tidy"])
    out = wl.advance_goal(g)
    assert out["status"] == "active", "must keep working until the criteria are met"


def test_criteria_met_completes_the_goal(monkeypatch):
    _step(monkeypatch, "done")
    monkeypatch.setattr("eli.cognition.gguf_inference.chat_completion",
                        lambda *a, **k: "YES — everything asked for is present")
    g = _Goal(success_criteria=["the notes are listed"])
    out = wl.advance_goal(g)
    assert out["status"] == "done"
    assert "YES" in out.get("completion_reason", "")


def test_an_unavailable_judge_does_not_declare_success(monkeypatch):
    _step(monkeypatch, "done")
    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr("eli.cognition.gguf_inference.chat_completion", boom)
    g = _Goal(success_criteria=["something specific"])
    assert wl.advance_goal(g)["status"] == "active"


def test_the_next_step_is_informed_by_what_was_already_tried(monkeypatch):
    seen = {}
    def fake_plan(goal):
        seen["objective"] = goal.objective
        return {"ok": True, "action": "LIST_NOTES", "args": {}, "confidence": 0.9,
                "objective": goal.objective}
    monkeypatch.setattr("eli.planning.goal_execution.plan_goal", fake_plan)
    monkeypatch.setattr("eli.planning.goal_execution.execute_goal",
                        lambda g, **k: {"outcome": "failed", "action": "LIST_NOTES",
                                        "detail": "no notes"})
    g = _Goal(metadata={"work": {"attempts": 1, "no_progress": 1, "status": "active",
                                 "steps": [{"action": "LIST_NOTES", "outcome": "failed"}]}})
    wl.advance_goal(g)
    assert "already tried" in seen["objective"]


def test_unplannable_goals_do_not_spin_forever(monkeypatch):
    monkeypatch.setattr("eli.planning.goal_execution.plan_goal",
                        lambda g: {"ok": False, "reason": "no capability matches"})
    g = _Goal()
    for _ in range(wl._NO_PROGRESS_LIMIT):
        out = wl.advance_goal(g)
    assert out["status"] == "stopped"


def test_dry_run_is_passed_through(monkeypatch):
    seen = {}
    monkeypatch.setattr("eli.planning.goal_execution.plan_goal",
                        lambda g: {"ok": True, "action": "DATE", "args": {},
                                   "confidence": 0.9, "objective": "x"})
    monkeypatch.setattr("eli.planning.goal_execution.execute_goal",
                        lambda g, **k: seen.update(k) or {"outcome": "dry_run"})
    wl.advance_goal(_Goal(), dry_run=True)
    assert seen.get("dry_run") is True


def test_work_tick_is_bounded_and_prioritised(monkeypatch):
    _step(monkeypatch, "done")
    goals = [_Goal(goal_id=f"g{i}", priority=i / 10.0) for i in range(6)]
    monkeypatch.setattr("eli.planning.goal_store.list_active_goals", lambda: goals)
    out = wl.work_tick(limit=99)
    assert len(out["items"]) <= 3
    assert out["items"][0]["goal_id"] == "g5", "highest priority first"


def test_work_tick_skips_finished_goals(monkeypatch):
    _step(monkeypatch, "done")
    finished = _Goal(goal_id="fin", metadata={"work": {"status": "done", "attempts": 1,
                                                       "steps": [], "no_progress": 0}})
    active = _Goal(goal_id="act")
    monkeypatch.setattr("eli.planning.goal_store.list_active_goals",
                        lambda: [finished, active])
    out = wl.work_tick(limit=3)
    assert [i["goal_id"] for i in out["items"]] == ["act"]


def test_progress_report_reflects_the_persisted_record(monkeypatch):
    monkeypatch.setattr("eli.planning.goal_store.load_goals", lambda: [
        _Goal(goal_id="a", metadata={"work": {"status": "done", "attempts": 2,
                                              "steps": [{"outcome": "done"}],
                                              "no_progress": 0}}),
        _Goal(goal_id="b", metadata={"work": {"status": "stopped", "attempts": 8,
                                              "steps": [], "no_progress": 3,
                                              "stop_reason": "gave up"}}),
        _Goal(goal_id="c"),
    ])
    rep = wl.progress_report()
    assert rep["done"] == 1 and rep["stopped"] == 1 and rep["active"] == 1
    assert any(r["stop_reason"] == "gave up" for r in rep["goals"])


def test_an_unavailable_goal_store_is_reported_not_crashed(monkeypatch):
    def boom():
        raise RuntimeError("store gone")
    monkeypatch.setattr("eli.planning.goal_store.list_active_goals", boom)
    out = wl.work_tick()
    assert out["ok"] is False and "store gone" in out["error"]
