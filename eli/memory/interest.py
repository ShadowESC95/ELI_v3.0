"""
eli/memory/interest.py
──────────────────────
What the user is interested in, measured the way memory is.

The user's interests were counts: a topic word mentioned in at least two messages in the last
thirty days, the last week counted twice (eli/planning/proactive_daemon.py), and a fixed boost of
0.15 in the recall for a memory tagged as a preference. A count cannot tell ten mentions spread over
months from one yesterday, and the boost knew nothing of what the user talks about. A topic in the
user's talk is a memory in theirs: its base-level activation (Anderson & Schooler, 1991; the same
form the recall uses, eli/memory/policy.base_activation) is the sum of its mentions, each faded as a
power of its age, A = ln Σⱼ tⱼ^(−d), d = 0.5. The topics with the highest activation are the user's
focus; a topic's share of the total activation is how much of their attention it holds.

    focus(limit, user_id)   the user's current focus: topics with the highest activation, each with
                            its share of the total
    share(text, user_id)    the share of the user's attention the topics of a text hold, 0..1;
                            what a new memory about them takes into its importance, and what the
                            recall gives a memory in place of the fixed preference boost

Read from the user's own turns of the last LOOKBACK_DAYS (ELI's replies and its bookkeeping never
count), topic words as reflection reads them (eli/runtime/reflection.topic_words), kept
REFRESH_SECONDS between reads.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

LOOKBACK_DAYS = 30.0            # as the proactive daemon read patterns
REFRESH_SECONDS = 60.0          # the user's talk is read again after this long
TURNS = 500                     # the user's turns read at once: a month of talk for a busy user
_cache: Dict[str, Any] = {"at": 0.0, "user": None, "activation": {}, "total": 0.0}


def _mentions(user_id: str, now: float) -> Dict[str, List[float]]:
    """Each topic with the times the user mentioned it, from their own turns."""
    from eli.memory.memory import get_memory
    from eli.runtime.reflection import topic_words
    out: Dict[str, List[float]] = {}
    since = now - LOOKBACK_DAYS * 86400.0
    turns = get_memory().get_recent_conversation(limit=TURNS, user_id=user_id or None) or []
    for turn in turns:
        if str((turn or {}).get("role") or "").lower() != "user":
            continue
        ts = float(turn.get("timestamp") or turn.get("ts") or 0.0)
        if ts < since:
            continue
        for word in topic_words(str(turn.get("content") or "")):
            out.setdefault(word, []).append(ts)
    return out


def _activations(user_id: str = "", *, now: float | None = None) -> Dict[str, float]:
    """Each topic's base-level activation over the user's mentions."""
    now = time.time() if now is None else now
    if _cache["user"] == user_id and now - float(_cache["at"]) < REFRESH_SECONDS:
        return _cache["activation"]
    from eli.memory.policy import base_activation
    activation: Dict[str, float] = {}
    try:
        for word, times in _mentions(user_id, now).items():
            ages = [(now - t) / 86400.0 for t in times]
            a = base_activation(len(times), max(ages) if ages else 0.0, ages)
            if a != -math.inf:
                activation[word] = a
    except Exception:
        log.debug("the user's talk unreadable for their interests", exc_info=True)
        activation = {}
    # shares are over e^A, the activations' own scale
    total = sum(math.exp(a) for a in activation.values())
    _cache.update({"at": now, "user": user_id, "activation": activation, "total": total})
    return activation


def forget() -> None:
    _cache["at"] = 0.0


def focus(limit: int = 6, user_id: str = "", *, now: float | None = None) -> List[Tuple[str, float]]:
    """The user's current focus: the topics with the highest activation, each with its share of the
    total attention (e^A over the sum), most first."""
    activation = _activations(user_id, now=now)
    total = float(_cache["total"] or 0.0)
    if not activation or total <= 0.0:
        return []
    ranked = sorted(activation.items(), key=lambda kv: kv[1], reverse=True)[:int(limit)]
    return [(word, round(math.exp(a) / total, 4)) for word, a in ranked]


def share(text: Any, user_id: str = "", *, now: float | None = None) -> float:
    """The share of the user's attention the topics of `text` hold, 0..1."""
    from eli.runtime.reflection import topic_words
    words = topic_words(str(text or ""))
    if not words:
        return 0.0
    activation = _activations(user_id, now=now)
    total = float(_cache["total"] or 0.0)
    if not activation or total <= 0.0:
        return 0.0
    held = sum(math.exp(activation[w]) for w in words if w in activation)
    return round(min(1.0, held / total), 4)


__all__ = ["LOOKBACK_DAYS", "REFRESH_SECONDS", "TURNS", "focus", "forget", "share"]
