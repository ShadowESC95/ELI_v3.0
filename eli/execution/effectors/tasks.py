"""Tasks effector — LIST_TASKS, CANCEL_TASK, RESCHEDULE_TASK.

Chat previously had no way to see or change the scheduler. Asked to delete a
scheduled task, the model had no grounded view of the pool and asserted there were
none while four were armed. These actions answer from the live pool via
`eli.runtime.task_admin`, which is the same code path the GUI Tasks tab uses.
"""
from __future__ import annotations

import re
import time

from eli.utils.log import get_logger

log = get_logger(__name__)

_ID_RX = re.compile(r"#?\s*(\d{1,6})\b")


def _reply(a, ok, msg, extra=None):
    """Build a result. `extra` is a DICT, not **kwargs: callers pass whole result
    dicts through, which carry their own ok/action/content keys — spreading those
    into keyword arguments collides with the explicit ones at call time."""
    extra = {k: v for k, v in (extra or {}).items()
             if k not in ("ok", "action", "content", "response")}
    return {"ok": ok, "action": a, "content": msg, "response": msg, **extra}


def _text_of(args) -> str:
    args = args or {}
    for k in ("text", "query", "request", "description", "message", "_raw_user_text"):
        v = str(args.get(k) or "").strip()
        if v:
            return v
    return ""


def _resolve_job_id(args):
    """Job id from args, else the first bare number in the utterance."""
    args = args or {}
    for k in ("job_id", "id", "task_id"):
        v = args.get(k)
        if v is not None:
            try:
                return int(v)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
    m = _ID_RX.search(_text_of(args))
    return int(m.group(1)) if m else None


def _h_list_tasks(a, args):
    from eli.runtime.task_admin import list_tasks, format_task
    tasks = list_tasks(limit=25)
    scheduled = [t for t in tasks if str(t.get("status") or "") == "scheduled"]
    if not tasks:
        return _reply(a, True, "No background or scheduled tasks.", {"tasks": []})
    lines = ["Scheduled and background tasks:"]
    lines += [f"  {format_task(t)}" for t in tasks]
    lines.append("")
    lines.append(f"{len(scheduled)} scheduled, {len(tasks)} total. "
                 f"Say \"cancel task <id>\" or \"reschedule task <id> to <time>\".")
    return _reply(a, True, "\n".join(lines), {"tasks": tasks})


def _h_cancel_task(a, args):
    from eli.runtime.task_admin import cancel_task, find_tasks, format_task
    jid = _resolve_job_id(args)

    if jid is None:
        # No id given — try to identify it by time or kind before giving up.
        text = _text_of(args)
        at = None
        try:
            from eli.runtime.scheduled_tasks import parse_when_strict
            at = parse_when_strict(text)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        matches = find_tasks(at=at) if at else find_tasks(text=text or None)
        if len(matches) == 1:
            jid = matches[0].get("id")
        elif len(matches) > 1:
            listing = "\n".join(f"  {format_task(t)}" for t in matches)
            return _reply(a, False,
                          f"That matches {len(matches)} tasks — which one?\n{listing}",
                          {"candidates": matches})
        else:
            from eli.runtime.task_admin import describe_tasks
            return _reply(a, False,
                          "I couldn't tell which task you mean. Currently scheduled:\n"
                          f"{describe_tasks()}\n\nSay \"cancel task <id>\".")

    r = cancel_task(jid)
    if r.get("ok"):
        return _reply(a, True, f"Cancelled task #{jid} ({r.get('kind') or 'task'}). "
                               f"It won't run, and it won't come back after a restart.", r)
    return _reply(a, False, f"Couldn't cancel task #{jid}: {r.get('error')}", r)


def _h_reschedule_task(a, args):
    from eli.runtime.task_admin import reschedule_task, find_tasks, format_task, describe_tasks
    args = args or {}
    jid = _resolve_job_id(args)
    when = str(args.get("when") or args.get("time") or "").strip()
    text = _text_of(args)

    if not when:
        return _reply(a, False,
                      "When should it run instead? e.g. \"reschedule task 3 to 14:00\".",
                      {"needs_time": True})
    try:
        from eli.runtime.scheduled_tasks import parse_when_strict
        if parse_when_strict(when) is None:
            return _reply(a, False,
                          f"I couldn't read “{when}” as a time. Try \"at 14:00\", "
                          f"\"in 2 hours\", or \"tomorrow morning\".", {"needs_time": True})
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    if jid is None:
        matches = find_tasks(text=text or None)
        if len(matches) == 1:
            jid = matches[0].get("id")
        elif len(matches) > 1:
            listing = "\n".join(f"  {format_task(t)}" for t in matches)
            return _reply(a, False, f"Which task?\n{listing}", {"candidates": matches})
        else:
            return _reply(a, False,
                          "I couldn't tell which task you mean. Currently scheduled:\n"
                          f"{describe_tasks()}\n\nSay \"reschedule task <id> to <time>\".")

    r = reschedule_task(jid, when, request=args.get("request"), kind=args.get("kind"))
    if r.get("ok"):
        return _reply(a, True,
                      f"Moved task #{jid} → new job #{r.get('job_id')} ({r.get('kind')}) "
                      f"at {r.get('when_human')}.", r)
    return _reply(a, False, f"Couldn't reschedule task #{jid}: {r.get('error')}", r)


def _h_resume_task(a, args):
    # Real goal_store lookup, not a chat-routed guess — always states staleness.
    try:
        topic = str((args or {}).get("topic") or (args or {}).get("query") or "").strip()
        if not topic:
            msg = "I don't have a project name to look up — what should I resume?"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        from eli.planning.goal_store import find_task, task_brief_for
        g = find_task(topic, include_idle=True)
        if g is None:
            msg = f"I don't have a saved task matching \"{topic}\" — nothing to resume."
            return {"ok": True, "action": a, "content": msg, "response": msg, "found": False}
        days = max(0.0, (time.time() - float(getattr(g, "updated_at", 0.0) or 0.0)) / 86400.0)
        staleness = "touched today" if days < 1.0 else f"last touched {days:.0f} day{'s' if days >= 2 else ''} ago"
        brief = task_brief_for(g).strip()
        msg = (f"Resuming \"{g.title}\" — {staleness}.\n{brief}" if brief
               else f"Resuming \"{g.title}\" — {staleness}. No recorded details yet.")
        return {"ok": True, "action": a, "content": msg, "response": msg, "found": True,
                "goal_id": g.goal_id, "days_stale": round(days, 1)}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "RESUME_TASK": _h_resume_task,
    "LIST_TASKS": _h_list_tasks,
    "CANCEL_TASK": _h_cancel_task,
    "RESCHEDULE_TASK": _h_reschedule_task,
}.items():
    _register(_action, _fn)
