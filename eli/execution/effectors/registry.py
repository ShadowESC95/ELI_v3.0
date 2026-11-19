"""Action → handler registry, and what is declared of each action — the extensible dispatch layer (v3 §3.2).

Each effector registers handlers for the actions it owns; the executor consults this
registry before its legacy if-chain. Adding a new action becomes: write a handler in an
effector module and `register()` it, saying there what the action is — no edit to the
executor's dispatch, and no list kept anywhere else.

A handler is `(action: str, args: dict) -> dict` and returns the StructuredResult. The
registry is populated at import time (effectors call `register`/`@handler` at module load);
the executor imports the relevant effectors, so their handlers are registered before the
first dispatch.

What is declared of an action (keyword arguments to `register`, or to `declare` for an
action whose handler is not an effector's, as the catalogue in
eli/tools/registry/capabilities_doc.py does for the actions it describes):

  about     (category, description, [example phrases]): its entry in the catalogue (`CATALOGUE`)
  shown     "always": its result is the reply in every reasoning mode; "quick": in Quick, and
            worded by the model in the others; unset: worded by the model
  direct    its result is returned straight from the executor, never handed back through the
            agent bus and chat path (kernel/stages/fast_actions)
  offered   False: never offered to the model's guess at an intent (a yes or no to something ELI
            asked, an internal step): it is reached through its own flow (cognition/llm_intent)
  explicit  only from the user's own words, never from a model's guess: it renames the user,
            changes ELI's setup or code, or erases data (cognition/llm_intent)
  open      offered to an MCP client without the control opt-in: it only reads, and nothing of
            the user's own (integrations/mcp/server)

Every list of actions sharing one of these used to be kept by hand where it was used
(SUPPORTED_ACTIONS, the verbatim sets of the engine and the fast path, the resolver's internal
and explicit-only sets, MCP's open set, the catalogue): a new action had to be written into
each, and the copies drifted. They are read from here (`Declared`).
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence, Set
from typing import Any, Callable, Dict, Iterator

Handler = Callable[[str, Dict[str, Any]], Dict[str, Any]]

_HANDLERS: Dict[str, Handler] = {}

FACETS = ("about", "shown", "direct", "offered", "explicit", "open")
_SHOWN = (None, "always", "quick")
# action -> {facet: value}; set once per facet (a second, different value is refused)
_DECLARED: Dict[str, Dict[str, Any]] = {}
# action -> (category, description, [phrases]), in the order declared: the catalogue
CATALOGUE: "OrderedDict[str, tuple]" = OrderedDict()
_VERSION = [0]


def _key(action: str) -> str:
    return str(action).strip().upper()


def declare(action: str, **facets: Any) -> None:
    """Say what an action is (see the module docstring). Each facet is said once: a second,
    different value for one is an error, so an action is described in one place."""
    name = _key(action)
    unknown = set(facets) - set(FACETS)
    if unknown:
        raise TypeError(f"{name}: no such facet {sorted(unknown)}; the facets are {FACETS}")
    if facets.get("shown") not in _SHOWN:
        raise ValueError(f"{name}: shown is one of {_SHOWN}")
    held = _DECLARED.setdefault(name, {})
    for facet, value in facets.items():
        if facet == "about":
            cat, desc, phrases = value
            value = (cat, desc, list(phrases))
        if facet in held and held[facet] != value:
            raise ValueError(f"{name}: {facet} is declared twice ({held[facet]!r} and {value!r})")
        held[facet] = value
        if facet == "about":
            CATALOGUE[name] = value
    _VERSION[0] += 1


def register(action: str, fn: Handler, **facets: Any) -> None:
    """Register `fn` as the handler for `action` (case-insensitive key), with what is declared of it."""
    _HANDLERS[_key(action)] = fn
    if facets:
        declare(action, **facets)
    else:
        _VERSION[0] += 1


def handler(action: str, **facets: Any) -> Callable[[Handler], Handler]:
    """Decorator form: `@handler("PLAY_MEDIA", direct=True)`."""
    def deco(fn: Handler) -> Handler:
        register(action, fn, **facets)
        return fn
    return deco


def get(action: str) -> Handler | None:
    """The handler for `action`, or None if the legacy if-chain should handle it."""
    return _HANDLERS.get(_key(action))


def registered_actions() -> set[str]:
    return set(_HANDLERS)


def facets_of(action: str) -> Dict[str, Any]:
    _complete()
    return dict(_DECLARED.get(_key(action)) or {})


_COMPLETE = [False]


def _complete() -> None:
    """Every declaration made: the catalogue's and every effector's (both declare at import)."""
    if _COMPLETE[0]:
        return
    _COMPLETE[0] = True       # first, for a declaration read while these import
    try:
        import importlib
        importlib.import_module("eli.tools.registry.capabilities_doc")
        from eli.execution.effectors import load_all
        load_all()
    except BaseException:
        _COMPLETE[0] = False  # not complete: the next reader meets the failure too, not half a registry
        raise


def with_facet(facet: str, *values: Any) -> frozenset:
    """The actions declared with `facet` set to one of `values` (True when none are given)."""
    if facet not in FACETS:
        raise TypeError(f"no such facet {facet!r}")
    _complete()
    wanted = values or (True,)
    return frozenset(name for name, held in _DECLARED.items() if facet in held and held[facet] in wanted)


class Declared(Set):
    """The live set of actions declared with a facet, for a module constant that was a hand-kept
    list: `_DIRECT_RESULT_ACTIONS = Declared("shown", "quick", "always")`. Read when first used, so
    a module that defines one does not import every effector when it is itself imported."""

    def __init__(self, facet: str, *values: Any, extra: tuple = ()) -> None:
        self._facet, self._values, self._extra = facet, values, frozenset(_key(a) for a in extra)
        self._seen = (-1, frozenset())

    def _items(self) -> frozenset:
        if self._seen[0] != _VERSION[0]:
            items = with_facet(self._facet, *self._values) | self._extra
            self._seen = (_VERSION[0], items)
        return self._seen[1]

    @classmethod
    def _from_iterable(cls, it) -> frozenset:
        return frozenset(it)

    def __contains__(self, action: object) -> bool:
        return isinstance(action, str) and _key(action) in self._items()

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._items()))

    def __len__(self) -> int:
        return len(self._items())

    def __repr__(self) -> str:
        return f"Declared({self._facet}={self._values or (True,)}: {len(self)} actions)"


class SupportedActions(Sequence):
    """The executor's SUPPORTED_ACTIONS: the actions it answers itself (`own`), then every action an
    effector registers, read live. The list held every registered action by hand as well, so a new
    action had to be written into it too, or the intent resolver and MCP could not reach it."""

    def __init__(self, own) -> None:
        self._own = tuple(dict.fromkeys(_key(a) for a in own))
        self._seen: tuple = (-1, (), frozenset())

    def _items(self) -> tuple:
        if self._seen[0] != _VERSION[0]:
            items = self._own + tuple(sorted(set(_HANDLERS) - set(self._own)))
            self._seen = (_VERSION[0], items, frozenset(items))
        return self._seen[1]

    def own(self) -> tuple:
        return self._own

    def __getitem__(self, index):
        return self._items()[index]

    def __len__(self) -> int:
        return len(self._items())

    def __contains__(self, action: object) -> bool:
        self._items()
        return isinstance(action, str) and _key(action) in self._seen[2]

    def __repr__(self) -> str:
        return f"SupportedActions({len(self)} actions, {len(self._own)} the executor's own)"


__all__ = ["Handler", "register", "handler", "get", "registered_actions", "declare", "facets_of",
           "with_facet", "Declared", "SupportedActions", "CATALOGUE", "FACETS"]
