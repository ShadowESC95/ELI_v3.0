"""Date / time / calendar effector — deterministic date + time-of-day + calendar stubs.

Registry handlers for DATE / GET_DATE and TIME / GET_TIME (real) and LIST_EVENTS /
ADD_EVENT (stubs until a calendar provider is wired).
"""
from __future__ import annotations

from eli.core.worldclock import describe
from eli.execution.effectors.registry import register as _register

_CAL_MSG = ("Calendar integration is not configured. Use your calendar app directly or "
            "wire a provider first.")


def _h_date(a, args):
    d = describe((args.get("original_query") or "") if args else "",
                 default_date=True, default_time=False)
    return {"ok": True, "action": a, "content": d, "response": d}


def _h_calendar_stub(a, args):
    return {"ok": True, "action": a, "content": _CAL_MSG, "response": _CAL_MSG}


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
