"""
eli/world/shown.py
──────────────────
What of ELI's own records stands in the rooms of its house.

The rooms are named for what ELI does: remembering, working, improving. This
puts the real thing in each: in the Memory Archive, the memories ELI has formed
lately, each a light that carries its own words; in the Upgrade Bay, the goals
ELI holds now; on the Evidence Wall, what ELI believes about the user, each with
when it learned it (a contested belief shows as one); in the Anomaly Room, where
two things ELI was told disagree, with the question that would settle it. What ELI
did for the user lately stands there too: what it found (a web search, the news, the
weather) pinned up on the Evidence Wall, what it wrote in the Workshop, and what is
playing on the Core Room's radio, each with what it says ("lines"). A room shows as
many as it holds (building.MAX_PER_ROOM), the newest and surest first. They are read
from ELI's memory, goal store and evidence ledger, never made up, and they are not
things ELI built: a body draws them, and may let the user pick one up, but moving one
changes nothing in ELI.

Reading ELI's records for every poll of the world would be wasteful, so what is
shown is refreshed at most every half minute.

    items()     the things to show, as the world feed carries things
    thoughts()  what is on ELI's mind now, in its records' own words, for a body to let rise from ELI
    coming_up() what is on the user's agenda in the next day and a half: events, reminders, alarms
    free()      whether ELI is free to make something of its own now (eli/world/urges.py)
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

REFRESH_SECONDS = 30.0
MEMORIES, GOALS = 16, 8
# What ELI did for the user, by what came of it, and where in the house it stands.
DONE = {
    "WEB_SEARCH": "found", "NEWS_FETCH": "found", "GET_WEATHER": "found",
    "CREATE_DOCUMENT": "written", "GENERATE_DOCUMENT": "written", "WRITE_DOCUMENT": "written",
    "CONVERT_DOCUMENT": "written", "CREATE_FILE": "written",
    "PLAY_MEDIA": "playing", "NOW_PLAYING": "playing", "NEXT_MEDIA": "playing", "PREVIOUS_MEDIA": "playing",
}
STOPPED = ("PAUSE_MEDIA", "STOP_MEDIA")
ROOM_OF = {"found": "evidence_wall", "written": "workshop", "playing": "core_room"}
KEPT = {"found": 6, "written": 6, "playing": 1}
LINES = 8                   # what one printed sheet holds

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


def _claims() -> List[Dict[str, Any]]:
    from eli.memory.claims import describe
    from eli.memory.memory import get_memory
    from eli.world.building import MAX_PER_ROOM
    rows = [r for r in get_memory().claims_current() or [] if isinstance(r, dict) and str(r.get("value", "")).strip()]
    rows.sort(key=lambda r: (float(r.get("recorded_at") or 0.0), float(r.get("confidence") or 0.0)), reverse=True)
    out = []
    for row in rows[:MAX_PER_ROOM]:
        key = f"claim_{row.get('id')}"
        contested = bool(row.get("contested"))
        sure = max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
        out.append({
            "id": key, "name": f"{str(row.get('relation', '')).replace('_', ' ')}: {row.get('value')}"[:48],
            "kind": "claim", "room": "evidence_wall", "x": 0.0, "y": 0.0, "importance": sure,
            "meaning": describe(row)[:200], "made_by": "claims",
            "form": {"shape": "board", "colour": "#f5b942" if contested else "#d9e4f2", "size": 0.6,
                     "glow": round(0.3 + 0.6 * sure, 2), "pos": _spot(key, 0.9, 2.1)},
        })
    return out


def _conflicts() -> List[Dict[str, Any]]:
    from eli.memory.claims import conflict_question
    from eli.memory.memory import get_memory
    from eli.world.building import MAX_PER_ROOM
    out = []
    for conflict in (get_memory().claim_conflicts() or [])[:MAX_PER_ROOM]:
        relation = str(conflict.get("relation", ""))
        key = f"conflict_{hashlib.sha1(relation.encode('utf-8')).hexdigest()[:10]}"
        out.append({
            "id": key, "name": relation.replace("_", " ").capitalize()[:47] + "?", "kind": "conflict",
            "room": "anomaly_room", "x": 0.0, "y": 0.0, "importance": 0.9,
            "meaning": conflict_question(conflict)[:200], "made_by": "claims",
            "form": {"shape": "crystal", "colour": "#ff6a4d", "size": 0.7, "glow": 0.9, "pos": _spot(key, 0.4, 1.6)},
        })
    return out


def _insight() -> List[Dict[str, str]]:
    from eli.planning.insight_synthesis import get_cached_insight
    insight = " ".join(str(get_cached_insight() or "").split())
    return [{"kind": "insight", "text": insight[:200]}] if insight else []


def _moments() -> List[Dict[str, str]]:
    from eli.world import urges
    return [{"kind": str(u.get("kind", "moment")), "text": str(u.get("text", ""))[:200]} for u in urges.waiting()[:3]]


def _first_goal() -> List[Dict[str, str]]:
    from eli.planning.goal_store import summarize_goals
    titles = [str(t) for t in (summarize_goals() or {}).get("titles") or [] if str(t).strip()]
    return [{"kind": "goal", "text": titles[0][:200]}] if titles else []


def _tone() -> List[Dict[str, str]]:
    from eli.cognition import tone_adaptor
    tone = str(tone_adaptor.current_tone().get("tone") or "")
    return [{"kind": "tone", "text": tone}] if tone and tone != "neutral" else []


def _mind() -> List[Dict[str, str]]:
    """Its latest insight, the moments of its own waiting to be made into something, the goal
    it holds first, and its tone. Each only if the record is there; one that cannot be read
    gives nothing of its own."""
    out: List[Dict[str, str]] = []
    for source in (_insight, _moments, _first_goal, _tone):
        try:
            out.extend(source())
        except Exception:
            continue
    return [t for t in out if t["text"].strip()]


_thoughts_cache: Tuple[float, List[Dict[str, str]]] = (0.0, [])
_coming_cache: Tuple[float, List[Dict[str, Any]]] = (-REFRESH_SECONDS, [])
AHEAD_HOURS = 36            # how far ahead the agenda a room shows reaches, as ELI's own prompt does
_free_cache: Tuple[float, bool] = (-REFRESH_SECONDS, False)


def coming_up(now: float | None = None) -> List[Dict[str, Any]]:
    """What is on the user's agenda soon, earliest first, each {"when": unix time, "said": "today (Mon 5
    Oct) at 19:30", "what": title, "kind": event | reminder}. From ELI's own agenda, never made up."""
    global _coming_cache
    clock = time.monotonic() if now is None else now
    if clock - _coming_cache[0] < REFRESH_SECONDS:
        return _coming_cache[1]
    out: List[Dict[str, Any]] = []
    try:
        from eli.kernel.state import get_active_user_id
        from eli.runtime import agenda
        start = time.time()
        for row in agenda.between(start, start + AHEAD_HOURS * 3600, user_id=str(get_active_user_id() or "")):
            when = float(row.get("start_ts") or 0.0)
            out.append({"when": when, "said": agenda.describe(when, has_time=bool(row.get("has_time", 1))),
                        "what": _plain(row.get("title") or ""), "kind": str(row.get("kind") or "event")})
    except Exception:
        out = []
    _coming_cache = (clock, out)
    return out


