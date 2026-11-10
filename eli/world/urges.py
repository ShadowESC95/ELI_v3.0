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
    thought_moments(space)   the moments ELI's thoughts bring: what surprised it, what it was told two
                             ways, a fix it proposed, a failure it diagnosed, a memory it let go, a memory
                             that proved wrong (eli/cognition/thoughts.py), not yet acted on
    waiting()                the moments not yet acted on: the events' and the thoughts'
    act(scene)               make something of the moment ELI is most curious about, if ELI will;
                             the act is a figure settled when it is taken back or when it stood
    free()                   whether ELI is free to: a model in memory, and nobody's mood against it
    act_when_free()          act, only when ELI is free: for ELI's own background loop; then write
                             of its own records (eli/world/stories.py)
"""
from __future__ import annotations

import logging
import time as _time
from typing import Any, Callable, Dict, List, Optional

from eli.world.building import OWN_ROOM, OWN_ROOM_INFO
from eli.world.core.schemas import EliWorldState, WorldEvent

log = logging.getLogger(__name__)

KEPT = 12
TEXT_MAX = 300
# An act nobody has taken back after this long stood: the figure given for it is settled right.
STOOD_SECONDS = 86400.0

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
    "game_played": "play",           # a game ELI made up, played together (eli/world/games.py)
}
DONE_FOR_YOU = "done_for_you"        # a tool that worked, when what came of it was for the user
TIME_OF_DAY = "time_of_day"
# Thoughts that are moments too (eli/cognition/thoughts.py), by the kind of moment each is: a memory
# let go and a recall that proved wrong are moments only once settled so (thought_moments).
THOUGHT_MOMENTS = {"surprise": "surprise", "conflict": "conflict", "proposal": "ambition", "diagnosis": "trouble",
                   "memory": "memory", "recall": "recall"}
THOUGHT_DAYS = 1.0                   # a thought older than this is no longer a moment to act on
ACTED_KEPT = 200                     # thought ids ELI has acted on, kept so none is acted on twice
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


def thought_moments(space: Dict[str, Any], now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The moments ELI's thoughts of the last THOUGHT_DAYS bring, in the thought's own words, each
    with the thought it came of; the ones ELI has acted on (space["acted_thoughts"]) are not brought
    again. A memory let go counts once dropped, a recall once it proved wrong."""
    try:
        from eli.cognition.thoughts import recent
    except ImportError:
        return []
    now = _time.time() if now is None else now
    acted = {int(i) for i in (space or {}).get("acted_thoughts") or [] if str(i).lstrip("-").isdigit()}
    out: List[Dict[str, Any]] = []
    for thought in recent(40, kinds=list(THOUGHT_MOMENTS), since=now - THOUGHT_DAYS * 86400.0):
        kind = str(thought.get("kind") or "")
        if int(thought["id"]) in acted:
            continue
        if kind == "memory" and str(thought.get("decision") or "") != "dropped":
            continue
        if kind == "recall" and str(thought.get("outcome") or "") != "wrong":
            continue
        text = " ".join(str(thought.get("words") or thought.get("what") or "").split())[:TEXT_MAX]
        if len(text) < 12:
            continue
        out.append({"kind": THOUGHT_MOMENTS[kind], "text": text, "ref": f"thought:{thought['id']}",
                    "source": "thoughts", "thought": int(thought["id"])})
    return out


