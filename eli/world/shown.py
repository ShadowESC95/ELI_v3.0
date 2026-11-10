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
weather) pinned up on the Evidence Wall, the pictures it took or looked at beside them
(served by their ledger row, picture()), what it wrote in the Workshop, and what is
playing on the Core Room's radio, each with what it says ("lines"). ELI's thoughts
lately (eli/cognition/thoughts.py: what it recalled, believed, read a sentence as, made,
tried, proposed, let go, and the notes it wrote) hang as sheets in the rooms they belong
to, the newest NOTES_PER_ROOM of each room, each written with the thought, what ELI
decided and why, its chance and what became of it: the user may pick one up and ask ELI
about it (eli/world/embodiment.py, held_record). A room shows as many records as it
holds (building.MAX_PER_ROOM), the newest and surest first. They are read from ELI's
memory, goal store, evidence ledger and thoughts, never made up, and they are not
things ELI built: a body draws them, and may let the user pick one up, but moving one
changes nothing in ELI.

Reading ELI's records for every poll of the world would be wasteful, so what is
shown is refreshed at most every half minute.

    items()     the things to show, as the world feed carries things; a belief carries its claim (what it
                is, its chance, since when, its basis and the record it rests on), a dispute its relation,
                a memory when it was kept
    books(room) ELI's books (eli/world/books.py) as things of kind "book", as many as `room` bytes of the
                message hold, the newest of each room first, the text of as many as still fit whole
    evidence()  what ELI's actions did and how often its figures proved right, week by week, for the
                Evidence Wall's scoreboard and charts (action_outcomes.report, epistemics.measured)
    thoughts()  what is on ELI's mind now, in its records' own words, for a body to let rise from ELI
    coming_up() what is on the user's agenda in the next day and a half: events, reminders, alarms
    free()      whether ELI is free to make something of its own now (eli/world/urges.py)
    kept()      the thoughts ELI has had lately, with why, how sure, what was decided and what became
                of them (eli/cognition/thoughts.py), for the rooms' screens
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from eli.utils.log import get_logger

log = get_logger(__name__)

REFRESH_SECONDS = 30.0
MEMORIES, GOALS = 16, 8
# What ELI did for the user, by what came of it, and where in the house it stands.
DONE = {
    "WEB_SEARCH": "found", "NEWS_FETCH": "found", "GET_WEATHER": "found",
    "CREATE_DOCUMENT": "written", "GENERATE_DOCUMENT": "written", "WRITE_DOCUMENT": "written",
    "CONVERT_DOCUMENT": "written", "CREATE_FILE": "written",
    "PLAY_MEDIA": "playing", "NOW_PLAYING": "playing", "NEXT_MEDIA": "playing", "PREVIOUS_MEDIA": "playing",
    "SCREENSHOT": "seen", "ANALYZE_IMAGE": "seen", "OCR_IMAGE": "seen",
}
# What a picture ELI took or looked at may be served as, read from the file's first bytes.
PICTURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))
STOPPED = ("PAUSE_MEDIA", "STOP_MEDIA")
ROOM_OF = {"found": "evidence_wall", "written": "workshop", "playing": "core_room", "seen": "evidence_wall"}
KEPT = {"found": 6, "written": 6, "playing": 1, "seen": 4}
LINES = 8                   # what one printed sheet holds
NOTES_PER_ROOM = 6          # thought sheets a room hangs up: the newest of those that belong there
# A thought sheet's paper by the thought's kind, as the rooms' screens colour the kinds.
THOUGHT_COLOURS = {"action": "#ffb347", "surprise": "#ffb347", "conflict": "#ffb347", "act": "#8fe8a8",
                   "recall": "#b9a4ff", "memory": "#b9a4ff", "claim": "#d9e4f2", "route": "#d9e4f2",
                   "proposal": "#d9e4f2", "diagnosis": "#d9e4f2", "note": "#f5e6c8"}

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
            **({"when": _memory_time(row)} if _memory_time(row) is not None else {}),
            "form": {"shape": "orb", "colour": "#9fd8ff", "size": 0.35, "glow": 0.8, "pos": _spot(key, 1.0, 2.6)},
        })
    return out[:MEMORIES]


