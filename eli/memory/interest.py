"""
eli/memory/interest.py
──────────────────────
What the user is interested in, measured the way memory is. The one estimator: the morning news,
the proactive daemon's focus and anything else that asks what the user cares about read it here.

A topic in the user's talk is a memory in theirs: its base-level activation (Anderson & Schooler,
1991; the same form the recall uses, eli/memory/policy.base_activation) is the sum of its mentions,
each faded as a power of its age, A = ln Σⱼ tⱼ^(−d), d = 0.5. The topics with the highest activation
are the user's focus; a topic's share of the total activation is how much of their attention it
holds.

Its own time scale. A person returns to a topic over days, not seconds: ages are counted in days
from one day up (TICK_DAYS), so a word said a minute ago weighs what one said this morning does, not
three hundred times what twenty mentions over the month do (with the recall's one-second floor the
last message's words were always "the focus"). And a minimum of evidence: a topic is an interest
once it came up on at least MIN_DAYS different days. One day's talk is one episode, however many
times a word was said in it, and the message being answered never makes its own words the user's
interest.

    focus(limit, user_id)   the user's current focus: topics with the highest activation, each with
                            its share of the total
    share(text, user_id)    the share of the user's attention the topics of a text hold, 0..1

Read from the user's own turns of the last LOOKBACK_DAYS (ELI's replies and its bookkeeping never
count), topic words as reflection reads them (eli/runtime/reflection.topic_words), kept
REFRESH_SECONDS between reads, per user and store.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

LOOKBACK_DAYS = 30.0            # as the proactive daemon read patterns
REFRESH_SECONDS = 60.0          # the user's talk is read again after this long
TICK_DAYS = 1.0                 # the time scale of returning to a topic: no mention is younger than a day
MIN_DAYS = 2                    # a topic is an interest once it came up on this many different days
_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, float], float]] = {}
_lock = threading.Lock()


def _store(memory: Any = None) -> Any:
    if memory is not None:
        return memory
    from eli.memory.memory import get_memory
    return get_memory()


def _mentions(user_id: str, now: float, memory: Any = None) -> Dict[str, List[float]]:
    """Each topic with the times the user mentioned it, from their own turns in the lookback."""
    from eli.runtime.reflection import topic_words
    out: Dict[str, List[float]] = {}
    since = now - LOOKBACK_DAYS * 86400.0
    turns = _store(memory).get_recent_conversation(limit=None, role="user", since=since,
                                                   user_id=user_id or None) or []
    for turn in turns:
        if str((turn or {}).get("role") or "").lower() != "user":
            continue
        ts = float(turn.get("timestamp") or turn.get("ts") or 0.0)
        if ts < since or ts > now:
            continue
        for word in topic_words(str(turn.get("content") or "")):
            out.setdefault(word, []).append(ts)
    return out


def _activations(user_id: str = "", *, now: Optional[float] = None, memory: Any = None) -> Tuple[Dict[str, float], float]:
    """Each topic's base-level activation over the user's mentions, and the total (over e^A), read
    together so one user's activations are never paired with another's total."""
    from eli.memory.policy import DAY, base_activation
    now = time.time() if now is None else now
    key = (str(user_id or ""), str(getattr(memory, "db_path", "") or "") if memory is not None else "")
    with _lock:
        slot = _cache.get(key)
        if slot is not None and now - slot[0] < REFRESH_SECONDS:
            return slot[1], slot[2]
    activation: Dict[str, float] = {}
    try:
        for word, times in _mentions(user_id, now, memory).items():
            if len({time.strftime("%Y-%m-%d", time.localtime(t)) for t in times}) < MIN_DAYS:
                continue
            ages = [max(TICK_DAYS, (now - t) / DAY) for t in times]
            a = base_activation(len(times), max(ages), ages)
            if a != -math.inf:
                activation[word] = a
    except Exception:
        log.debug("the user's talk unreadable for their interests", exc_info=True)
        activation = {}
    # shares are over e^A, the activations' own scale
    total = sum(math.exp(a) for a in activation.values())
    with _lock:
        _cache[key] = (now, activation, total)
    return activation, total


def forget() -> None:
    with _lock:
        _cache.clear()


def focus(limit: int = 6, user_id: str = "", *, now: Optional[float] = None, memory: Any = None) -> List[Tuple[str, float]]:
    """The user's current focus: the topics with the highest activation, each with its share of the
    total attention (e^A over the sum), most first."""
    activation, total = _activations(user_id, now=now, memory=memory)
    if not activation or total <= 0.0:
        return []
    ranked = sorted(activation.items(), key=lambda kv: kv[1], reverse=True)[:int(limit)]
    return [(word, round(math.exp(a) / total, 4)) for word, a in ranked]


def share(text: Any, user_id: str = "", *, now: Optional[float] = None, memory: Any = None) -> float:
    """The share of the user's attention the topics of `text` hold, 0..1."""
    from eli.runtime.reflection import topic_words
    words = topic_words(str(text or ""))
    if not words:
        return 0.0
    activation, total = _activations(user_id, now=now, memory=memory)
    if not activation or total <= 0.0:
        return 0.0
    held = sum(math.exp(activation[w]) for w in words if w in activation)
    return round(min(1.0, held / total), 4)


__all__ = ["LOOKBACK_DAYS", "MIN_DAYS", "REFRESH_SECONDS", "TICK_DAYS", "focus", "forget", "share"]