def waiting(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The moments waiting: those ELI's own events brought (consider), then those its thoughts
    bring (thought_moments); none twice."""
    space = (_store().load().spaces or {}).get(OWN_ROOM) or {}
    kept = [u for u in space.get("urges") or [] if isinstance(u, dict)]
    seen = {u.get("text") for u in kept}
    for moment in thought_moments(space, now):
        if moment["text"] not in seen:
            kept.append(moment)
            seen.add(moment["text"])
    return kept


def _settle(urge: Dict[str, Any]) -> None:
    """This urge has been answered, one way or the other: an event's moment leaves the list, a
    thought's is marked acted on."""
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    with INGEST_LOCK:
        store = _store()
        state = store.load()
        space = state.spaces.setdefault(OWN_ROOM, dict(OWN_ROOM_INFO))
        if urge.get("thought") is not None:
            acted = [int(i) for i in space.get("acted_thoughts") or [] if str(i).lstrip("-").isdigit()]
            acted.append(int(urge["thought"]))
            space["acted_thoughts"] = acted[-ACTED_KEPT:]
        else:
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
    _settle_stood()
    urge = _first_by_interest(pending)
    # Whether to act on it at all: the chance acts of this kind stood (were not taken back), against
    # the chance acts as a whole stood (eli/cognition/epistemics, kind act); with nothing settled yet
    # both are even and ELI acts. A kind that keeps being taken back is let go of, and the thought
    # says so.
    stood = _stands(urge)
    if not stood["act"]:
        _settle(urge)
        _thought_of(urge, [], stood, decision="declined")
        return None
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
        _assess(urge, stood)
    _thought_of(urge, made, stood, decision="taken" if made else "let go")
    outcome["about"] = {"kind": urge["kind"], "text": urge["text"]}
    return outcome


def _ref(urge: Dict[str, Any]) -> str:
    return f"act:{urge.get('ref')}"


def _quiet_minutes() -> float:
    """How long since the user last spoke to ELI, in minutes: a feature of an act's figure."""
    try:
        from eli.cognition.inference_broker import seconds_since_foreground
        since = float(seconds_since_foreground())
    except (ImportError, AttributeError, TypeError, ValueError):
        return 0.0
    # no conversation yet this run gives a very large number: nothing to measure from
    return round(since / 60.0, 1) if 0.0 <= since < 7 * 86400.0 else 0.0


def _stands(urge: Dict[str, Any]) -> Dict[str, Any]:
    """Whether ELI acts on a moment of this kind: the base rate of such acts standing, against the base
    rate of all acts standing; {"act", "p_kind", "n_kind", "p_all", "n_all"}."""
    try:
        from eli.cognition.epistemics import ACT, base_rate
        p_kind, n_kind = base_rate(ACT, class_=str(urge.get("kind") or ""))
        p_all, n_all = base_rate(ACT)
    except Exception:
        log.debug("act rates unavailable", exc_info=True)
        p_kind, n_kind, p_all, n_all = 0.5, 0, 0.5, 0
    return {"act": not (n_kind and n_all) or p_kind >= p_all, "p_kind": p_kind, "n_kind": n_kind,
            "p_all": p_all, "n_all": n_all}


def _assess(urge: Dict[str, Any], stood: Dict[str, Any]) -> None:
    """The figure for an act that made something: settled wrong when it is taken back (undone), right
    when it has stood STOOD_SECONDS."""
    try:
        from eli.cognition.epistemics import ACT, assess
        assess(ACT, float(stood["p_kind"]), request_id=_ref(urge), class_=str(urge.get("kind") or ""),
               features={"quiet_minutes": _quiet_minutes()})
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
        log.debug("act not assessed", exc_info=True)


def _thought_of(urge: Dict[str, Any], made: List[str], stood: Dict[str, Any], *, decision: str) -> None:
    try:
        from eli.cognition.thoughts import record
        what = f"made {', '.join(made)}" if made else ("made nothing of it" if decision == "let go" else "let the moment go")
        rate = (f"acts of this kind stood {stood['p_kind']:.0%} of the time ({stood['n_kind']} settled)"
                if stood.get("n_kind") else "no act of this kind has been settled yet")
        record("act", what, why=[f"a {str(urge.get('kind', '')).replace('_', ' ')} moment: {urge.get('text', '')}"[:200], rate],
               chance=float(stood["p_kind"]), decision=decision,
               reason=("because such acts stood" if decision == "taken" else
                       ("because such acts kept being taken back" if decision == "declined" else "ELI chose to")),
               subject=str(urge.get("text") or "")[:120], ref=_ref(urge) if made else "")
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
        log.debug("act thought not kept", exc_info=True)


def _settle_stood(now: Optional[float] = None) -> int:
    """Acts nobody took back for STOOD_SECONDS stood: their figures and thoughts are settled right."""
    before = (_time.time() if now is None else now) - STOOD_SECONDS
    n = 0
    try:
        from eli.cognition import thoughts
        from eli.cognition.epistemics import ACT, settle_open
        n = settle_open(ACT, "act:", True, how="stood", before=before)
        thoughts.settle_prefix("act:", "stood", how="nobody took it back", before=before, kinds=["act"])
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
        log.debug("standing acts not settled", exc_info=True)
    return n


def undone(refs: List[Any]) -> int:
    """The user took back what ELI made of these moments: the figure and the thought of each are
    settled wrong (eli/world/building.py calls this from an undo). How many were."""
    n = 0
    try:
        from eli.cognition import thoughts
        from eli.cognition.epistemics import ACT, settle
        for ref in refs or []:
            if ref in (None, ""):
                continue
            if settle(f"act:{ref}", False, how="undone", kind=ACT):
                n += 1
            thoughts.settle(f"act:{ref}", "undone", how="the user took it back")
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
        log.debug("undone acts not settled", exc_info=True)
    return n


def interest_in(text: str) -> float:
    """ELI's own interest in what a moment is about: its curiosity about the subject
    (eli/cognition/curiosity.py), 0 when nothing has surprised it there."""
    try:
        from eli.cognition.curiosity import about
        return float(about(text))
    except Exception:
        return 0.0


def _first_by_interest(pending: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The moment to act on: the one ELI is most curious about; the oldest among equals."""
    best, best_interest = pending[0], -1.0
    for urge in pending:
        interest = interest_in(str(urge.get("text") or ""))
        if interest > best_interest:
            best, best_interest = urge, interest
    return best


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
    """For ELI's own background loop: act, only when ELI is free; then write of its own records (a
    story, which of its positions are likings, its shelves' names: eli/world/stories.py)."""
    if not free():
        return None
    try:
        _day_turned()
    except Exception:
        log.debug("the turn of the day was not noticed", exc_info=True)
    outcome = None
    try:
        from eli.world import embodiment
        outcome = act(embodiment.live())
    except Exception:
        log.debug("no act of ELI's own this time", exc_info=True)
    try:
        from eli.world import stories
        stories.write_when_free()
    except Exception:
        log.debug("ELI wrote nothing of its own this time", exc_info=True)
    return outcome
