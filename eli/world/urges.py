"""
eli/world/urges.py
──────────────────
What in ELI's own life asks to be made into something.

Nothing here decides what ELI makes. When something happens in ELI (an insight
is formed, a goal is taken on or finished, an ambition to improve is raised,
something ELI tried fails, something ELI did for the user: a search, a document,
a song) the event already passes through ELI's world. And when nothing else is
waiting, the turn of the day (morning, afternoon, evening, night) is a moment too. The ones that carry words are
kept as *urges*: the moment, in its own words, with where it came from.

Later, when ELI is free (a model already in memory, nobody waiting, and
the user's mood not asking for restraint), ELI may make something of one in
whichever room of its house ELI chooses, or relight one. What ELI makes, and
where, is up to ELI, and a small model may choose to make nothing. Whatever ELI makes is linked to the record the moment came from, so "why
is this here" has a true answer.

    consider(state, event)   in the engine: keep a moment that has words
    waiting()                the urges not yet acted on
    act(scene)               make something of the oldest, if ELI will
    free()                   whether ELI is free to: a model in memory, and nobody's mood against it
    act_when_free()          act, only when ELI is free: for ELI's own background loop
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from eli.world.building import OWN_ROOM, OWN_ROOM_INFO
from eli.world.core.schemas import EliWorldState, WorldEvent

log = logging.getLogger(__name__)

KEPT = 12
TEXT_MAX = 300

# Which events are moments of ELI's own. This says what kind of moment each is;
# it says nothing about what, if anything, becomes of it.
MOMENTS = {
    "reflection": "insight",
    "task_opened": "goal",
    "task_closed": "goal_done",
    "improvement_proposal": "ambition",
    "upgrade_candidate": "ambition",
    "belief_changed": "belief",
    "error_detected": "trouble",
}
DONE_FOR_YOU = "done_for_you"        # a tool that worked, when what came of it was for the user
TIME_OF_DAY = "time_of_day"
# the room itself talking, and the World panel's buttons for trying events out
_NOT_HIS = ("spatial_client", "composer", "world_building", "eli_world_panel")


def consider(state: EliWorldState, event: WorldEvent) -> None:
    """Keep this event as an urge if it is a moment of ELI's own and has words."""
    kind = MOMENTS.get(event.event_type)
    text = " ".join(str(event.summary or "").split())[:TEXT_MAX]
    said = (event.payload or {}).get("said") if event.event_type == "task_completed" else None
    if said:
        kind, text = DONE_FOR_YOU, " ".join(str(said).split())[:TEXT_MAX]
    if kind is None or len(text) < 12 or event.source in _NOT_HIS:
        return
    if text.lower().startswith("proactive tick"):
        return                      # the loop counting its own work is not a thought
    space = state.spaces.setdefault(OWN_ROOM, dict(OWN_ROOM_INFO))
    urges = [u for u in space.get("urges") or [] if isinstance(u, dict)]
    if any(u.get("text") == text for u in urges):
        return
    urges.append({"kind": kind, "text": text, "ref": event.timestamp, "source": str(event.source)})
    space["urges"] = urges[-KEPT:]


def _store():
    from eli.world.local_world_bridge import _get_engine
    return _get_engine().storage


def waiting() -> List[Dict[str, Any]]:
    space = (_store().load().spaces or {}).get(OWN_ROOM) or {}
    return [u for u in space.get("urges") or [] if isinstance(u, dict)]


def _settle(urge: Dict[str, Any]) -> None:
    """This urge has been answered, one way or the other."""
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    with INGEST_LOCK:
        store = _store()
        state = store.load()
        space = state.spaces.get(OWN_ROOM)
        if isinstance(space, dict):
            space["urges"] = [u for u in space.get("urges") or [] if u.get("ref") != urge.get("ref")]
            store.save(state)


def act(scene: Optional[Dict[str, Any]] = None, *,
        generate: Optional[Callable[[str], Any]] = None) -> Optional[Dict[str, Any]]:
    """Let ELI make something of the oldest urge. None when nothing is waiting.
    If the model is not there the urge is kept for when it is."""
    from eli.world import composer
    pending = waiting()
    if not pending:
        return None
    urge = pending[0]
    outcome = composer.compose(None, scene, about=urge, generate=generate)   # ComposeError leaves it waiting
    _settle(urge)
    made = [r.get("name") for r in outcome.get("results") or [] if r.get("ok") and r.get("name")]
    if made:
        from eli.world.persistence.journal import append_journal_entry
        append_journal_entry(
            title=f"Made of my own accord: {', '.join(made)}",
            body=f"Because: {urge['text']}\n\nThe moment was a {urge['kind'].replace('_', ' ')}"
                 f" (from `{urge.get('source', '')}`).",
            source="eli_world_urges")
    outcome["about"] = {"kind": urge["kind"], "text": urge["text"]}
    return outcome


def free() -> bool:
    """Whether ELI is free to make something of its own: a model already in memory
    (one is never loaded for this) and the user's mood not asking ELI to hold back.
    Whoever asks has already checked that nobody is waiting on ELI."""
    try:
        from eli.cognition.gguf_inference import is_loaded
        if not is_loaded():
            return False
    except Exception:
        return False
    try:
        from eli.cognition.affect_policy import should_defer_proactive
        if should_defer_proactive()[0]:
            return False
    except Exception:
        pass
    return True


def _day_turned(now: Optional[float] = None) -> None:
    """With nothing else waiting, the part of the day that has begun since ELI last noticed one
    is a moment: ELI may answer the evening coming in, or not."""
    import time as _time
    from eli.runtime.reflection import part_of_day
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    now = _time.time() if now is None else now
    part = part_of_day(now)
    with INGEST_LOCK:
        store = _store()
        state = store.load()
        space = state.spaces.setdefault(OWN_ROOM, dict(OWN_ROOM_INFO))
        if space.get("day_part") == part:
            return
        first = space.get("day_part") is None
        space["day_part"] = part
        if not first and not [u for u in space.get("urges") or [] if isinstance(u, dict)]:
            clock = _time.strftime("%H:%M", _time.localtime(now))
            space["urges"] = [{"kind": TIME_OF_DAY, "text": f"It is {part} now ({clock}).", "ref": now,
                               "source": "clock"}]
        store.save(state)


def act_when_free() -> Optional[Dict[str, Any]]:
    """For ELI's own background loop: act, only when ELI is free."""
    if not free():
        return None
    try:
        _day_turned()
    except Exception:
        log.debug("the turn of the day was not noticed", exc_info=True)
    try:
        from eli.world import embodiment
        return act(embodiment.live())
    except Exception:
        return None
