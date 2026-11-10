"""
eli/cognition/curiosity.py
──────────────────────────
What ELI is curious about, measured.

ELI's curiosity was a bar nudged up by a tenth on each reflection and down with idle time, and the
moments of its own it acted on were taken oldest first. Neither said what ELI found interesting.
Curiosity, in the account this follows (Loewenstein's information gap; Schmidhuber's learning
progress), is what has recently surprised a mind and is not yet settled: the subjects where its
predictions missed (eli/memory/salience.py keeps each surprise as a thought with its size) and the
questions still open (a claim in dispute). That is measurable from ELI's own records, so it is.

    about(text)       ELI's curiosity about what a text is about: the surprises about its subjects,
                      each weighed by its recency (half-life HALF_LIFE_DAYS), plus one for each open
                      conflict about them; 0 when nothing there has surprised ELI
    subjects(limit)   the subjects ELI is most curious about now, each with its curiosity
    sitting(reads)    the sitting's curiosity for the world's awareness bar: the running mean of
                      the surprises' sizes (eli/world/agency/autonomy_engine.py keeps the mean)

The world's urges are acted on in order of curiosity (eli/world/urges.py), not of age.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

# A surprise weighs half as much after a week: the shared recency weight (epistemics.recency_weight).
from eli.cognition.epistemics import HALF_LIFE_DAYS, recency_weight  # noqa: E402
LOOKBACK_DAYS = 30.0            # how far back surprises count, as the ledger reads runs
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
_STOP = frozenset("the a an and or of to in on for with at by from about this that these those it its is are was were "
                  "be been you your me my we our i they them their what which who how why when where did do does".split())


def _words(text: Any) -> List[str]:
    return [w for w in _WORD.findall(str(text or "").lower()) if w not in _STOP]


def _surprises(now: float) -> List[Dict[str, Any]]:
    from eli.cognition.thoughts import recent
    return recent(200, kinds=["surprise"], since=now - LOOKBACK_DAYS * 86400.0)


def _weight(ts: float, now: float) -> float:
    return recency_weight(ts, now, HALF_LIFE_DAYS)


def _open_conflicts() -> List[str]:
    try:
        from eli.memory.memory import get_memory
        out = []
        for conflict in get_memory().claim_conflicts() or []:
            for side in ("standing", "disputed"):
                out += [str(r.get("value") or "") for r in conflict.get(side) or []]
            out.append(str(conflict.get("relation") or "").replace("_", " "))
        return [c for c in out if c]
    except Exception:
        return []


def about(text: Any, *, now: float | None = None) -> float:
    """ELI's curiosity about what `text` is about."""
    words = set(_words(text))
    if not words:
        return 0.0
    now = time.time() if now is None else now
    total = 0.0
    try:
        for thought in _surprises(now):
            subject = set(_words(thought.get("subject") or "")) | set(_words(thought.get("what") or ""))
            if words & subject:
                total += _weight(float(thought.get("ts") or 0.0), now) * float(thought.get("chance") or 0.0)
    except (OSError, ValueError, TypeError, ImportError):
        log.debug("surprises unreadable", exc_info=True)
    for conflict in _open_conflicts():
        if words & set(_words(conflict)):
            total += 1.0
    return round(total, 4)


def subjects(limit: int = 6, *, now: float | None = None) -> List[Tuple[str, float]]:
    """The subjects ELI is most curious about now, each with its curiosity, most first."""
    now = time.time() if now is None else now
    tally: Dict[str, float] = {}
    try:
        for thought in _surprises(now):
            for subject in [s.strip() for s in str(thought.get("subject") or "").split(",") if s.strip()][:3]:
                tally[subject.lower()] = tally.get(subject.lower(), 0.0) + _weight(float(thought.get("ts") or 0.0), now) * float(thought.get("chance") or 0.0)
    except (OSError, ValueError, TypeError, ImportError):
        log.debug("surprises unreadable", exc_info=True)
    for conflict in _open_conflicts():
        tally[conflict.lower()] = tally.get(conflict.lower(), 0.0) + 1.0
    return sorted(((s, round(v, 4)) for s, v in tally.items()), key=lambda kv: kv[1], reverse=True)[:int(limit)]


def sitting(magnitudes: List[float]) -> float:
    """The sitting's curiosity: the mean size of its surprises, 0 with none."""
    sizes = [max(0.0, min(1.0, float(m))) for m in (magnitudes or [])]
    return sum(sizes) / len(sizes) if sizes else 0.0


__all__ = ["HALF_LIFE_DAYS", "LOOKBACK_DAYS", "about", "sitting", "subjects"]
