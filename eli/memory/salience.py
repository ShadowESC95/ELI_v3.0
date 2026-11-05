"""
eli/memory/salience.py
──────────────────────
Surprise makes a memory matter more.

A memory's importance was set when it was stored and reinforced when it was recalled and helped.
Nothing in ELI's day-to-day could raise it: a claim the user contradicted, an action predicted to
work that failed (or the other way about), a mood that turned, a reply the user corrected, each
told ELI something was not as it believed, and the memories that belief rested on stayed where
they were. In a mind, surprise is what sets a memory apart: the prediction error is the learning
signal (Rescorla & Wagner, 1972), the unexpected is remembered, and the expected fades.

    note_surprise(kind, magnitude, subjects, why)   raise the importance of the memories about
                                                    `subjects` by the surprise, and keep a thought of it
    importance_gain(magnitude)                      how much a surprise of this size raises importance

The magnitude is a prediction error in 0..1 as its source measures it: the chance that was given
for what did not happen (a claim at 0.8 proved wrong: 0.8; an action predicted at 0.9 that failed:
0.9; a correction of a reply given at p: p), or for the affect state the share of its innovation
test that was exceeded, capped at one. The gain is the error times GAIN, where GAIN is the
Rescorla–Wagner learning rate at its usual middle setting (0.25): a full surprise lifts importance
a quarter of the way to one. Importance never passes one, and the stored weight (how long a row is
kept) is left to the policy's own curve.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

GAIN = 0.25                 # the learning rate: the share of the remaining distance to one a full surprise covers
SUBJECT_MEMORIES = 4        # how many memories about one subject a surprise reaches: the closest few, not the store


def importance_gain(magnitude: float) -> float:
    """The importance a surprise of this size adds: GAIN times the error, 0..GAIN."""
    return GAIN * max(0.0, min(1.0, float(magnitude or 0.0)))


def note_surprise(kind: str, magnitude: float, *, subjects: Iterable[str], why: str, user_id: str = "",
                  request_id: str = "", now: Optional[float] = None, db_path=None) -> Dict[str, Any]:
    """Something did not fit what ELI believed: raise the importance of the memories about
    `subjects` by the surprise, and keep a thought of it in the Anomaly Room. {"touched": ids,
    "gain": the gain}; nothing touched when nothing is known about the subjects."""
    gain = importance_gain(magnitude)
    named = [str(s).strip() for s in (subjects or []) if str(s).strip()]
    touched: List[int] = []
    if gain <= 0.0 or not named:
        return {"touched": touched, "gain": gain}
    try:
        from eli.memory.memory import get_memory
        mem = get_memory(db_path) if db_path else get_memory()
    except Exception as failure:  # noqa: BLE001 - no memory to raise: the thought is still kept
        log.debug("memory unavailable for the surprise: %s", failure)
        mem = None
    if mem is not None:
        for subject in named[:SUBJECT_MEMORIES]:
            try:
                hits = mem.recall_memory(subject, limit=SUBJECT_MEMORIES) or []
            except Exception as failure:  # noqa: BLE001 - a lookup that fails touches nothing
                log.debug("surprise lookup failed: %s", failure)
                continue
            ids = [int(h["id"]) for h in hits if str(h.get("id", "")).isdigit() and int(h["id"]) > 0]
            touched.extend(i for i in ids if i not in touched)
        if touched:
            try:
                mem.raise_importance(touched, gain, reason=f"surprise: {kind}")
            except (OSError, ValueError, TypeError, AttributeError) as failure:  # noqa: BLE001 - the store refusing the raise leaves the thought
                log.debug("importance not raised: %s", failure)
    try:
        # ELI's world hears it: its curiosity is the mean size of the sitting's surprises
        # (eli/world/agency/autonomy_engine.py), and the subjects are what it is curious about
        from eli.world.world_event_bus import fire_world_event
        fire_world_event("surprise", "salience", str(why or kind)[:200],
                         {"magnitude": max(0.0, min(1.0, float(magnitude or 0.0))), "subjects": named[:3]})
    except (OSError, ValueError, TypeError, ImportError, RuntimeError) as failure:  # noqa: BLE001 - the world is never in the way of the signal
        log.debug("surprise not heard by the world: %s", failure)
    try:
        from eli.cognition.thoughts import record
        record("surprise", f"{str(why or kind).strip()}", why=[f"{kind} did not fit", f"surprise {float(magnitude):.0%}"],
               chance=max(0.0, min(1.0, float(magnitude or 0.0))), decision="raised" if touched else "noted",
               reason=(f"{len(touched)} memor{'y' if len(touched) == 1 else 'ies'} about {', '.join(named[:3])} "
                       f"matter {gain:.0%} more") if touched else "nothing in memory about it to raise",
               subject=", ".join(named[:3]), request_id=request_id, user_id=user_id, now=now, db_path=db_path)
    except (OSError, ValueError, TypeError, ImportError) as failure:  # noqa: BLE001 - the thought store is never in the way of the signal
        log.debug("surprise thought not kept: %s", failure)
    return {"touched": touched, "gain": gain}


__all__ = ["GAIN", "SUBJECT_MEMORIES", "importance_gain", "note_surprise"]
