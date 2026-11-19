"""Date / time / calendar effector.

Registry handlers for DATE / GET_DATE, TIME / GET_TIME, and LIST_EVENTS / ADD_EVENT /
REMOVE_EVENT, which answer from ELI's own calendar (eli.runtime.agenda).
"""
from __future__ import annotations

import time

from eli.core.worldclock import describe
from eli.execution.effectors.registry import register as _register
from eli.utils.log import get_logger

log = get_logger(__name__)


def agenda_turn_context(args):
    """(user id, the user's own recent messages newest first) for a calendar action: "add it"
    names nothing itself, so the event is looked for in what they said in the last half hour."""
    who, earlier = "", []
    try:
        from eli.kernel import request_context as _rc_agenda
        who = str(_rc_agenda.user_id_var.get() or "")
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        from eli.core.paths import user_db_path
        from eli.memory import get_memory
        current = " ".join(str((args or {}).get("text") or "").split()).lower()
        turns = get_memory(db_path=user_db_path()).get_recent_conversation(
            limit=12, user_id=who or None, since=time.time() - 1800, role="user") or []
        for t in reversed(turns):
            said = str((t or {}).get("content") or "").strip()
            if said and " ".join(said.split()).lower() != current:
                earlier.append(said)
    except Exception:
        log.debug("recent turns unavailable for the agenda", exc_info=True)
    return who, earlier


def _h_date(a, args):
    d = describe((args.get("original_query") or "") if args else "",
                 default_date=True, default_time=False)
    return {"ok": True, "action": a, "content": d, "response": d}


def _h_calendar(a, args):
    # Both used to return "Calendar integration is not configured" marked ok, which the model
    # then reworded as "no events are scheduled according to your calendar".
    try:
        from eli.runtime import agenda as _agenda
        _who, _earlier = agenda_turn_context(args)
        if a == "REMOVE_EVENT":
            return _agenda.do_remove(args, user_id=_who)
        if a == "LIST_EVENTS":
            _listed = _agenda.do_list_events(args, user_id=_who, earlier=_earlier)
            if _listed.get("offer"):
                from eli.runtime.pending_proposal import set_pending_proposal
                set_pending_proposal(_listed["offer"], summary="add the event to the calendar")
            return _listed
        return _agenda.do_add_event(args, user_id=_who, earlier=_earlier)
    except Exception as e:
        log.debug("agenda action failed", exc_info=True)
        msg = f"The calendar could not be read or written: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_time(a, args):
    now_str = describe((args.get("original_query") or "") if args else "",
                       default_date=False, default_time=True)
    return {"ok": True, "action": a, "content": now_str, "response": now_str}


def _h_get_time(a, args):
    return _h_time("TIME", args)


_register("DATE", _h_date)
_register("GET_DATE", _h_date)
_register("TIME", _h_time)
_register("GET_TIME", _h_get_time)
_register("LIST_EVENTS", _h_calendar)
_register("ADD_EVENT", _h_calendar)
_register("REMOVE_EVENT", _h_calendar)

__all__ = ["_h_date", "_h_calendar", "_h_time", "_h_get_time", "agenda_turn_context"]
