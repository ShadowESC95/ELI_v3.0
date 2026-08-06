"""The sentinel that distinguishes "pass" from "route() returns None".

A stage returning ``None`` means *this stage did not match, try the next one*.
But a few guards in the original ``route()`` deliberately did ``return None`` to
end routing outright — the utterance mentions a topic but carries no actionable
verb, so the caller should treat it as unrouted and let the priority pipeline's
own fallback handle it (a lower-confidence CHAT than route()'s own fallback).

Those two meanings collapse into each other once the guards live in stages, so
the deliberate one gets its own value. Returning ``ROUTE_NONE`` makes route()
return ``None`` immediately, exactly as the inline ``return None`` did.
"""
from __future__ import annotations


class _RouteNone:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ROUTE_NONE"

    def __bool__(self) -> bool:
        return False


ROUTE_NONE = _RouteNone()

__all__ = ["ROUTE_NONE"]
