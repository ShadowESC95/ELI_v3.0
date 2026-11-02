"""Jobs effector — CHECK_JOB, BACKGROUND_JOBS (background task status)."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)


def _duration(seconds) -> str:
    s = int(round(float(seconds or 0)))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s // 3600} h {round(s % 3600 / 60)} min"


def _h_check_job(a, args):
    import re as _re_jb
    from eli.runtime.background_tasks import get_background_tasks as _get_bt
    _bt = _get_bt()
    if a == "BACKGROUND_JOBS":
        jobs = _bt.list(limit=15)
        if not jobs:
            msg = "No background jobs."
        else:
            from eli.runtime.background_tasks import work_lines
            msg = "Background jobs:\n" + "\n".join(f"  {ln}" for ln in work_lines())
        return {"ok": True, "action": a, "jobs": jobs, "content": msg, "response": msg}
    _jid = args.get("job_id") or args.get("id")
    if _jid is None:
        _m = _re_jb.search(r"\b(\d+)\b", str(args.get("text") or args.get("query") or args.get("description") or ""))
        _jid = _m.group(1) if _m else None
    _named_job = _jid is not None
    if _jid is None:        # "how's it going?": the job that is running, else the latest
        _jobs = _bt.list(limit=1, status="running") or _bt.list(limit=1)
        _jid = _jobs[0]["id"] if _jobs else None
    if _jid is None:
        msg = "No background jobs."
        return {"ok": False, "action": a, "content": msg, "response": msg}
    t = _bt.get(int(_jid))
    if not t:
        msg = f"No background job #{_jid}."
        return {"ok": False, "action": a, "content": msg, "response": msg}
    if t["status"] == "done":
        r = t.get("result") or {}
        extra = ""
        if isinstance(r, dict) and r.get("script_path"):
            extra = f" → {r.get('script_path')}"
            if r.get("solved"):
                extra += f" (verified, score {r.get('score')})"
        msg = f"Job #{_jid} ({t['name']}) done in {_duration(t['elapsed_s'])}{extra}."
        # Surface the actual result (e.g. the PDF-folder summary), not just a
        # 'done' status — that's what the user is waiting for. Not when they asked what is
        # going on and this is only the latest job: "eta on that job?" got the whole report back.
        _job_content = ""
        if isinstance(r, dict):
            _job_content = str(r.get("content") or r.get("response") or "").strip()
        _job_content = _job_content or " ".join(
            x for x in (t.get("note"), (t.get("meta") or {}).get("document_path")) if x)
        _result_asked = _named_job or _re_jb.search(
            r"\b(?:results?|summary|analysis|show|read|output)\b", str(args.get("text") or ""), _re_jb.I)
        if not _result_asked:
            msg = "Nothing is running. " + msg + (f" {_job_content.splitlines()[0]}" if _job_content else "")
        elif _job_content and _job_content not in (msg,):
            msg += "\n\n" + _job_content
    elif t["status"] == "failed":
        msg = f"Job #{_jid} ({t['name']}) failed: {(t.get('error') or '')}"
    elif t["status"] == "interrupted":
        msg = (f"Job #{_jid} ({t['name']}) was cut off when ELI closed, after {_duration(t['elapsed_s'])}. "
               "Say “do them again” to start it over.")
    elif t["status"] == "scheduled":
        import time as _time_jb
        msg = f"Job #{_jid} ({t['name']}) is scheduled" + (
            _time_jb.strftime(" for %A %H:%M.", _time_jb.localtime(float(t["scheduled_for"])))
            if t.get("scheduled_for") else ".")
    else:
        msg = f"Job #{_jid} ({t['name']}) is {t['status']}, {_duration(t['elapsed_s'])} in."
        _p = t.get("progress") or {}
        if _p.get("total"):
            msg += f" {str(_p.get('stage') or 'working').capitalize()}: {_p.get('done', 0)} of {_p['total']}"
            msg += (f", about {_duration(t['eta_s'])} left for that at the rate so far." if t.get("eta_s")
                    else ".")
    return {"ok": True, "action": a, "job": t, "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CHECK_JOB": _h_check_job,
    "BACKGROUND_JOBS": _h_check_job,
}.items():
    _register(_action, _fn)


def _h_goal_progress(a, args):
    """What ELI is working on, finished, or gave up on — from the persisted record."""
    try:
        from eli.planning.work_loop import progress_report
        rep = progress_report()
    except Exception as e:
        msg = f"Couldn't read goal progress: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not rep.get("ok"):
        msg = f"Couldn't read goal progress: {rep.get('error', 'unknown')}"
        return {"ok": False, "action": a, "content": msg, "response": msg}

    goals = rep.get("goals") or []
    if not goals:
        msg = ("I have no goals on the books, so there's nothing in progress. "
               "Autonomous work only runs when the operator policy is set to goal_driven.")
        return {"ok": True, "action": a, "content": msg, "response": msg, **rep}

    lines = [f"{rep['active']} active, {rep['done']} done, {rep['stopped']} stopped:"]
    for g in goals:
        bit = f"  {g['title']} — {g['status']} after {g['attempts']} attempt(s)"
        if g.get("stop_reason"):
            bit += f" ({g['stop_reason']})"
        last = g.get("last_step") or {}
        if last.get("outcome"):
            bit += f"; last step {last.get('action', '?')} → {last['outcome']}"
        lines.append(bit)
    body = "\n".join(lines)
    return {"ok": True, "action": a, "content": body, "response": body, **rep}


from eli.execution.effectors.registry import register as _register_goal_progress
_register_goal_progress("GOAL_PROGRESS", _h_goal_progress)
