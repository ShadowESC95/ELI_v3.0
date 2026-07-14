"""Date / time / calendar effector — deterministic date + time-of-day + calendar stubs.

Registry handlers for DATE / GET_DATE and TIME / GET_TIME (real) and LIST_EVENTS /
ADD_EVENT (stubs until a calendar provider is wired).
"""
from __future__ import annotations

from datetime import datetime

from eli.execution.effectors.registry import register as _register

_CAL_MSG = ("Calendar integration is not configured. Use your calendar app directly or "
            "wire a provider first.")


def _h_date(a, args):
    d = datetime.now().strftime("%A, %Y-%m-%d")
    return {"ok": True, "action": a, "content": d, "response": d}


def _h_calendar_stub(a, args):
    return {"ok": True, "action": a, "content": _CAL_MSG, "response": _CAL_MSG}


def _h_time(a, args):
    original_query = args.get("original_query", "") if args else ""
    if "time" in original_query.lower() and not any(
            w in original_query.lower() for w in ["date", "day", "today", "calendar", "days"]):
        time_fmt = "%H:%M:%S"
    else:
        time_fmt = "%Y-%m-%d %H:%M:%S"
    now_str = datetime.now().strftime(time_fmt)
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
