from __future__ import annotations

import inspect
import time
from typing import Any, Dict, List

from eli.planning.goal_store import due_goals, mark_goal_tick


def _goal_to_proposal(goal) -> Dict[str, Any]:
    return {
        "title": f"Goal tick: {goal.title}",
        "summary": goal.objective or goal.title,
        "rationale": (
            "Autonomy mission-layer due tick. "
            f"goal_id={goal.goal_id} priority={goal.priority} cadence_sec={goal.cadence_sec}"
        ),
        "risk": goal.risk,
        "source": "goal_mission_layer",
        "status": "pending_approval",
        "kind": "mission_goal",
        "metadata": {
            "goal_id": goal.goal_id,
            "goal_title": goal.title,
            "goal_tags": list(goal.tags),
            "constraints": list(goal.constraints),
            "success_criteria": list(goal.success_criteria),
            "autonomy_mode": goal.autonomy_mode,
        },
        "requested_action": {
            "type": "goal_tick",
            "goal_id": goal.goal_id,
            "title": goal.title,
            "objective": goal.objective,
        },
    }


EMITTER = "proactive"           # the same emitter goal_execution's gate uses for a goal's action


def _action_class(goal) -> str:
    """What working on this goal would do, for the approval engine: the class of the action its work
    last planned, or, when it has planned none, goal_execution's conservative class for an action
    nobody has looked at (manual approval). A goal tick was filed as observe-only from an unknown
    emitter, so the engine approved every one by itself, whatever the goal was."""
    from eli.planning.goal_execution import classify_action
    steps = ((getattr(goal, "metadata", None) or {}).get("work") or {}).get("steps") or []
    planned = next((str(s.get("action") or "") for s in reversed(steps) if isinstance(s, dict) and s.get("action")), "")
    return classify_action(planned)


def _awaiting(goal_id: str) -> bool:
    """Whether a proposal for this goal already waits for the user: one question per goal."""
    try:
        from eli.planning import proposal_queue as pq
        return any(r.approval_state == pq.AWAITING_THE_USER and (r.payload.get("metadata") or {}).get("goal_id") == goal_id
                   for r in pq.read_records())
    except Exception:
        return False


def _emit_governed_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.planning import proposal_queue as pq
    except Exception as exc:
        return {"ok": False, "error": f"proposal_queue import failed: {exc}", "proposal": proposal}

    fn = getattr(pq, "append_governed_proposal", None)
    if fn is None:
        return {"ok": False, "error": "append_governed_proposal missing", "proposal": proposal}

    try:
        sig = inspect.signature(fn)
        kwargs = {k: v for k, v in proposal.items() if k in sig.parameters}
        # the queue takes the proposal itself as its payload: without it every goal tick failed
        # ("missing 1 required positional argument: 'payload'") and the mission layer never ran
        if "payload" in sig.parameters and "payload" not in kwargs:
            kwargs["payload"] = dict(proposal)
        if kwargs:
            rec = fn(**kwargs)
        else:
            try:
                rec = fn(proposal)
            except TypeError:
                rec = fn()
        # plain data: the scheduler files each tick's items as attention metadata (JSON), and a
        # ProposalRecord object there made every scheduler tick raise from 3 Nov on
        return {"ok": True, "record": rec.to_dict() if hasattr(rec, "to_dict") else rec, "proposal": proposal}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "proposal": proposal}


def governed_goal_tick(limit: int = 3, now: float | None = None) -> Dict[str, Any]:
    now = time.time() if now is None else float(now)
    goals = due_goals(now=now, limit=limit)
    emitted: List[Dict[str, Any]] = []
    for goal in goals:
        if _awaiting(goal.goal_id):
            emitted.append({"goal_id": goal.goal_id, "title": goal.title,
                            "emit": {"ok": True, "skipped": "a proposal for it already waits for the user"}})
            mark_goal_tick(goal.goal_id, when=now)
            continue
        proposal = _goal_to_proposal(goal)
        proposal["action_class"] = _action_class(goal)
        proposal["emitter"] = EMITTER
        res = _emit_governed_proposal(proposal)
        emitted.append({"goal_id": goal.goal_id, "title": goal.title, "emit": res})
        mark_goal_tick(goal.goal_id, when=now)
    return {
        "ok": True,
        "kind": "governed_goal_tick",
        "count": len(emitted),
        "items": emitted,
    }


def pending_goal_proposals() -> List[Dict[str, Any]]:
    """The goal proposals waiting for the user's yes: {"proposal_id", "goal_id", "title", "objective",
    "action_class", "why"}, oldest first. For the surface that asks the user (GET_PROPOSALS)."""
    from eli.planning import proposal_queue as pq
    out = []
    for rec in pq.read_records():
        if rec.approval_state != pq.AWAITING_THE_USER or rec.kind != "mission_goal":
            continue
        meta = rec.payload.get("metadata") or {}
        asked = rec.payload.get("requested_action") or {}
        out.append({"proposal_id": rec.proposal_id, "goal_id": meta.get("goal_id"),
                    "title": asked.get("title") or meta.get("goal_title") or "",
                    "objective": asked.get("objective") or "", "action_class": rec.action_class,
                    "why": rec.policy_reason})
    return out


def confirm_goal_proposal(proposal_id: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """The user said yes to a goal's proposal: it leaves the queue and the goal is worked on once,
    through goal_execution with the yes given (the approval engine still decides what it allows).
    Returns goal_execution's trace, or why nothing ran."""
    from eli.planning import proposal_queue as pq
    from eli.planning.goal_execution import execute_goal
    from eli.planning.goal_store import load_goals
    rec = next((r for r in pq.read_records() if r.proposal_id == proposal_id), None)
    if rec is None or rec.approval_state != pq.AWAITING_THE_USER:
        return {"ok": False, "outcome": "not_found", "detail": "no goal proposal by that id is waiting"}
    goal_id = (rec.payload.get("metadata") or {}).get("goal_id")
    goal = next((g for g in load_goals() if g.goal_id == goal_id), None)
    if goal is None:
        pq.take_record(proposal_id)
        return {"ok": False, "outcome": "not_found", "detail": "the goal it was for is no longer kept"}
    trace = execute_goal(goal, dry_run=dry_run, user_confirmed=True)
    pq.take_record(proposal_id)
    trace["ok"] = trace.get("outcome") in ("done", "dry_run")
    return trace
