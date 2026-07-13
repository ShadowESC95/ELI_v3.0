"""Jobs effector — CHECK_JOB, BACKGROUND_JOBS (background task status)."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_check_job(a, args):
    import re as _re_jb
    from eli.runtime.background_tasks import get_background_tasks as _get_bt
    _bt = _get_bt()
    if a == "BACKGROUND_JOBS":
        jobs = _bt.list(limit=15)
        if not jobs:
            msg = "No background jobs."
        else:
            msg = "Background jobs:\n" + "\n".join(
                f"  #{j['id']} [{j['status']}] {j['name']} ({j['elapsed_s']}s)" for j in jobs)
        return {"ok": True, "action": a, "jobs": jobs, "content": msg, "response": msg}
    _jid = args.get("job_id") or args.get("id")
    if _jid is None:
        _m = _re_jb.search(r"\b(\d+)\b", str(args.get("text") or args.get("query") or args.get("description") or ""))
        _jid = _m.group(1) if _m else None
    if _jid is None:
        msg = "Which job? e.g. 'check job 3'."
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
        msg = f"Job #{_jid} ({t['name']}) done in {t['elapsed_s']}s{extra}."
        # Surface the actual result (e.g. the PDF-folder summary), not just a
        # 'done' status — that's what the user is waiting for.
        _job_content = ""
        if isinstance(r, dict):
            _job_content = str(r.get("content") or r.get("response") or "").strip()
        if _job_content and _job_content not in (msg,):
            msg += "\n\n" + _job_content
    elif t["status"] == "failed":
        msg = f"Job #{_jid} failed: {(t.get('error') or '')[:200]}"
    else:
        msg = f"Job #{_jid} ({t['name']}) is {t['status']} — {t['elapsed_s']}s elapsed."
    return {"ok": True, "action": a, "job": t, "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CHECK_JOB": _h_check_job,
    "BACKGROUND_JOBS": _h_check_job,
}.items():
    _register(_action, _fn)
