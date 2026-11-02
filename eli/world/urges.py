"""
eli/world/urges.py
──────────────────
What in ELI's own life asks to be made into something.

Nothing here runs on a clock of its own and nothing here decides what ELI
makes. When something happens in ELI (an insight is formed, a goal is taken
on or finished, an ambition to improve is raised, something ELI tried fails)
the event already passes through ELI's world. The ones that carry words are
kept as *urges*: the moment, in its own words, with where it came from.

Later, when ELI is free (a model already in memory, nobody waiting, and
the user's mood not asking for restraint), ELI may make something of one in
whichever room of its house ELI chooses, or relight one. What ELI makes, and
where, is up to ELI, and a small model may choose to make nothing. Whatever ELI makes is linked to the record the moment came from, so "why
is this here" has a true answer.

    consider(state, event)   in the engine: keep a moment that has words
    waiting()                the urges not yet acted on
    act(scene)               make something of the oldest, if ELI will
    act_when_free()          the same, only when ELI is free: for ELI's own background loop
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from eli.world.building import OWN_ROOM, OWN_ROOM_INFO
from eli.world.core.schemas import EliWorldState, WorldEvent

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
# the room itself talking, and the World panel's buttons for trying events out
_NOT_HIS = ("spatial_client", "composer", "world_building", "eli_world_panel")


def consider(state: EliWorldState, event: WorldEvent) -> None:
    """Keep this event as an urge if it is a moment of ELI's own and has words."""
    kind = MOMENTS.get(event.event_type)
    text = " ".join(str(event.summary or "").split())[:TEXT_MAX]
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


def act_when_free() -> Optional[Dict[str, Any]]:
    """For ELI's own background loop. Acts only with a model already in memory
    (one is never loaded for this) and when the user's mood does not ask ELI to
    hold back. The loop itself has already checked that nobody is waiting on ELI."""
    try:
        from eli.cognition.gguf_inference import is_loaded
        if not is_loaded():
            return None
    except Exception:
        return None
    try:
        from eli.cognition.affect_policy import should_defer_proactive
        if should_defer_proactive()[0]:
            return None
    except Exception:
        pass
    try:
        from eli.world import embodiment
        return act(embodiment.live())
    except Exception:
        return None
