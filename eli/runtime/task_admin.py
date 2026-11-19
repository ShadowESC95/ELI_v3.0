"""Headless list/cancel/reschedule for scheduled background tasks.

The GUI Tasks tab has always been able to do this; chat could not. There was no
LIST_TASKS / CANCEL_TASK / RESCHEDULE_TASK action anywhere, and the read-only
BACKGROUND_JOBS trigger required the literal phrase "background jobs" — so
"delete background task for 02:00 on Sunday" matched nothing, fell through to
CHAT, and the model answered from no evidence at all: it stated there were no
active background tasks while four were armed and visible in the Tasks tab.

This module holds that logic once, headless, so chat and the GUI cannot drift.
`describe_tasks()` additionally exists to be injected into prompts: an ungrounded
model asked about its own schedule will confabulate, so give it the rows.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from eli.runtime.scheduled_tasks import _WORKERS
from eli.utils.log import get_logger

log = get_logger(__name__)

# Every kind the scheduler can actually dispatch, plus "auto" (the kind is read from the request).
KNOWN_KINDS = ("auto", *_WORKERS)


def _bt():
    from eli.runtime.background_tasks import get_background_tasks
    return get_background_tasks()


def list_tasks(limit: int = 50, *, scheduled_only: bool = False) -> List[Dict[str, Any]]:
    """Return the live task pool as plain dicts (newest scheduler state)."""
    try:
        tasks = _bt().list(limit=limit) or []
    except Exception as exc:
        log.debug(f"[TASK_ADMIN] list failed: {exc}")
        return []
    if scheduled_only:
        tasks = [t for t in tasks if str(t.get("status") or "") == "scheduled"]
    return tasks


def format_task(t: Dict[str, Any]) -> str:
    jid = t.get("id")
    kind = t.get("kind") or "task"
    status = str(t.get("status") or "")
    meta = t.get("meta") or {}
    req = str(meta.get("request") or t.get("name") or "").strip()
    if status == "scheduled" and t.get("scheduled_for"):
        when = datetime.fromtimestamp(float(t["scheduled_for"])).strftime("%H:%M on %a %d %b")
        whenc = f"scheduled for {when}"
    else:
        whenc = status or "unknown"
    rec = " (recurring)" if meta.get("recurring") else ""
    return f"#{jid} [{kind}] {whenc}{rec}" + (f" — {req[:70]}" if req else "")


def describe_tasks(limit: int = 10) -> str:
    """One line per task, for prompt injection. Never empty — an explicit 'none'
    is what stops the model inventing an answer."""
    tasks = list_tasks(limit=limit)
    if not tasks:
        return "none"
    return "\n".join(format_task(t) for t in tasks)


def cancel_task(jid: int) -> Dict[str, Any]:
    """Cancel one task. background_tasks.cancel() now also drops the durable
    record, so a cancelled task cannot re-arm on the next boot."""
    try:
        jid = int(jid)
    except Exception:
        return {"ok": False, "error": f"not a task id: {jid!r}"}
    bt = _bt()
    before = bt.get(jid) or {}
    if not before:
        return {"ok": False, "error": f"no task #{jid}"}
    ok = bool(bt.cancel(jid))
    return {
        "ok": ok, "job_id": jid, "kind": before.get("kind"),
        "was": before.get("status"),
        "error": None if ok else "already running or finished — cannot cancel",
    }


def reschedule_task(jid: int, when: str, *, request: Optional[str] = None,
                    kind: Optional[str] = None) -> Dict[str, Any]:
    """Move a scheduled task to a new time, preserving its request, kind and
    recurrence unless explicitly overridden."""
    from eli.runtime.scheduled_tasks import schedule_request
    try:
        jid = int(jid)
    except Exception:
        return {"ok": False, "error": f"not a task id: {jid!r}"}
    bt = _bt()
    d = bt.get(jid) or {}
    if not d:
        return {"ok": False, "error": f"no task #{jid}"}
    if str(d.get("status") or "") != "scheduled":
        return {"ok": False,
                "error": f"task #{jid} is {d.get('status')} — only a scheduled task can be moved"}
    meta = d.get("meta") or {}
    req = str(request or meta.get("request") or d.get("name") or "").strip()
    if not req:
        return {"ok": False, "error": f"task #{jid} has no request text to re-create"}
    bt.cancel(jid)   # also forgets the durable record
    r = schedule_request(
        req,
        when_spec=when,
        kind=(kind or meta.get("kind") or None),
        # Preserve recurrence — the GUI dropped it, silently turning a nightly
        # job into a one-shot every time it was edited.
        recurring=bool(meta.get("recurring")),
    )
    if r.get("ok"):
        r["replaced"] = jid
    return r


def find_tasks(*, at: Optional[float] = None, kind: Optional[str] = None,
               text: Optional[str] = None, tolerance_s: float = 300.0) -> List[Dict[str, Any]]:
    """Find scheduled tasks by fire time, kind, or request text — so a user can
    say "cancel the 2am one" without knowing the job id."""
    out = []
    for t in list_tasks(scheduled_only=True):
        if at is not None:
            sf = t.get("scheduled_for")
            if sf is None or abs(float(sf) - float(at)) > tolerance_s:
                continue
        if kind and str(t.get("kind") or "").lower() != kind.lower():
            continue
        if text:
            hay = f"{(t.get('meta') or {}).get('request') or ''} {t.get('name') or ''}".lower()
            if text.lower() not in hay:
                continue
        out.append(t)
    return out
