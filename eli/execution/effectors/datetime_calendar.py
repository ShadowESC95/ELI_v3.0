"""Date / calendar effector — deterministic date + calendar-stub actions.

Registry handlers for DATE / GET_DATE (real) and LIST_EVENTS / ADD_EVENT (stubs until a
calendar provider is wired). Time-of-day (TIME/GET_TIME) stays in the executor for now.
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


_register("DATE", _h_date)
_register("GET_DATE", _h_date)
_register("LIST_EVENTS", _h_calendar_stub)
_register("ADD_EVENT", _h_calendar_stub)

__all__ = ["_h_date", "_h_calendar_stub"]
