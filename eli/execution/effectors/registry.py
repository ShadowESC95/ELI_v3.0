"""Action → handler registry — the extensible dispatch layer (v3 §3.2).

Each effector registers handlers for the actions it owns; the executor consults this
registry before its legacy if-chain. Adding a new action becomes: write a handler in an
effector module and `register()` it — no edit to the executor's dispatch.

A handler is `(action: str, args: dict) -> dict` and returns the StructuredResult. The
registry is populated at import time (effectors call `register`/`@handler` at module load);
the executor imports the relevant effectors, so their handlers are registered before the
first dispatch.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

Handler = Callable[[str, Dict[str, Any]], Dict[str, Any]]

_HANDLERS: Dict[str, Handler] = {}


def register(action: str, fn: Handler) -> None:
    """Register `fn` as the handler for `action` (case-insensitive key)."""
    _HANDLERS[str(action).strip().upper()] = fn


def handler(action: str) -> Callable[[Handler], Handler]:
    """Decorator form: `@handler("PLAY_MEDIA")`."""
    def deco(fn: Handler) -> Handler:
        register(action, fn)
        return fn
    return deco


def get(action: str) -> Handler | None:
    """The handler for `action`, or None if the legacy if-chain should handle it."""
    return _HANDLERS.get(str(action).strip().upper())


def registered_actions() -> set[str]:
    return set(_HANDLERS)


__all__ = ["Handler", "register", "handler", "get", "registered_actions"]