def thoughts(now: float | None = None) -> List[Dict[str, str]]:
    """What is on ELI's mind now, refreshed as often as the records in the rooms."""
    global _thoughts_cache
    now = time.monotonic() if now is None else now
    if _thoughts_cache[1] and now - _thoughts_cache[0] < REFRESH_SECONDS:
        return _thoughts_cache[1]
    _thoughts_cache = (now, _mind())
    return _thoughts_cache[1]


def free(now: float | None = None) -> bool:
    """Whether ELI is free to make something of its own, read as often as its records."""
    global _free_cache
    now = time.monotonic() if now is None else now
    if now - _free_cache[0] < REFRESH_SECONDS:
        return _free_cache[1]
    try:
        from eli.world import urges
        _free_cache = (now, bool(urges.free()))
    except Exception:
        _free_cache = (now, False)
    return _free_cache[1]


def _plain(text: Any) -> str:
    """Text as it reads on paper: no markdown, links as their words, one line of spaces."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text or ""))
    text = re.sub(r"[*_`#>]+", "", text)
    return " ".join(text.split())


def _text_lines(text: Any) -> List[str]:
    out = []
    for line in str(text or "").splitlines():
        line = _plain(re.sub(r"^\s*(?:[-•*]|\d+[.)])\s+", "", line))
        if line:
            out.append(line)
    return out[:LINES]


def _site(url: Any) -> str:
    host = urlparse(str(url or "")).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _told(action: str, args: Dict[str, Any], result: Dict[str, Any]) -> Tuple[str, List[str]]:
    """What a sheet or a radio says of one thing ELI did: its title and its lines."""
    content = result.get("readback") or result.get("content") or result.get("response") or ""
    if action == "WEB_SEARCH":
        lines: List[str] = []
        for found in (result.get("results") or [])[: LINES // 2]:
            if isinstance(found, dict):
                lines.append(_plain(found.get("title") or found.get("name") or ""))
                site = _site(found.get("url") or found.get("href") or found.get("link"))
                if site:
                    lines.append(f"    {site}")
        return f"Searched: {_plain(result.get('query') or args.get('query') or '')}", [l for l in lines if l.strip()]
    if action == "NEWS_FETCH":
        topic = _plain(args.get("topic") or "")
        return (f"The news: {topic}" if topic else "The news"), _text_lines(content)
    if action == "GET_WEATHER":
        place = _plain(args.get("location") or args.get("place") or args.get("city") or "")
        return (f"Weather: {place}" if place else "The weather"), _text_lines(content)
    if DONE.get(action) == "written":
        named = result.get("filename") or result.get("path") or result.get("doc_path") or args.get("path") or ""
        title = os.path.basename(str(named)) or _plain(args.get("topic") or "") or "A document"
        return title, _text_lines(content)
    media = result.get("media") if isinstance(result.get("media"), dict) else {}
    title = _plain(media.get("title") or "")
    if title:
        return title, [_plain(x) for x in (media.get("artist"), media.get("album")) if _plain(x)]
    playing = re.sub(r"^[^:]*playing:\s*", "", _plain(content), flags=re.I)
    title, _, where = playing.partition(" — ")
    return title or "Something is playing", ([f"on {where}"] if where else [])


def what_came_of(action: str, args: Dict[str, Any], result: Dict[str, Any]) -> str:
    """One line on what ELI did for the user, for its world to know it in words: "Searched: when do
    the tides turn: Tide times explained". "" for an action that is not one done for the user."""
    action = str(action or "").upper()
    if action not in DONE or not isinstance(result, dict):
        return ""
    title, lines = _told(action, dict(args or {}), result)
    return ": ".join(x for x in (title, (lines[0].strip() if lines else "")) if x)[:200]