def _memory_time(row: Dict[str, Any]):
    """When a memory was kept, as its row says; None for a row that holds no time (the memory store reads
    a row with none by its id, which is not a time)."""
    try:
        when = float(row.get("timestamp"))
    except (TypeError, ValueError):
        return None
    return when if row.get("timestamp") != row.get("id") and when > 0 else None


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
    from eli.memory.claims import basis, chance, describe, origin_words
    from eli.memory.memory import get_memory
    from eli.world.building import MAX_PER_ROOM
    memory = get_memory()
    rows = [r for r in memory.claims_current() or [] if isinstance(r, dict) and str(r.get("value", "")).strip()]
    rows.sort(key=lambda r: (float(r.get("recorded_at") or 0.0), float(r.get("confidence") or 0.0)), reverse=True)
    out = []
    for row in rows[:MAX_PER_ROOM]:
        key = f"claim_{row.get('id')}"
        contested = bool(row.get("contested"))
        # the chance the claim is right (its sureness calibrated on what became of such claims), on the sheet
        try:
            sure = max(0.0, min(1.0, float(chance(row).p)))
        except (OSError, ValueError, ImportError):
            sure = max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
        out.append({
            "id": key, "name": f"{str(row.get('relation', '')).replace('_', ' ')}: {row.get('value')}"[:48],
            "kind": "claim", "room": "evidence_wall", "x": 0.0, "y": 0.0, "importance": sure,
            "meaning": describe(row)[:200], "made_by": "claims",
            "claim": {"relation": str(row.get("relation") or ""), "value": str(row.get("value") or ""),
                      "chance": round(sure, 4), "sure": float(row.get("confidence") or 0.0),
                      "since": row.get("valid_from"), "until": row.get("valid_to"), "learned": row.get("recorded_at"),
                      "origin": str(row.get("origin") or ""), "basis": basis(row),
                      "confirmations": int(row.get("confirmations") or 1), "contested": contested,
                      "status": str(row.get("status") or ""),
                      "sources": _claim_sources(memory, row, origin_words(row.get("origin")))},
            "form": {"shape": "board", "colour": "#f5b942" if contested else "#d9e4f2", "size": 0.6,
                     "glow": round(0.3 + 0.6 * sure, 2), "pos": _spot(key, 0.9, 2.1)},
        })
    return out


def _claim_sources(memory: Any, row: Dict[str, Any], origin: str) -> List[Dict[str, Any]]:
    """The record a claim was learned from (its source memory, memory.memory_facts), as an exhibit;
    [] for a claim with none, or one whose memory is gone."""
    source = row.get("source_memory_id")
    if not str(source or "").lstrip("-").isdigit() or int(source) <= 0:
        return []
    try:
        facts = memory.memory_facts(int(source)) or {}
    except Exception:  # noqa: BLE001 - the memory unreadable: the claim rests on nothing shown
        log.debug("a claim's source memory was not read", exc_info=True)
        return []
    if not str(facts.get("text") or "").strip():
        return []
    return [{"id": f"memory_{int(source)}", "kind": "memory", "text": str(facts["text"])[:200],
             "when": facts.get("when") or None, "origin": origin}]


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
            "room": "anomaly_room", "x": 0.0, "y": 0.0, "importance": 0.9, "relation": relation,
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
# the thoughts last read, per reader (thoughts.for_feed's user_id; "" the turn's own, the owner outside one)
_kept_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
THOUGHTS = 60               # the latest thoughts a body is handed, across the rooms
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


