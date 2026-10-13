"""
eli/world/shown.py
──────────────────
What of ELI's own records stands in the rooms of his house.

The rooms are named for what he does: remembering, working, improving. This
puts the real thing in each: in the Memory Archive, the memories he has formed
lately, each a light that carries its own words; in the Upgrade Bay, the goals
he holds now. They are read from his memory and his goal store, never made up,
and they are not things he built: a body draws them, and may let the user pick
one up, but moving one changes nothing in him.

Reading his records for every poll of the world would be wasteful, so what is
shown is refreshed at most every half minute.

    items()     the things to show, as the world feed carries things
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Dict, List, Tuple

REFRESH_SECONDS = 30.0
MEMORIES, GOALS = 16, 8

_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])


def _spot(key: str, low: float, high: float) -> List[float]:
    """A steady place for a record: round the room, clear of straight ahead."""
    digest = hashlib.sha1(key.encode("utf-8")).digest()
    bearing = math.radians(40.0 + 280.0 * digest[0] / 255.0)
    radius = 3.2 + 3.0 * digest[1] / 255.0
    return [round(radius * math.sin(bearing), 2), round(low + (high - low) * digest[2] / 255.0, 2),
            round(radius * math.cos(bearing), 2)]


def _memories() -> List[Dict[str, Any]]:
    from eli.memory.memory import get_memory
    out = []
    for row in get_memory().get_recent_semantic_memories(limit=MEMORIES) or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get("text", "")).split())
        if not text:
            continue
        key = f"memory_{row.get('id', hashlib.sha1(text.encode()).hexdigest()[:10])}"
        out.append({
            "id": key, "name": " ".join(text.split()[:5]), "kind": "memory", "room": "memory_archive",
            "x": 0.0, "y": 0.0, "importance": 0.5, "meaning": text[:200], "made_by": "memory",
            "form": {"shape": "orb", "colour": "#9fd8ff", "size": 0.35, "glow": 0.8, "pos": _spot(key, 1.0, 2.6)},
        })
    return out[:MEMORIES]


def _goals() -> List[Dict[str, Any]]:
    from eli.planning.goal_store import summarize_goals
    out = []
    for title in ((summarize_goals() or {}).get("titles") or [])[:GOALS]:
        title = " ".join(str(title).split())
        if not title:
            continue
        key = f"goal_{hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}"
        out.append({
            "id": key, "name": title[:48], "kind": "goal", "room": "upgrade_bay",
            "x": 0.0, "y": 0.0, "importance": 0.7, "meaning": "A goal ELI holds now.", "made_by": "goal",
            "form": {"shape": "beacon", "colour": "#4fbf73", "size": 0.8, "glow": 0.7, "pos": _spot(key, 0.0, 0.0)},
        })
    return out


def items(now: float | None = None) -> List[Dict[str, Any]]:
    """The records to show. A source that cannot be read shows nothing of its own."""
    global _cache
    now = time.monotonic() if now is None else now
    if _cache[1] and now - _cache[0] < REFRESH_SECONDS:
        return _cache[1]
    found: List[Dict[str, Any]] = []
    for source in (_memories, _goals):
        try:
            found.extend(source())
        except Exception:
            continue
    _cache = (now, found)
    return found


def forget() -> None:
    global _cache
    _cache = (0.0, [])