def _results() -> List[Dict[str, Any]]:
    from eli.kernel.state import get_active_user_id
    from eli.runtime.evidence_ledger import execution_outcome, recent_events
    owner = str(get_active_user_id() or "")
    rows = [r for r in recent_events(event_type="executor_action", limit=400)
            if str(r.get("user_id") or "") in ("", owner)]
    out: List[Dict[str, Any]] = []
    counted: Dict[str, int] = {}
    stopped_after = False            # the newest music action was a pause or a stop
    seen_music = False
    for row in rows:                 # newest first
        action = str(row.get("action") or "").upper()
        if action in STOPPED and not seen_music:
            stopped_after = True
            seen_music = True
            continue
        kind = DONE.get(action)
        payload = row.get("payload") or {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if not kind or counted.get(kind, 0) >= KEPT[kind] or execution_outcome(result) != "ok":
            continue
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        title, lines = _told(action, args, result)
        if kind == "playing":
            seen_music = True
            lines = lines + (["paused"] if stopped_after else [])
        counted[kind] = counted.get(kind, 0) + 1
        key = "playing" if kind == "playing" else f"{kind}_{row.get('id')}"
        out.append({
            "id": key, "name": title[:48], "kind": kind, "room": ROOM_OF[kind], "x": 0.0, "y": 0.0,
            "importance": 0.6, "meaning": " · ".join(lines)[:200], "lines": lines, "made_by": action.lower(),
            "when": float(row.get("timestamp") or 0.0),
            "form": {"shape": "board" if kind != "playing" else "lamp", "colour": "#e8e2d4", "size": 0.6,
                     "glow": 0.4, "pos": _spot(key, 0.9, 2.1)},
        })
    return out


def items(now: float | None = None) -> List[Dict[str, Any]]:
    """The records to show. A source that cannot be read shows nothing of its own."""
    global _cache
    now = time.monotonic() if now is None else now
    if _cache[1] and now - _cache[0] < REFRESH_SECONDS:
        return _cache[1]
    found: List[Dict[str, Any]] = []
    for source in (_memories, _goals, _claims, _conflicts, _results):
        try:
            found.extend(source())
        except Exception:
            continue
    _cache = (now, found)
    return found


def forget() -> None:
    global _cache, _thoughts_cache, _free_cache, _coming_cache
    _cache = (0.0, [])
    _thoughts_cache = (0.0, [])
    _coming_cache = (-REFRESH_SECONDS, [])
    _free_cache = (-REFRESH_SECONDS, False)