def kept(now: float | None = None, *, room: str = "", kinds: List[str] | None = None, limit: int = THOUGHTS,
         user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The thoughts ELI has had lately (eli/cognition/thoughts.py), each with why, its chance, what was
    decided and what became of it, for the rooms' screens; refreshed as often as the records. With a
    room or kinds, only those, read fresh. `user_id` is whose they are (thoughts.for_feed: a member's
    own and ELI's shared ones; None the turn's user, the owner outside a turn), each reader's kept apart."""
    clock = time.monotonic() if now is None else now
    if room or kinds:
        try:
            from eli.cognition.thoughts import for_feed
            out = [t for t in for_feed(limit, user_id=user_id)
                   if (not room or t["room"] == room) and (not kinds or t["kind"] in kinds)]
        except Exception:
            out = []
        return out
    reader = str(user_id or "")
    held = _kept_cache.get(reader)
    if held and clock - held[0] < REFRESH_SECONDS:
        return held[1]
    try:
        from eli.cognition.thoughts import for_feed
        _kept_cache[reader] = (clock, for_feed(limit, user_id=user_id))
    except Exception:
        _kept_cache[reader] = (clock, [])
    return _kept_cache[reader][1]


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
    if DONE.get(action) == "seen":
        named = os.path.basename(str(result.get("path") or args.get("path") or args.get("file") or ""))
        title = "A screenshot" if action == "SCREENSHOT" else (f"Looked at: {named}" if named else "A picture")
        return title, _text_lines(content)
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


def _picture_file(row: Dict[str, Any]) -> str:
    """The picture a "seen" ledger row is of, when it is still there and is a picture; else ""."""
    payload = row.get("payload") or {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    named = str(result.get("path") or args.get("path") or args.get("file") or "").strip()
    if not named or DONE.get(str(row.get("action") or "").upper()) != "seen":
        return ""
    path = os.path.realpath(os.path.expanduser(named))
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return ""
    return path if picture_type(head) else ""


def picture_type(head: bytes) -> str:
    """The media type of a picture from its first bytes ("" for anything that is not one)."""
    for magic, kind in PICTURES:
        if head.startswith(magic) and (kind != "image/webp" or head[8:12] == b"WEBP"):
            return kind
    return ""


def picture(row_id: Any) -> str:
    """The file of a picture ELI took or looked at for the owner, by its ledger row, for a body to
    show; "" unless the ledger records exactly that and the file is still a picture."""
    from eli.kernel.state import get_active_user_id
    from eli.runtime.evidence_ledger import recent_events
    owner = str(get_active_user_id() or "")
    for row in recent_events(event_type="executor_action", limit=400):
        if str(row.get("id")) == str(row_id) and str(row.get("user_id") or "") in ("", owner):
            return _picture_file(row)
    return ""


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
        shows = _picture_file(row) if kind == "seen" else ""
        if kind == "seen" and not shows:
            continue                 # a picture that is gone, or never was one, is not shown
        counted[kind] = counted.get(kind, 0) + 1
        key = "playing" if kind == "playing" else f"{kind}_{row.get('id')}"
        out.append({
            "id": key, "name": title[:48], "kind": kind, "room": ROOM_OF[kind], "x": 0.0, "y": 0.0,
            "importance": 0.6, "meaning": " · ".join(lines)[:200], "lines": lines, "made_by": action.lower(),
            "when": float(row.get("timestamp") or 0.0),
            "form": {"shape": "board" if kind != "playing" else "lamp", "colour": "#e8e2d4", "size": 0.6,
                     "glow": 0.4, "pos": _spot(key, 0.9, 2.1)},
            **({"picture": f"/v1/world/picture/{row.get('id')}"} if shows else {}),
        })
    return out


def thought_lines(thought: Dict[str, Any]) -> List[str]:
    """What a thought's sheet says, line by line: the thought, what ELI decided and why, its chance
    and what became of it, what brought it up, and when it was had."""
    lines = [_plain(thought.get("what") or "")]
    decision = str(thought.get("decision") or "").strip()
    reason = str(thought.get("reason") or "").strip()
    if decision:
        lines.append(decision + (f": {reason}" if reason else ""))
    tail = []
    if thought.get("chance") is not None:
        try:
            tail.append(f"chance {float(thought['chance']):.0%}")
        except (TypeError, ValueError):
            pass
    outcome = str(thought.get("outcome") or "").strip()
    if outcome:
        how = str(thought.get("how") or "").strip()
        tail.append(f"proved {outcome}" + (f" ({how})" if how else ""))
    if tail:
        lines.append(" · ".join(tail))
    why = [str(w).strip() for w in (thought.get("why") or []) if str(w).strip()]
    if why:
        lines.append("because " + "; ".join(why[:3]))
    try:
        lines.append(time.strftime("%a %d %b %H:%M", time.localtime(float(thought.get("ts") or 0.0))))
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return [line for line in lines if line][:LINES]


def _thought_notes() -> List[Dict[str, Any]]:
    """ELI's thoughts lately as sheets in the rooms they belong to, the newest NOTES_PER_ROOM of
    each room, each with its lines and the thought's own measures (kind, chance, decision, status,
    outcome) for a body that draws them by kind."""
    from eli.cognition.thoughts import recent, room_of
    counted: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for thought in recent(THOUGHTS):
        kind = str(thought.get("kind") or "")
        room = str(thought.get("room") or room_of(kind))
        if counted.get(room, 0) >= NOTES_PER_ROOM:
            continue
        counted[room] = counted.get(room, 0) + 1
        key = f"thought_{thought['id']}"
        chance = thought.get("chance")
        sure = max(0.0, min(1.0, float(chance))) if chance is not None else 0.5
        out.append({
            "id": key, "name": f"{kind}: {_plain(thought.get('what') or '')}"[:48], "kind": "thought", "room": room,
            "x": 0.0, "y": 0.0, "importance": sure, "meaning": str(thought.get("words") or "")[:200],
            "lines": thought_lines(thought), "made_by": "thoughts", "when": float(thought.get("ts") or 0.0),
            "thought": {"kind": kind, "chance": chance, "decision": str(thought.get("decision") or ""),
                        "status": str(thought.get("status") or ""), "outcome": str(thought.get("outcome") or "")},
            "form": {"shape": "board", "colour": THOUGHT_COLOURS.get(kind, "#d9e4f2"), "size": 0.6,
                     "glow": round(0.3 + 0.6 * sure, 2), "pos": _spot(key, 0.9, 2.1)},
        })
    return out


# ── what ELI did and how often it was right, for the Evidence Wall ─────────────────────────────────

# Figures go to a body to the hundredth (a whole percent, as a body shows them), so the message
# changes when what a body would show does, not every time a recency weight moves.
FIGURE_DIGITS = 2
_evidence_cache: Tuple[float, Dict[str, Any]] = (-REFRESH_SECONDS, {})


def _figure(value: Any) -> Optional[float]:
    try:
        return round(float(value), FIGURE_DIGITS)
    except (TypeError, ValueError):
        return None


def _action_row(row: Dict[str, Any]) -> Dict[str, Any]:
    from eli.runtime.action_outcomes import cause_words, plain
    causes = sorted((row.get("causes") or {}).items(), key=lambda kv: -kv[1])
    return {"action": str(row["action"]), "name": plain(row["action"]), "n": int(row["n"]), "ok": int(row["ok"]),
            "p": _figure(row.get("p")),
            "causes": [{"cause": str(c), "words": cause_words(c), "n": int(k)} for c, k in causes],
            "worst_target": str(row.get("worst_target") or ""), "last": float(row.get("last") or 0.0),
            "weeks": [{"from": float(w["from"]), "n": int(w["n"]), "ok": int(w["ok"])} for w in row.get("weeks") or []],
            "instead": [{"action": str(a["action"]), "name": plain(a["action"]), "n": int(a["n"]), "p": _figure(a["p"])}
                        for a in row.get("alternatives") or []]}


def evidence(now: float | None = None) -> Dict[str, Any]:
    """What ELI's actions did and how often its figures proved right, for the Evidence Wall's scoreboard
    and charts: {"actions": {"days", "runs", "rows": action_outcomes.report's actions, failures first, each
    with its causes in words, what worked instead and its weeks}, "accuracy": epistemics.measured's rows,
    each kind's share right, calibration error and weeks}. A record that cannot be read is left out, not
    filled in; read as often as the rooms' records."""
    global _evidence_cache
    clock = time.monotonic() if now is None else now
    if clock - _evidence_cache[0] < REFRESH_SECONDS:
        return _evidence_cache[1]
    out: Dict[str, Any] = {}
    try:
        from eli.runtime import action_outcomes
        rep = action_outcomes.report(action_outcomes.DAYS, limit=None)
        out["actions"] = {"days": float(rep["days"]), "runs": int(rep["runs"]),
                          "rows": [_action_row(r) for r in rep["actions"]]}
    except Exception:  # noqa: BLE001
        log.debug("what ELI's actions did was not read", exc_info=True)
    try:
        from eli.cognition.epistemics import measured
        out["accuracy"] = [{"kind": r["kind"], "words": r["words"], "n": int(r["n"]), "right": _figure(r["right"]),
                            "ece": _figure(r["ece"]) if r.get("ece") is not None else None,
                            "weeks": [{"from": float(w["from"]), "n": int(w["n"]),
                                       "right": _figure(w["right"]) if w.get("right") is not None else None}
                                      for w in r.get("weeks") or []]}
                           for r in measured()]
    except Exception:  # noqa: BLE001
        log.debug("how often ELI was right was not read", exc_info=True)
    _evidence_cache = (clock, out)
    return out


# ── ELI's books, as many as a message holds ──────────────────────────────────────────────────────

# A book's paper by what it is a book of, as the thought sheets are coloured by their kind.
BOOK_COLOURS = {"conversation": "#6b4f9a", "session": "#4f6b9a", "log": "#9a4f4f", "period": "#4f8a6b",
                "story": "#b08a3c", "likes": "#4f9a6b", "dislikes": "#9a6b4f", "beliefs": "#4f6b8a",
                "thoughts": "#8fe8a8", "reflection": "#b9a4ff", "goals": "#4fbf73", "diary": "#f5e6c8",
                "lesson": "#c8a45a"}
_books_cache: Dict[str, Any] = {"at": -REFRESH_SECONDS, "books": [], "sections": {}}


def _book_object(book: Dict[str, Any]) -> Dict[str, Any]:
    """A book as the feed carries it (an object of kind "book", its "book" without sections)."""
    from eli.world.books import header
    of = str(book["of"])
    told = {k: v for k, v in header(book).items()
            if k not in ("id", "room", "made_by", "importance", "length")}
    told["length"] = int(book.get("length") or 0)
    return {
        "id": book["id"], "kind": "book", "room": book["room"], "name": str(book["title"])[:60],
        "meaning": str(book.get("about") or "")[:200], "made_by": book["made_by"],
        "when": book.get("from") if book.get("from") is not None else book.get("to"),
        "x": 0.0, "y": 0.0, "importance": float(book.get("importance") or 0.0),
        "form": {"shape": "board", "colour": BOOK_COLOURS.get(of, "#d9e4f2"), "size": 1.0, "glow": 0.0,
                 "pos": _spot(book["id"], 0.9, 2.1)},
        "book": told,
    }


def _shelf_order(books: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The order books go into a message: each room's newest in turn, so no room's books crowd out
    another's when not all fit."""
    rooms: Dict[str, List[Dict[str, Any]]] = {}
    for book in sorted(books, key=lambda b: float(b.get("to") or b.get("from") or 0.0), reverse=True):
        rooms.setdefault(book["room"], []).append(book)
    out: List[Dict[str, Any]] = []
    while any(rooms.values()):
        for shelf in rooms.values():
            if shelf:
                out.append(shelf.pop(0))
    return out


def _size(value: Any) -> int:
    """What a value takes in the message, as the world link sends it (json.dumps, ASCII)."""
    import json
    return len(json.dumps(value, default=str))


def books(room: int, now: float | None = None) -> List[Dict[str, Any]]:
    """ELI's books (eli/world/books.py) as feed objects of kind "book", as many as `room` bytes of the
    message hold: every room's newest first, in turn; then, newest first again, the sections of each
    that still fits whole. A book sent without its sections carries its length, and is read whole
    from GET /v1/world/book/{id}. The books are read as often as the rooms' records, and what each
    takes in the message is measured then; the sections of a book once read are kept until then."""
    from eli.world import books as kept
    clock = time.monotonic() if now is None else now
    if clock - float(_books_cache["at"]) >= REFRESH_SECONDS:
        try:
            listed = _shelf_order(kept.listed())
        except Exception:  # noqa: BLE001
            log.debug("ELI's books were not read", exc_info=True)
            listed = []
        entries = []
        for book in listed:
            obj = _book_object(book)
            entries.append((book, obj, _size(obj) + 2))          # and the ", " between it and the next
        _books_cache.update({"at": clock, "books": entries, "sections": {}})
    chosen = []
    used = 0
    for book, obj, cost in _books_cache["books"]:                 # the books themselves, as many as fit
        if used + cost <= room:
            chosen.append((book, obj))
            used += cost
    # their text, newest first, for as many as still fit whole: read once, together, for the run of books
    # that might (a run, so the conversations' turns are read over one stretch of time, not all of it)
    left, might = room - used, set()
    for book, _obj in chosen:
        if int(book.get("length") or 0) > left:
            break
        might.add(book["id"])
        left -= int(book.get("length") or 0)
    whole = _books_cache["sections"]
    missing = [b for b, _o in chosen if b["id"] in might and b["id"] not in whole]
    if missing:
        try:
            text = kept.sections(missing)
        except Exception:  # noqa: BLE001
            log.debug("the books' text was not read", exc_info=True)
            text = {}
        for book in missing:
            obj = next(o for b, o in chosen if b is book)
            if book["id"] in text:
                full = {**obj, "book": {**obj["book"], "sections": text[book["id"]]}}
                whole[book["id"]] = (full, _size(full) - _size(obj))
            else:
                whole[book["id"]] = None
    out = []
    for book, obj in chosen:
        full = whole.get(book["id"]) if book["id"] in might else None
        if full is not None and used + full[1] <= room:
            obj, used = full[0], used + full[1]
        out.append(obj)
    return out


def items(now: float | None = None) -> List[Dict[str, Any]]:
    """The records to show. A source that cannot be read shows nothing of its own."""
    global _cache
    now = time.monotonic() if now is None else now
    if _cache[1] and now - _cache[0] < REFRESH_SECONDS:
        return _cache[1]
    found: List[Dict[str, Any]] = []
    for source in (_memories, _goals, _claims, _conflicts, _results, _thought_notes):
        try:
            found.extend(source())
        except Exception:
            continue
    _cache = (now, found)
    return found


def forget() -> None:
    global _cache, _thoughts_cache, _free_cache, _coming_cache, _kept_cache, _evidence_cache
    _cache = (0.0, [])
    _evidence_cache = (-REFRESH_SECONDS, {})
    _books_cache.update({"at": -REFRESH_SECONDS, "books": [], "sections": {}})
    _thoughts_cache = (0.0, [])
    _coming_cache = (-REFRESH_SECONDS, [])
    _free_cache = (-REFRESH_SECONDS, False)
    _kept_cache.clear()
