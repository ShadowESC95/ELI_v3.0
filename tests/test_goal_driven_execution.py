"""goal_driven must actually drive goals — and only within the existing gate.

Before this, `operator_policy` advertised four modes and the operator console let you
pick any of them, but the only branch on `goal_driven` in the whole codebase set a log
severity. These tests pin both halves of the fix: the mode now acts, and it cannot act
outside what `approval_engine` already permits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from eli.planning import goal_execution as ge


@dataclass
class _Goal:
    goal_id: str = "goal_test"
    title: str = "test goal"
    objective: str = "what is the date"
    risk: str = "low"
    tags: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Classification — the safety-critical part
# --------------------------------------------------------------------------

def test_unknown_actions_land_in_a_manual_tier():
    """An action nobody classified must never be auto-approved for being unrecognised."""
    from eli.runtime.approval_engine import AUTO_APPROVE
    assert ge.classify_action("SOME_BRAND_NEW_ACTION") not in AUTO_APPROVE
    assert ge.classify_action("") not in AUTO_APPROVE


def test_world_changing_actions_are_never_auto_approved():
    from eli.runtime.approval_engine import AUTO_APPROVE
    for action in ("COMPUTER_USE", "SHELL_EXEC", "RUN_CMD", "KEYBOARD", "MOUSE_CONTROL",
                   "SELF_UPGRADE", "CODE_PATCH", "DELETE_FILE", "MCP_CALL",
                   "SET_CLIPBOARD", "SMART_HOME_COMMAND"):
        assert ge.classify_action(action) not in AUTO_APPROVE, f"{action} must not auto-approve"


def test_reads_are_classified_as_observe_only():
    for action in ("DATE", "TIME", "BACKGROUND_JOBS_STATUS", "LIST_TASKS"):
        assert ge.classify_action(action) == "observe-only", action


def test_network_actions_are_classified_as_external_network():
    for action in ("WEB_SEARCH", "GET_WEATHER", "NEWS_BRIEFING"):
        assert ge.classify_action(action) == "external-network", action


def test_substring_lookalikes_are_not_mistaken_for_reads():
    """Regression: a first draft matched substrings and auto-approved SET_TIMER
    ("TIME" inside it) and RESTORE_WINDOWS ("STORE_" inside it). Both change real
    state; neither may ever be auto-approved for looking like a query."""
    from eli.runtime.approval_engine import AUTO_APPROVE
    for action in ("SET_TIMER", "RESTORE_WINDOWS", "SET_VOLUME", "SKIP_YOUTUBE_AD"):
        assert ge.classify_action(action) not in AUTO_APPROVE, action


def test_every_supported_action_gets_a_valid_class():
    """Sweep the real action surface: every action classifies, and nothing that looks
    world-changing lands in an auto-approving tier."""
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    from eli.runtime.approval_engine import AUTO_APPROVE, ACTION_CLASSES
    write_verbs = ("SHELL", "RUN_", "DELETE_", "REMOVE_", "INSTALL", "UPGRADE",
                   "SELF_IMPROVE", "SELF_UPGRADE", "KEYBOARD", "MOUSE", "COMPUTER_USE",
                   "OPEN_", "CLOSE_", "PLAY_", "RESTORE_", "SET_", "CREATE_", "WRITE",
                   "SEND_", "CODE_", "APPLY_", "PATCH", "SKIP_")
    for action in SUPPORTED_ACTIONS:
        klass = ge.classify_action(action)
        assert klass in ACTION_CLASSES, f"{action} -> unknown class {klass}"
        if klass in AUTO_APPROVE and action.upper().startswith(write_verbs):
            # SET_USER* is a deliberate memory-write, which the engine auto-approves.
            assert action.upper().startswith("SET_USER"), \
                f"{action} auto-approves ({klass}) but starts with a write verb"


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def test_a_goal_with_no_objective_is_not_actioned():
    out = ge.plan_goal(_Goal(objective="", title=""))
    assert out["ok"] is False


def test_an_objective_with_no_matching_capability_is_reported_not_invented(monkeypatch):
    monkeypatch.setattr("eli.cognition.llm_intent.parse_with_llm",
                        lambda *a, **k: {"action": "CHAT", "args": {}, "confidence": 0.9})
    out = ge.plan_goal(_Goal(objective="ponder the nature of being"))
    assert out["ok"] is False
    assert "no concrete capability" in out["reason"]


def test_planning_uses_the_grammar_constrained_resolver(monkeypatch):
    seen = {}
    def fake(text, *a, **k):
        seen["text"] = text
        return {"action": "DATE", "args": {}, "confidence": 0.95}
    monkeypatch.setattr("eli.cognition.llm_intent.parse_with_llm", fake)
    out = ge.plan_goal(_Goal(objective="tell me the date"))
    assert out["ok"] and out["action"] == "DATE"
    assert seen["text"] == "tell me the date"


# --------------------------------------------------------------------------
# Execution gating
# --------------------------------------------------------------------------

def _plan(monkeypatch, action, confidence=0.95):
    monkeypatch.setattr("eli.cognition.llm_intent.parse_with_llm",
                        lambda *a, **k: {"action": action, "args": {}, "confidence": confidence})


def test_low_confidence_never_acts(monkeypatch):
    _plan(monkeypatch, "DATE", confidence=0.4)
    ran = []
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda *a, **k: ran.append(a))
    trace = ge.execute_goal(_Goal())
    assert trace["outcome"] == "low_confidence"
    assert trace["executed"] is False and ran == []


def test_an_action_needing_approval_is_not_executed(monkeypatch):
    _plan(monkeypatch, "COMPUTER_USE")
    ran = []
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda *a, **k: ran.append(a))
    trace = ge.execute_goal(_Goal(objective="take over the screen"))
    assert trace["executed"] is False
    assert trace["outcome"] in ("needs_approval", "blocked")
    assert ran == [], "a gated action must never reach the executor"


def test_an_auto_approved_read_does_execute(monkeypatch):
    _plan(monkeypatch, "DATE")
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda a, args: {"ok": True, "content": "it is Tuesday"})
    trace = ge.execute_goal(_Goal())
    assert trace["executed"] is True and trace["outcome"] == "done"
    assert "Tuesday" in trace["detail"]


def test_a_failing_action_is_reported_as_failed_not_done(monkeypatch):
    _plan(monkeypatch, "DATE")
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda a, args: {"ok": False, "content": "clock unavailable"})
    trace = ge.execute_goal(_Goal())
    assert trace["outcome"] == "failed"
    assert "clock unavailable" in trace["detail"]


def test_a_raising_action_is_caught_and_reported(monkeypatch):
    _plan(monkeypatch, "DATE")
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr("eli.execution.executor_enhanced.execute", boom)
    trace = ge.execute_goal(_Goal())
    assert trace["outcome"] == "error" and "kaboom" in trace["detail"]


def test_dry_run_never_executes(monkeypatch):
    _plan(monkeypatch, "DATE")
    ran = []
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda *a, **k: ran.append(a))
    trace = ge.execute_goal(_Goal(), dry_run=True)
    assert trace["outcome"] == "dry_run" and ran == []


def test_an_unavailable_approval_engine_refuses(monkeypatch):
    """A gate that cannot be consulted must refuse, not wave things through."""
    _plan(monkeypatch, "DATE")
    monkeypatch.setattr("eli.runtime.approval_engine.evaluate_record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate down")))
    ran = []
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda *a, **k: ran.append(a))
    trace = ge.execute_goal(_Goal())
    assert trace["executed"] is False and ran == []


def test_run_due_goals_is_bounded(monkeypatch):
    _plan(monkeypatch, "DATE")
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda a, args: {"ok": True, "content": "x"})
    monkeypatch.setattr("eli.planning.goal_store.due_goals",
                        lambda now=None, limit=5: [_Goal(goal_id=f"g{i}") for i in range(20)])
    monkeypatch.setattr("eli.planning.goal_store.mark_goal_tick", lambda *a, **k: True)
    out = ge.run_due_goals(limit=99)
    assert out["count"] <= ge._MAX_ACTIONS_PER_TICK


# --------------------------------------------------------------------------
# The mode itself
# --------------------------------------------------------------------------

def test_non_goal_driven_modes_do_not_execute(monkeypatch, tmp_path):
    """proposal_only stays propose-and-wait — the whole point of the default."""
    from eli.planning import autonomy_scheduler as sched
    monkeypatch.setattr(sched, "load_policy", lambda: {"mode": "proposal_only"})
    monkeypatch.setattr(sched, "load_scheduler_state", lambda: {})
    monkeypatch.setattr(sched, "save_scheduler_state", lambda s: s)
    monkeypatch.setattr(sched, "append_attention", lambda **k: None)
    monkeypatch.setattr(sched, "safe_proposal_summary", lambda: {})
    monkeypatch.setattr(sched, "safe_goal_summary", lambda: {})
    called = []
    monkeypatch.setattr("eli.planning.goal_execution.run_due_goals",
                        lambda **k: called.append(k) or {"count": 0, "items": []})
    out = sched.scheduler_tick(cooldown_sec=0)
    assert "execution" not in out
    assert called == [], "proposal_only must never execute"


def test_goal_driven_mode_executes(monkeypatch):
    from eli.planning import autonomy_scheduler as sched
    monkeypatch.setattr(sched, "load_policy", lambda: {"mode": "goal_driven"})
    monkeypatch.setattr(sched, "load_scheduler_state", lambda: {})
    monkeypatch.setattr(sched, "save_scheduler_state", lambda s: s)
    monkeypatch.setattr(sched, "append_attention", lambda **k: None)
    monkeypatch.setattr(sched, "safe_proposal_summary", lambda: {})
    monkeypatch.setattr(sched, "safe_goal_summary", lambda: {})
    called = []
    monkeypatch.setattr("eli.planning.goal_execution.run_due_goals",
                        lambda **k: (called.append(k), {"count": 1, "items": []})[1])
    out = sched.scheduler_tick(cooldown_sec=0)
    assert out.get("execution") is not None
    assert called, "goal_driven must actually run goals"


def test_observe_only_still_short_circuits(monkeypatch):
    from eli.planning import autonomy_scheduler as sched
    monkeypatch.setattr(sched, "load_policy", lambda: {"mode": "observe_only"})
    monkeypatch.setattr(sched, "load_scheduler_state", lambda: {})
    monkeypatch.setattr(sched, "save_scheduler_state", lambda s: s)
    out = sched.scheduler_tick(cooldown_sec=0)
    assert out["status"] == "observe_only" and "execution" not in out


def test_the_daemons_ticks_pass_on_what_ran_and_name_what_failed(monkeypatch):
    """The proactive daemon and the operator console call these on every tick: a tick that raised
    would end the daemon's loop. They were only checked to exist."""
    from eli.planning import autonomy_controller as ac
    from eli.planning import autonomy_scheduler, goal_tick, proposal_memory_bridge, proposal_queue
    from eli.runtime import code_monitor, self_model_refresh

    def _fails(*a, **k):
        raise RuntimeError("the goal store is locked")

    monkeypatch.setattr(goal_tick, "governed_goal_tick", lambda limit=3: {"ok": True, "ran": limit})
    monkeypatch.setattr(autonomy_scheduler, "scheduler_tick",
                        lambda limit=3, cooldown_sec=60: {"ok": True, "limit": limit, "cooldown": cooldown_sec})
    assert ac.safe_goal_tick(limit=2) == {"ok": True, "ran": 2}
    assert ac.safe_scheduler_tick(limit=4, cooldown_sec=9) == {"ok": True, "limit": 4, "cooldown": 9}
    monkeypatch.setattr(goal_tick, "governed_goal_tick", _fails)
    monkeypatch.setattr(autonomy_scheduler, "scheduler_tick", _fails)
    for out, kind in ((ac.safe_goal_tick(), "safe_goal_tick"), (ac.safe_scheduler_tick(), "safe_scheduler_tick")):
        assert out == {"ok": False, "kind": kind, "error": "the goal store is locked"}

    # one step of the maintenance tick failing is named, and the others still run
    monkeypatch.setattr(code_monitor.CodeMonitor, "check", _fails)
    monkeypatch.setattr(self_model_refresh, "refresh_all_overlays_nonfatal", lambda reason="": {"ok": True})
    monkeypatch.setattr(proposal_queue, "summarize_by_state", lambda: {"pending": 1})
    monkeypatch.setattr(proposal_memory_bridge, "drain_proposals_to_agent_memory",
                        lambda max_items=64, archive=True: {"drained": 0})
    out = ac.safe_tick(reason="a test")
    assert out["ok"] is False and out["errors"] == ["code_monitor:the goal store is locked"]
    assert out["overlay_refresh"] == {"ok": True} and out["proposal_policy_summary"] == {"pending": 1}
    assert out["proposal_drain"] == {"drained": 0} and out["reason"] == "a test"
