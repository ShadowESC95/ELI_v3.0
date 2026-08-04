"""Work loop — sustained progress on a goal across sessions.

``goal_execution`` runs a due goal *once*. That is a tick, not work: nothing accumulates,
so a goal needing four steps never finishes, and restarting ELI loses whatever it had
figured out. The only genuinely closed loop in the system was ``self_improvement``, which
patches, runs ``pytest``, and reverts on failure. This applies that shape — act, verify,
keep or stop — to arbitrary goals.

Progress lives in the goal's own ``metadata["work"]`` and is written through
``goal_store``, which is already durable. No new store, no parallel source of truth: a
goal and its history restart together or not at all.

    pick ──► plan next step (informed by what already happened)
              ──► execute (gated exactly as goal_execution gates)
              ──► record ──► judge done / continue / stop

Stopping is as important as starting. A goal stops when its success criteria are met,
when it exhausts its attempt budget, or when consecutive steps stop achieving anything —
the same no-progress reasoning the computer-use loop uses, for the same reason: an agent
that cannot tell it is stuck will happily burn a machine's evening on nothing.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_MAX_ATTEMPTS = 8          # per goal, across all sessions
_NO_PROGRESS_LIMIT = 3     # consecutive steps that achieve nothing
_MAX_HISTORY = 20          # keep the goal record bounded


def _work_state(goal) -> Dict[str, Any]:
    meta = dict(getattr(goal, "metadata", {}) or {})
    work = dict(meta.get("work") or {})
    work.setdefault("attempts", 0)
    work.setdefault("steps", [])
    work.setdefault("status", "active")
    work.setdefault("no_progress", 0)
    return work


def _persist(goal, work: Dict[str, Any]) -> bool:
    """Write the work record back through goal_store so it survives a restart."""
    work["steps"] = list(work.get("steps", []))[-_MAX_HISTORY:]
    work["updated_at"] = time.time()
    try:
        from eli.planning.goal_store import upsert_goal
        meta = dict(getattr(goal, "metadata", {}) or {})
        meta["work"] = work
        try:
            goal.metadata = meta
        except Exception:
            pass  # frozen spec — the dict below still carries it
        upsert_goal(goal)
        return True
    except Exception:
        log.debug("work_loop: could not persist progress for %s",
                  getattr(goal, "goal_id", "?"), exc_info=True)
        return False


def _criteria_met(goal, work: Dict[str, Any]) -> tuple[bool, str]:
    """Has this goal achieved what it set out to do?

    With explicit success criteria the judgement is delegated to the model, shown only
    what actually happened. Without criteria, one step that genuinely succeeded is
    treated as completion — inventing further work would be inventing the goal.
    """
    criteria = [c for c in (getattr(goal, "success_criteria", None) or []) if str(c).strip()]
    successes = [s for s in work.get("steps", []) if s.get("outcome") == "done"]
    if not successes:
        return False, ""
    if not criteria:
        return True, "objective achieved in one step (no explicit success criteria)"

    transcript = "\n".join(
        f"- {s.get('action', '?')}: {s.get('outcome')} — {str(s.get('detail', ''))[:200]}"
        for s in work.get("steps", [])[-8:]
    )
    prompt = (
        f"GOAL: {getattr(goal, 'title', '')}\n"
        f"OBJECTIVE: {getattr(goal, 'objective', '')}\n"
        f"SUCCESS CRITERIA:\n" + "\n".join(f"- {c}" for c in criteria) + "\n\n"
        f"WHAT HAS ACTUALLY HAPPENED:\n{transcript}\n\n"
        "Are the success criteria met by what actually happened? "
        "Answer YES or NO, then one short sentence of justification."
    )
    try:
        from eli.cognition import gguf_inference
        reply = str(gguf_inference.chat_completion(
            prompt,
            system=("You judge whether a goal is complete based ONLY on what actually "
                    "happened. Do not assume anything succeeded that is not listed."),
            max_tokens=120, temperature=0.0) or "")
    except Exception:
        log.debug("work_loop: completion judge unavailable", exc_info=True)
        return False, ""
    verdict = reply.strip().upper()
    if verdict.startswith("YES"):
        return True, reply.strip()[:300]
    return False, ""


def _plan_next(goal, work: Dict[str, Any]) -> Dict[str, Any]:
    """The next step, informed by what has already been tried.

    Reuses ``goal_execution.plan_goal`` on a restated objective so the planner still
    goes through the grammar-constrained resolver — a long-running goal gets no extra
    licence to invent capabilities.
    """
    from eli.planning.goal_execution import plan_goal

    steps = work.get("steps", [])
    if not steps:
        return plan_goal(goal)

    tried = "; ".join(f"{s.get('action')} -> {s.get('outcome')}" for s in steps[-5:])
    base = str(getattr(goal, "objective", "") or getattr(goal, "title", "") or "")
    # Built outside the class body on purpose: assigning `objective` inside it would
    # shadow the enclosing name and the RHS would resolve to the unbound class-local.
    restated_objective = f"{base} (already tried: {tried})"

    class _Restated:
        goal_id = getattr(goal, "goal_id", "")
        title = getattr(goal, "title", "")
        objective = restated_objective

    return plan_goal(_Restated())


def advance_goal(goal, *, dry_run: bool = False) -> Dict[str, Any]:
    """Take one bounded step on a goal and record what happened."""
    from eli.planning.goal_execution import execute_goal

    goal_id = str(getattr(goal, "goal_id", "") or "")
    work = _work_state(goal)
    out: Dict[str, Any] = {"goal_id": goal_id, "title": getattr(goal, "title", ""),
                           "attempts": work["attempts"], "status": work["status"]}

    if work["status"] in ("done", "stopped"):
        out["outcome"] = work["status"]
        out["detail"] = "already finished"
        return out

    if work["attempts"] >= _MAX_ATTEMPTS:
        work["status"] = "stopped"
        out.update(outcome="stopped", status="stopped",
                   detail=f"used its {_MAX_ATTEMPTS}-attempt budget without finishing")
        _persist(goal, work)
        return out

    plan = _plan_next(goal, work)
    if not plan.get("ok"):
        work["attempts"] += 1
        work["no_progress"] += 1
        step = {"at": time.time(), "outcome": "unplannable",
                "detail": plan.get("reason", "")}
        work["steps"].append(step)
        if work["no_progress"] >= _NO_PROGRESS_LIMIT:
            work["status"] = "stopped"
        _persist(goal, work)
        out.update(outcome="unplannable", detail=plan.get("reason", ""),
                   status=work["status"], attempts=work["attempts"])
        return out

    # execute_goal re-plans internally; pass the already-resolved objective through so
    # the gate sees the same action this loop decided on.
    class _Step:
        goal_id = getattr(goal, "goal_id", "")
        title = getattr(goal, "title", "")
        objective = plan.get("objective") or getattr(goal, "objective", "")

    trace = execute_goal(_Step(), dry_run=dry_run)

    work["attempts"] += 1
    step = {"at": time.time(), "action": trace.get("action") or plan.get("action"),
            "outcome": trace.get("outcome"), "detail": trace.get("detail", "")}
    work["steps"].append(step)

    if trace.get("outcome") == "done":
        work["no_progress"] = 0
    else:
        work["no_progress"] += 1

    done, why = (False, "")
    if trace.get("outcome") == "done":
        done, why = _criteria_met(goal, work)
    if done:
        work["status"] = "done"
        work["completed_at"] = time.time()
        work["completion_reason"] = why
    elif work["no_progress"] >= _NO_PROGRESS_LIMIT:
        work["status"] = "stopped"
        work["stop_reason"] = "consecutive steps achieved nothing"
    elif work["attempts"] >= _MAX_ATTEMPTS:
        work["status"] = "stopped"
        work["stop_reason"] = f"used its {_MAX_ATTEMPTS}-attempt budget"

    _persist(goal, work)
    out.update(outcome=trace.get("outcome"), detail=trace.get("detail", ""),
               action=step.get("action"), status=work["status"],
               attempts=work["attempts"])
    if work["status"] == "done":
        out["completion_reason"] = why
    return out


def work_tick(limit: int = 1, *, now: float | None = None,
              dry_run: bool = False) -> Dict[str, Any]:
    """Advance up to ``limit`` unfinished goals by one step each.

    Callers are responsible for checking the policy mode — this does not grant itself
    permission to act any more than ``goal_execution`` does.
    """
    limit = max(0, min(int(limit), 3))
    out: Dict[str, Any] = {"ok": True, "kind": "work_tick", "advanced": 0, "items": []}
    if limit == 0:
        return out
    try:
        from eli.planning.goal_store import list_active_goals
        goals = list_active_goals() or []
    except Exception as e:
        return {"ok": False, "kind": "work_tick", "advanced": 0, "items": [],
                "error": f"goal store unavailable ({e})"}

    unfinished = [g for g in goals if _work_state(g).get("status") == "active"]
    def _priority(g) -> float:
        raw = getattr(g, "priority", None)
        try:
            return float(raw) if raw is not None else 0.5
        except (TypeError, ValueError):
            return 0.5

    unfinished.sort(key=_priority, reverse=True)

    for goal in unfinished[:limit]:
        item = advance_goal(goal, dry_run=dry_run)
        out["items"].append(item)
        if item.get("outcome") == "done" or item.get("status") == "done":
            out["advanced"] += 1
        elif item.get("outcome") not in ("stopped", "unplannable"):
            out["advanced"] += 1
    return out


def progress_report() -> Dict[str, Any]:
    """What ELI is working on, what it finished, and what it gave up on — from the
    persisted record, so it survives a restart and reflects real outcomes."""
    try:
        from eli.planning.goal_store import load_goals
        goals = load_goals() or []
    except Exception as e:
        return {"ok": False, "error": f"goal store unavailable ({e})", "goals": []}

    rows: List[Dict[str, Any]] = []
    for g in goals:
        work = _work_state(g)
        rows.append({
            "goal_id": getattr(g, "goal_id", ""),
            "title": getattr(g, "title", ""),
            "status": work.get("status"),
            "attempts": work.get("attempts", 0),
            "last_step": (work.get("steps") or [{}])[-1],
            "stop_reason": work.get("stop_reason", ""),
            "completion_reason": work.get("completion_reason", ""),
        })
    return {"ok": True, "goals": rows,
            "active": sum(1 for r in rows if r["status"] == "active"),
            "done": sum(1 for r in rows if r["status"] == "done"),
            "stopped": sum(1 for r in rows if r["status"] == "stopped")}
