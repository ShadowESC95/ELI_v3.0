"""Goal execution — what makes ``goal_driven`` actually drive goals.

Before this module, ``operator_policy`` offered four modes and the operator console let
you pick any of them, but the only thing in the codebase that branched on
``goal_driven`` was a log-severity label. Selecting "goal driven" changed a severity
string and nothing else: goals were proposed into a queue in every mode, and a human
approved them in every mode. A mode that advertises autonomy and does not deliver it is
the same class of defect as an action ELI claims but never performs.

The pipeline here closes that gap:

    goal ──► plan ──► classify ──► approve ──► execute ──► verify ──► record

* **plan** — the goal's objective is free text, so it is resolved to a real action
  through the same grammar-constrained resolver the router uses. The model therefore
  cannot invent a capability to pursue a goal with.
* **classify** — the resolved action is mapped to an ``approval_engine`` action class.
  An action nobody classified lands in a *manual*-approval tier, never an automatic one.
* **approve** — the existing ``approval_engine`` decides. This module does not get its
  own notion of what is safe; it feeds the gate that already exists.
* **execute / verify / record** — the outcome written back to the goal is what actually
  happened, including the failures.

Nothing here runs unless the operator has explicitly selected ``goal_driven``. Every
other mode keeps the previous propose-and-wait behaviour exactly.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

# Below this, the resolver is guessing rather than resolving, and a guess is not a
# mandate to act on the user's machine unattended.
_MIN_CONFIDENCE = 0.75

# Ceiling per tick, so a misbehaving goal cannot monopolise the machine between ticks.
_MAX_ACTIONS_PER_TICK = 3

# Classification is anchored, never a loose substring test. A first draft matched
# substrings and quietly auto-approved SET_TIMER (because "TIME" appears inside it) and
# RESTORE_WINDOWS (because "STORE_" does) — two actions that change real state. Matching
# is therefore prefix/suffix anchored, and write verbs are checked *before* read shapes
# so a mutating name can never be read as a query.

# Verb prefixes that change something. Checked first; longest match wins.
_WRITE_PREFIXES: List[tuple[str, str]] = [
    ("COMPUTER_USE", "shell-exec"),
    ("SHELL", "shell-exec"),
    ("RUN_", "shell-exec"),
    ("EXECUTE", "shell-exec"),
    ("KEYBOARD", "shell-exec"),
    ("MOUSE", "shell-exec"),
    ("GAZE_CLICK", "shell-exec"),
    ("SMART_HOME", "shell-exec"),
    ("INSTALL", "shell-exec"),
    ("UNINSTALL", "shell-exec"),
    ("MCP_CALL", "shell-exec"),
    ("RESTORE_", "shell-exec"),
    ("OPEN_", "shell-exec"),
    ("CLOSE_", "shell-exec"),
    ("PLAY_", "shell-exec"),
    ("SKIP_", "shell-exec"),
    ("SELF_IMPROVE", "self-modification"),
    ("SELF_UPGRADE", "self-modification"),
    ("SELF_PATCH", "self-modification"),
    ("SELF_MODIFY", "self-modification"),
    ("CODE_", "self-modification"),
    ("APPLY_", "self-modification"),
    ("UPGRADE", "self-modification"),
    ("PATCH", "self-modification"),
    ("DELETE_", "local-file-write"),
    ("REMOVE_", "local-file-write"),
    ("WRITE", "local-file-write"),
    ("SAVE", "local-file-write"),
    ("EXPORT", "local-file-write"),
    ("CREATE_", "local-file-write"),
    ("DOC_GENERATE", "local-file-write"),
    ("GENERATE_", "local-file-write"),
    ("WEB_", "external-network"),
    ("SEARCH", "external-network"),
    ("NEWS", "external-network"),
    ("GET_WEATHER", "external-network"),
    ("FETCH", "external-network"),
    ("SEND_", "external-network"),
    ("DOWNLOAD", "external-network"),
    ("REMEMBER", "memory-write"),
    ("FORGET", "memory-write"),
    ("STORE_", "memory-write"),
    ("SET_USER", "memory-write"),
]

# Read-only shapes, only consulted once every write prefix has been ruled out.
_READ_PREFIXES = ("LIST_", "EXPLAIN_", "SHOW_", "GET_STATUS", "SCREEN_READ")
_READ_SUFFIXES = ("_STATUS", "_REPORT", "_AUDIT", "_CAPABILITIES")
_READ_EXACT = {
    "DATE", "TIME", "GET_DATE", "GET_TIME", "SCREENSHOT", "MEMORY_RECALL",
    "USER_INFO_REPORT", "MESSAGE_TIME_QUERY", "RESOLVE_RUNTIME_PATHS",
    "MORNING_REPORT", "SELF_REPORT",
}

# The tier an unclassified action falls into. "shell-exec" is not a claim that the
# action runs a shell — it is the catch-all *manual approval* tier. An action nobody
# thought about must not be auto-approved merely because it was unrecognised.
_UNKNOWN_CLASS = "shell-exec"


def classify_action(action: str) -> str:
    """Map an ELI action to an approval_engine action class, conservatively.

    Write verbs are tested before read shapes, so a mutating action can never be
    mistaken for a query on the strength of a word buried inside its name.
    """
    name = (action or "").strip().upper()
    if not name:
        return _UNKNOWN_CLASS

    best: Optional[tuple[str, str]] = None
    for prefix, klass in _WRITE_PREFIXES:
        if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, klass)
    if best is not None:
        return best[1]

    if (name in _READ_EXACT
            or name.startswith(_READ_PREFIXES)
            or name.endswith(_READ_SUFFIXES)):
        return "observe-only"

    return _UNKNOWN_CLASS


def plan_goal(goal) -> Dict[str, Any]:
    """Resolve a goal's objective to a concrete action.

    Uses the grammar-constrained intent resolver, so the planned action is always a
    capability that exists — a goal cannot talk ELI into inventing one.
    """
    objective = str(getattr(goal, "objective", "") or getattr(goal, "title", "") or "").strip()
    if not objective:
        return {"ok": False, "reason": "goal has no objective to act on"}
    try:
        from eli.cognition.llm_intent import parse_with_llm
        parsed = parse_with_llm(objective) or {}
    except Exception as e:
        log.debug("goal_execution: planning failed", exc_info=True)
        return {"ok": False, "reason": f"could not plan this goal ({e})"}

    action = str(parsed.get("action") or "").strip().upper()
    if not action or action == "CHAT":
        # CHAT means the resolver found no capability that matches. Acting anyway would
        # mean inventing work; saying so is the honest outcome.
        return {"ok": False, "reason": "no concrete capability matches this objective",
                "action": action or "CHAT"}
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    return {"ok": True, "action": action, "args": parsed.get("args") or {},
            "confidence": confidence, "objective": objective}


def _approve(action: str, action_class: str, goal_id: str) -> Dict[str, Any]:
    """Ask the existing approval engine. This module never decides safety itself."""
    try:
        from eli.planning.proposal_models import ProposalRecord
        from eli.runtime.approval_engine import evaluate_record
        rec = ProposalRecord(
            kind="goal_execution",
            payload={"action": action, "goal_id": goal_id},
            source="autonomy_scheduler",
            action_class=action_class,
            emitter="proactive",
            requested_by="goal_driven",
        )
        rec = evaluate_record(rec)
        return {"state": rec.approval_state, "reason": rec.policy_reason,
                "requires_confirmation": bool(rec.requires_confirmation)}
    except Exception as e:
        log.debug("goal_execution: approval evaluation failed", exc_info=True)
        # A gate that cannot be consulted is a gate that refuses.
        return {"state": "blocked", "reason": f"approval engine unavailable ({e})",
                "requires_confirmation": True}


def execute_goal(goal, *, dry_run: bool = False) -> Dict[str, Any]:
    """Plan, gate and (if permitted) run one goal. Returns an honest trace."""
    goal_id = str(getattr(goal, "goal_id", "") or "")
    title = str(getattr(goal, "title", "") or goal_id)
    trace: Dict[str, Any] = {"goal_id": goal_id, "title": title, "at": time.time(),
                             "executed": False}

    plan = plan_goal(goal)
    trace["plan"] = plan
    if not plan.get("ok"):
        trace["outcome"] = "unplannable"
        trace["detail"] = plan.get("reason", "")
        return trace

    action = plan["action"]
    if plan["confidence"] < _MIN_CONFIDENCE:
        trace["outcome"] = "low_confidence"
        trace["detail"] = (f"resolved to {action} at {plan['confidence']:.2f}, "
                           f"below the {_MIN_CONFIDENCE} bar for acting unattended")
        return trace

    action_class = classify_action(action)
    trace["action_class"] = action_class
    verdict = _approve(action, action_class, goal_id)
    trace["approval"] = verdict
    if verdict["state"] != "approved":
        trace["outcome"] = "needs_approval" if verdict["state"] != "blocked" else "blocked"
        trace["detail"] = verdict["reason"] or f"{action_class} requires operator approval"
        return trace

    if dry_run:
        trace["outcome"] = "dry_run"
        trace["detail"] = f"would run {action}"
        return trace

    try:
        from eli.execution.executor_enhanced import execute
        result = execute(action, plan["args"]) or {}
    except Exception as e:
        log.debug("goal_execution: %s failed", action, exc_info=True)
        trace["outcome"] = "error"
        trace["detail"] = f"{action} raised: {e}"
        return trace

    ok = bool(result.get("ok", True))
    trace["executed"] = True
    trace["outcome"] = "done" if ok else "failed"
    trace["detail"] = str(result.get("content") or result.get("response")
                          or result.get("error") or "").strip()[:600]
    trace["action"] = action
    return trace


def run_due_goals(limit: int = _MAX_ACTIONS_PER_TICK, *, now: float | None = None,
                  dry_run: bool = False) -> Dict[str, Any]:
    """Execute up to ``limit`` due goals. Callers must have checked the policy mode."""
    limit = max(0, min(int(limit), _MAX_ACTIONS_PER_TICK))
    out: Dict[str, Any] = {"ok": True, "kind": "goal_execution", "count": 0, "items": []}
    if limit == 0:
        return out
    try:
        from eli.planning.goal_store import due_goals, mark_goal_tick
    except Exception as e:
        return {"ok": False, "kind": "goal_execution", "count": 0, "items": [],
                "error": f"goal store unavailable ({e})"}

    try:
        goals = due_goals(now=now, limit=limit) or []
    except Exception as e:
        return {"ok": False, "kind": "goal_execution", "count": 0, "items": [],
                "error": f"could not read due goals ({e})"}

    for goal in goals[:limit]:
        trace = execute_goal(goal, dry_run=dry_run)
        out["items"].append(trace)
        if trace.get("executed"):
            out["count"] += 1
            try:
                mark_goal_tick(trace["goal_id"], when=now)
            except Exception:
                log.debug("goal_execution: mark_goal_tick failed", exc_info=True)
    return out
