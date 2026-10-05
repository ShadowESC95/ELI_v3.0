"""Date / time / calendar effector.

Registry handlers for DATE / GET_DATE, TIME / GET_TIME, and LIST_EVENTS / ADD_EVENT, which
answer from ELI's own calendar (eli.runtime.agenda).
"""
from __future__ import annotations

from eli.core.worldclock import describe
from eli.execution.effectors.registry import register as _register


def _h_date(a, args):
    d = describe((args.get("original_query") or "") if args else "",
                 default_date=True, default_time=False)
    return {"ok": True, "action": a, "content": d, "response": d}


def _h_calendar_stub(a, args):
    """The built-in executor branch answers these from eli.runtime.agenda; so does this."""
    from eli.runtime import agenda
    if str(a).upper() == "LIST_EVENTS":
        return agenda.do_list_events(args or {})
    return agenda.do_add_event(args or {})


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
_register("LIST_EVENTS", _h_calendar_stub)
_register("ADD_EVENT", _h_calendar_stub)

__all__ = ["_h_date", "_h_calendar_stub", "_h_time", "_h_get_time"]
