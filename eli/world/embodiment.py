"""
eli/world/embodiment.py
───────────────────────
What ELI can tell of where it is embodied, while it is embodied.

A spatial client (ELI VR) reports the scene every few seconds: which room the
user is standing in and what that place is, where they are and what they are
looking at, whether ELI is there with them and how far away, which of ELI's things are
near. That is kept here, in memory only, for as long as it is fresh. While it
is, ELI's prompt carries a few plain lines saying so, and ELI can answer "where
are we" or "what am I looking at" from what is actually the case.

    report(scene)       a client saying what is so now
    live()              the freshest scene, or None once it has gone stale
    held()              the record in the user's hand, if they picked one of ELI's up
    held_record(held)   what is on record behind it: a thought, a belief, a memory, a finding
    report_body(body)   a client saying, once, what it can draw: the real things ELI may place
    props()             those things, from the last client that said
    context_block()     the lines for the prompt, or "" when nobody is there

Plain text on purpose: it reads the same to any local model, and asks nothing
of a model's own tool or vision format.

Presence is kept per session, on disk in the world folder, so every ELI v3 process
on this data folder agrees on who is here (ELI VR may be served by a headless ELI
of its own while the desktop window runs beside it):

    presence.json     the bodies here now, one per (surface, session), with the
                      last scene each reported and when
    sessions.jsonl    the bodies that were here: surface, session, when it came
                      and left, the rooms it was in, and why it ended

A session ends when its client says so (vr_session_ended), when its world link
closes, when the same surface starts a new session, or when no scene has come
for FRESH_SECONDS; it ends at the last moment it was seen.

    present()                 the bodies here now
    end(surface, session, why)  a session over: its client said so, or its link closed
    sessions_between(a, b)    the sessions that overlap a period, for recall
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

from eli.utils.log import get_logger
from eli.world.building import _number, _text  # the one reading of a number and of a line of text a body sends

FRESH_SECONDS = 45.0       # a scene older than this is not where anyone is
SCENE_MAX = 4000           # characters of JSON a report may be
_TEXT, _LONG = 60, 240
HELD_LINES = 8             # what a held sheet says, line by line, as a body reports it

log = get_logger(__name__)
PROPS_MAX = 400            # real things one body may offer
_lock = threading.Lock()
_body: Dict[str, Any] = {}       # the last body that said what it can draw


def _triple(value: Any) -> Optional[List[float]]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [round(_number(v), 2) for v in value]
    return None


def _clean(scene: Dict[str, Any]) -> Dict[str, Any]:
    """A report cut down to what is understood here, every text held short."""
    out: Dict[str, Any] = {
        "surface": _text(scene.get("surface") or "spatial_client", 32),
        "session": _text(scene.get("session"), 64),
        "room": _text(scene.get("room"), 40).lower().replace(" ", "_"),
        "room_name": _text(scene.get("room_name"), _TEXT),
        "place": _text(scene.get("place"), 40),
        "place_name": _text(scene.get("place_name"), _TEXT),
        "seen_as": _text(scene.get("seen_as"), _LONG),
        "headset": bool(scene.get("headset")),
        "screen": bool(scene.get("screen")),
        "minutes": round(max(0.0, _number(scene.get("minutes"))), 1),
    }
    user = scene.get("user") if isinstance(scene.get("user"), dict) else {}
    looking = user.get("looking_at") if isinstance(user.get("looking_at"), dict) else {}
    out["user"] = {
        "pos": _triple(user.get("pos")) or [0.0, 1.7, 0.0],
        "facing": [round(_number(v), 3) for v in user.get("facing", [])][:2]
        if isinstance(user.get("facing"), (list, tuple)) and len(user.get("facing")) >= 2 else [0.0, 1.0],
        "looking_at": {"kind": _text(looking.get("kind"), 24), "name": _text(looking.get("name"), _TEXT)},
    }
    eli = scene.get("eli") if isinstance(scene.get("eli"), dict) else {}
    out["eli"] = {
        "visible": bool(eli.get("visible")),
        "pos": _triple(eli.get("pos")),
        "distance": round(max(0.0, _number(eli.get("distance"))), 1),
    }
    # a record of ELI's the user has picked up (a thought sheet, a belief, a memory, a finding)
    holding = scene.get("holding") if isinstance(scene.get("holding"), dict) else None
    if holding and _text(holding.get("id"), 80):
        out["holding"] = {"id": _text(holding.get("id"), 80), "kind": _text(holding.get("kind"), 24),
                          "name": _text(holding.get("name"), 80),
                          "lines": [_text(line, 160) for line in (holding.get("lines") or [])[:HELD_LINES] if _text(line, 160)]}
    things = []
    for thing in (scene.get("things") or [])[:12]:
        if isinstance(thing, dict) and _text(thing.get("name"), _TEXT):
            things.append({"name": _text(thing.get("name"), _TEXT), "kind": _text(thing.get("kind"), 24),
                           "distance": round(max(0.0, _number(thing.get("distance"))), 1)})
    out["things"] = things
    return out


BODY_NAMES = {"eli_vr": "ELI VR", "spatial_client": "a spatial client"}

PRESENCE_FILE = "presence.json"      # the bodies here now, each with its last scene
SESSIONS_FILE = "sessions.jsonl"     # the bodies that were here, one line per finished session
_presence_lock = threading.RLock()


def _now() -> float:
    return time.time()


def _body_name(surface: str, headset: bool) -> str:
    name = BODY_NAMES.get(surface, surface.replace("_", " "))
    return f"{name} ({'headset' if headset else 'flat window'})"


def _remember(title: str, text: str) -> bool:
    """A body coming or going, kept where ELI's recall finds it and in its journal. False when
    neither could be written."""
    kept = 0
    try:
        from eli.memory import get_memory
        get_memory().store_memory(text, tags=["embodiment", "body", "vr"], source="embodiment", kind="event")
        kept += 1
    except Exception:
        log.warning("a body's coming or going could not be remembered", exc_info=True)
        kept += 0
    try:
        from eli.world.persistence.journal import append_journal_entry
        append_journal_entry(title=title, body=text, source="embodiment")
        kept += 1
    except OSError:
        log.warning("a body's coming or going could not be journalled", exc_info=True)
    return kept > 0


def _presence_files():
    from eli.world.persistence import storage
    folder = storage.world_dir()
    return folder / PRESENCE_FILE, folder / SESSIONS_FILE


@contextmanager
def _file_held(path):
    """`path` (a lock file) held against every other process on this computer."""
    with open(path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _read_open(path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = data.get("open") if isinstance(data, dict) else None
    return {k: v for k, v in (found or {}).items() if isinstance(v, dict)}


def _write_open(path, open_: Dict[str, Dict[str, Any]]) -> None:
    fd, scratch = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"open": open_}, fh, ensure_ascii=False, default=str)
    os.replace(scratch, path)


Change = Callable[[Dict[str, Dict[str, Any]], float], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]


def _change_presence(change: Change) -> Dict[str, Dict[str, Any]]:
    """Run `change(open, now)` on the bodies here now, held against this process's other threads and
    every other ELI v3 process on this data folder; write back what it changed, file the sessions it
    ended in the history, and then (with nothing held) remember each arrival and departure. Gives
    back the bodies here now."""
    path, history = _presence_files()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _presence_lock, _file_held(path.with_name(path.name + ".lock")):
            open_ = _read_open(path)
            before = json.dumps(open_, sort_keys=True, default=str)
            arrived, left = change(open_, _now())
            if json.dumps(open_, sort_keys=True, default=str) != before:
                _write_open(path, open_)
            if left:
                with history.open("a", encoding="utf-8") as fh:
                    for ended in left:
                        fh.write(json.dumps(ended, ensure_ascii=False, default=str) + "\n")
                from eli.world.persistence import storage
                storage._trim_jsonl(history)
    except OSError:
        log.warning("who is in ELI's world could not be kept on disk", exc_info=True)
        return {}
    # a memory write may embed and index: never while presence is held (scenes and prompts wait on it)
    for entry in left:
        _remember("A body left", _departure_words(entry))
    for entry in arrived:
        _remember("A body arrived", _arrival_words(entry))
    return open_


def _when(ts: float) -> str:
    return time.strftime("%H:%M on %a %d %b %Y", time.localtime(float(ts)))


def _arrival_words(entry: Dict[str, Any]) -> str:
    room = (entry.get("rooms") or ["its rooms"])[0]
    return f"{_body_name(entry['surface'], bool(entry.get('headset')))} joined ELI in {room} at {_when(entry['started'])}."


def _departure_words(entry: Dict[str, Any]) -> str:
    rooms = list(entry.get("rooms") or [])
    minutes = max(1, int(round((float(entry["ended"]) - float(entry["started"])) / 60.0)))
    words = (f"{_body_name(entry['surface'], bool(entry.get('headset')))} left at {_when(entry['ended'])}, after "
             f"{minutes} minute{'s' if minutes != 1 else ''} with ELI (it came in {rooms[0] if rooms else 'its rooms'}).")
    later = [r for r in dict.fromkeys(rooms[1:]) if r != (rooms[0] if rooms else "")]
    if later:
        words += f" It was also in {', '.join(later)}."
    return words


def _ended(entry: Dict[str, Any], why: str) -> Dict[str, Any]:
    """A session over, at the last moment it was seen."""
    done = {k: v for k, v in entry.items() if k != "scene"}
    done["ended"] = float(entry.get("last_seen") or entry.get("started") or _now())
    done["end"] = why
    return done


def _end_stale(open_: Dict[str, Dict[str, Any]], now: float, max_age: float) -> List[Dict[str, Any]]:
    gone = [k for k, e in open_.items() if now - float(e.get("last_seen") or 0.0) > max_age]
    return [_ended(open_.pop(k), "no longer seen") for k in gone]


def _key(surface: str, session: str) -> str:
    return f"{surface}|{session}"


def present() -> List[Dict[str, Any]]:
    """The bodies here now: [{"surface", "session", "since", "room" (where it came in), "rooms",
    "headset", "last_seen"}]. One no scene has come from for FRESH_SECONDS has left."""
    open_ = _change_presence(lambda o, now: ([], _end_stale(o, now, FRESH_SECONDS)))
    return [{"surface": e["surface"], "session": e.get("session", ""), "since": e["started"],
             "room": (e.get("rooms") or [""])[0], "rooms": list(e.get("rooms") or []),
             "headset": bool(e.get("headset")), "last_seen": e.get("last_seen")} for e in open_.values()]


def report(scene: Any) -> Dict[str, Any]:
    """Take a client's word for what is so now. Raises ValueError for something
    that is not a scene, or is too large to be one."""
    if not isinstance(scene, dict):
        raise ValueError("a scene must be an object")
    if len(json.dumps(scene, default=str)) > SCENE_MAX:
        raise ValueError("the scene is too large")
    clean = _clean(scene)

    def change(open_, now):
        left = _end_stale(open_, now, FRESH_SECONDS)
        surface, session = clean["surface"], clean["session"]
        key = _key(surface, session)
        if session:          # the same surface on a new session: the old one is over
            for other in [k for k, e in open_.items() if e.get("surface") == surface and k != key]:
                left.append(_ended(open_.pop(other), "a new session began"))
        room = clean.get("room_name") or clean.get("room") or ""
        entry, arrived = open_.get(key), []
        if entry is None:
            entry = {"surface": surface, "session": session, "started": now, "rooms": [room] if room else []}
            open_[key] = entry
            arrived.append(entry)
        elif room and (not entry.get("rooms") or entry["rooms"][-1] != room):
            entry.setdefault("rooms", []).append(room)
        entry["headset"] = bool(clean.get("headset"))
        entry["last_seen"] = now
        entry["scene"] = clean
        return arrived, left

    _change_presence(change)
    _measure_headset()
    return clean


def end(surface: Optional[str] = None, session: str = "", why: str = "the session ended") -> None:
    """A session over: its client said so, or its world link closed. With a session id, that session;
    with only a surface, every session of it; with neither, everyone."""
    def change(open_, now):
        gone = [k for k, e in open_.items()
                if (surface is None or e.get("surface") == surface) and (not session or e.get("session") == session)]
        return [], [_ended(open_.pop(k), why) for k in gone]
    _change_presence(change)


def sessions_between(since: float, until: float) -> List[Dict[str, Any]]:
    """The sessions that overlap [since, until), oldest first: {"surface", "session", "headset",
    "started", "ended" (None while it is still here), "rooms", "end"}. What recall reads for a
    period: who came into ELI's world, when, where, and when they left."""
    _, history = _presence_files()
    found: List[Dict[str, Any]] = []
    try:
        with history.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and "started" in entry:
                    found.append(entry)
    except OSError:
        pass
    for entry in present():
        found.append({"surface": entry["surface"], "session": entry["session"], "headset": entry["headset"],
                      "started": entry["since"], "ended": None, "rooms": entry["rooms"], "end": ""})
    out = [e for e in found if float(e["started"]) < until
           and (e.get("ended") is None or float(e["ended"]) >= since)]
    return sorted(out, key=lambda e: float(e["started"]))


def _measure_headset() -> None:
    """What the headset's programs hold on the cards, kept for the next model load
    (eli/core/load_plan.py); no more often than a scene stays fresh."""
    try:
        from eli.core import load_plan
        load_plan.note_xr_footprint()
    except (OSError, ImportError):
        log.debug("the headset's footprint was not measured", exc_info=True)


def report_body(body: Any) -> Dict[str, Any]:
    """Take a client's word for the real things it can draw (chairs, lamps, shelves), each an id,
    a name and its size in metres [wide, tall, deep]. Kept until another body says otherwise:
    a thing ELI places stays drawable whether or not the body is there at that moment."""
    if not isinstance(body, dict):
        raise ValueError("a body must be an object")
    props = []
    for raw in (body.get("props") or [])[:PROPS_MAX]:
        if not isinstance(raw, dict):
            continue
        ident = str(raw.get("id") or "").strip()
        if not ident or not all(c.isalnum() or c in "_-" for c in ident) or len(ident) > 60:
            continue
        size = raw.get("size") if isinstance(raw.get("size"), (list, tuple)) and len(raw.get("size")) == 3 else [1, 1, 1]
        props.append({"id": ident, "name": _text(raw.get("name") or ident.replace("_", " "), 48),
                      "size": [round(max(0.01, _number(v, 1.0)), 2) for v in size]})
    clean = {"surface": _text(body.get("surface") or "spatial_client", 32), "props": props}
    with _lock:
        _body.clear()
        _body.update(clean)
    return {"surface": clean["surface"], "props": len(props)}


def props() -> List[Dict[str, Any]]:
    """The real things ELI may place: what the last body said it draws, and ELI's own models
    (eli/world/models.py), which a body fetches from the feed. [{"id", "name", "size"}]."""
    with _lock:
        out = [dict(p) for p in _body.get("props") or []]
    seen = {p["id"] for p in out}
    from eli.world import models
    out += [p for p in models.props() if p["id"] not in seen][:PROPS_MAX]
    return out


def live(max_age: float = FRESH_SECONDS) -> Optional[Dict[str, Any]]:
    """The freshest scene any client reported, or None once all have gone stale. Read from the
    presence record, so any ELI v3 process on this data folder sees the body another one serves."""
    open_ = _change_presence(lambda o, now: ([], _end_stale(o, now, max_age)))
    scenes = [e for e in open_.values() if isinstance(e.get("scene"), dict)]
    if not scenes:
        return None
    return dict(max(scenes, key=lambda e: float(e.get("last_seen") or 0.0))["scene"])


def forget_body() -> None:
    with _lock:
        _body.clear()


def forget(surface: Optional[str] = None) -> None:
    """A client leaving, or (with no name) everyone."""
    end(surface, why="the session ended")


def held(max_age: float = FRESH_SECONDS) -> Optional[Dict[str, Any]]:
    """The record in the user's hand ({"id", "kind", "name", "lines"}), or None."""
    scene = live(max_age)
    return dict(scene["holding"]) if scene and scene.get("holding") else None


def held_record(holding: Dict[str, Any]) -> List[str]:
    """What is on record behind a held sheet, as lines: a thought with why it came up, how sure ELI
    was, what it decided and what became of it; a belief with its basis and chance; a memory with
    when it was kept, how often it helped and was corrected; a finding with what it found; a book
    with what it is a book of and when, for a story how ELI told it and from what, for a lesson its
    record (the page in view comes from the body, as "lines"). Only what the record holds; nothing
    filled in."""
    ident = str((holding or {}).get("id") or "")
    kind, _, tail = ident.partition("_")
    lines: List[str] = []
    try:
        if kind == "thought" and tail.isdigit():
            from eli.cognition.thoughts import by_id
            thought = by_id(int(tail))
            if thought:
                lines.append(f"A {thought['kind']} thought you had: {thought['what']}")
                if thought.get("why"):
                    lines.append("It came up because " + "; ".join(str(w) for w in thought["why"][:3]) + ".")
                if thought.get("decision"):
                    lines.append(f"You decided: {thought['decision']}"
                                 + (f", because {thought['reason']}" if thought.get("reason") else "") + ".")
                if thought.get("chance") is not None:
                    lines.append(f"How sure you were: {float(thought['chance']):.0%}.")
                lines.append(("What became of it: it proved " + str(thought.get("outcome"))
                              + (f" ({thought['how']})" if thought.get("how") else "") + ".")
                             if thought.get("outcome") else "What became of it is not settled yet.")
                lines.append("When: " + time.strftime("%a %d %b %Y %H:%M", time.localtime(float(thought.get("ts") or 0.0))) + ".")
        elif kind == "claim" and tail.isdigit():
            from eli.memory import claims as _claims
            from eli.memory.memory import get_memory
            row = next((r for r in get_memory().claims_current() or [] if str(r.get("id")) == tail), None)
            if row:
                lines.append("A belief of yours about the user: " + _claims.describe(row) + ".")
                lines.append("Its basis: " + _claims.basis(row) + ".")
                try:
                    lines.append(f"The chance it is right, as you measure it: {float(_claims.chance(row).p):.0%}.")
                except (OSError, ValueError, ImportError, AttributeError) as failure:
                    log.debug("a held claim's chance was not read: %s", failure)
                if row.get("contested"):
                    lines.append("Something else you were told disputes it.")
        elif kind == "memory" and tail.isdigit():
            from eli.memory.memory import get_memory
            facts = get_memory().memory_facts(int(tail))
            if facts:
                lines.append("A memory of yours: " + str(facts.get("text") or ""))
                if facts.get("when"):
                    lines.append("Kept on " + time.strftime("%a %d %b %Y %H:%M", time.localtime(float(facts["when"])))
                                 + (f", from {facts['source']}" if facts.get("source") else "") + ".")
                lines.append(f"How much it matters to you: {float(facts.get('importance') or 0.0):.0%}; it helped "
                             f"{int(facts.get('helped') or 0)} answer{'s' if int(facts.get('helped') or 0) != 1 else ''} and "
                             f"was corrected {int(facts.get('corrected') or 0)} time{'s' if int(facts.get('corrected') or 0) != 1 else ''}.")
        elif kind == "book" and tail:
            from eli.world import books
            book = books.read(ident)
            if book:
                span = " – ".join(time.strftime("%a %d %b %Y", time.localtime(float(t)))
                                  for t in dict.fromkeys(x for x in (book.get("from"), book.get("to")) if x))
                lines.append(f"A book of yours ({book['of']}): \"{book['title']}\"" + (f", {span}" if span else "") + ".")
                if book.get("about"):
                    lines.append(f"What it is: {book['about']}.")
                if book.get("of") == "story":
                    lines.append(("You wrote it as fiction" if book.get("fiction") else "You wrote it as it happened")
                                 + (f", {book['by'].replace('ELI, ', '', 1)}" if str(book.get("by") or "").startswith("ELI, ") else "")
                                 + (", from " + "; ".join(book["drawn_from"]) if book.get("drawn_from") else "") + ".")
                if book.get("of") == "lesson" and isinstance(book.get("record"), dict):
                    rec = book["record"]
                    lines.append(f"It is a lesson about {book.get('applies_to', '')}: {rec['status']}, checked {rec['checks']} "
                                 f"time{'s' if rec['checks'] != 1 else ''}, it helped {rec['helped']} and harmed {rec['harmed']}"
                                 + (f"; retired because {rec['retired_reason']}" if rec.get("retired_reason") else "") + ".")
        elif kind in ("found", "written", "seen") and tail.isdigit():
            from eli.world import shown
            record = next((r for r in shown.items() if r.get("id") == ident), None)
            if record:
                lines.append(f"What you {'found' if kind == 'found' else 'wrote' if kind == 'written' else 'saw'} for the user: {record['name']}.")
                lines.extend(str(line) for line in record.get("lines") or [])
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError) as failure:
        log.debug("the held record was not read: %s", failure)
    return lines[:HELD_LINES + 2]


def near_user(scene: Optional[Dict[str, Any]], ahead: float = 2.2) -> Tuple[float, float]:
    """A spot on the floor a little in front of the user: where a new thing
    goes when nobody said where. [right, forward] from the middle of the room."""
    if not scene:
        return 0.0, 2.5
    pos = scene["user"]["pos"]
    right, forward = scene["user"]["facing"]
    length = math.hypot(right, forward) or 1.0
    return round(pos[0] + ahead * right / length, 2), round(pos[2] + ahead * forward / length, 2)


def the(room_name: str) -> str:
    """A room's name as it reads in a sentence: "the Core Room", but "ELI's Room"."""
    return room_name if room_name.lower().startswith(("eli", "the ")) else f"the {room_name}"


def context_block(max_age: float = FRESH_SECONDS) -> str:
    """The lines ELI's prompt carries while someone is in a room with ELI."""
    scene = live(max_age)
    if scene is None:
        return ""
    where = scene["room_name"] or scene["room"].replace("_", " ").title() or "a room"
    how = "wearing a headset" if scene["headset"] else "on a monitor, not in a headset"
    lines = [f"The user is inside ELI VR right now, {how}, standing in {the(where)}."]
    if scene["seen_as"]:
        lines.append(f"What that place is: {scene['seen_as']}")
    eli = scene["eli"]
    if eli["visible"]:
        lines.append(f"You are there with them as a body of light, about {eli['distance']:.1f} m from them.")
    else:
        lines.append("You are not in that room at the moment, so they cannot see you there.")
    looking = scene["user"]["looking_at"]
    if looking["kind"] == "eli":
        lines.append("They are looking at you.")
    elif looking["name"]:
        lines.append(f"They are looking at: {looking['name']}.")
    holding = scene.get("holding")
    if holding:
        what = ("a book of yours" if holding.get("kind") == "book"
                else f"a {holding['kind']} sheet of yours" if holding.get("kind") else "a sheet of yours")
        lines.append(f"In their hand: {what}, \"{holding['name']}\"." + (" It reads: " + " / ".join(holding["lines"]) if holding.get("lines") else ""))
        for line in held_record(holding):
            lines.append("  " + line)
        lines.append("If they ask about what they are holding, answer from that record: why it came up, how sure you "
                     "were, what you decided, what became of it. What the record does not hold, say you do not have.")
    if scene["things"]:
        listed = ", ".join(f"{t['name']} ({t['kind']}, {t['distance']:.1f} m)" if t["kind"] else t["name"]
                           for t in scene["things"][:8])
        lines.append(f"Things of yours in the room: {listed}.")
    if scene["screen"]:
        lines.append("Their desktop is showing on a large screen in the room.")
    lines.append("This is a real 3D place the user can see. Speak as someone who is there with them. "
                 "Describe only what is listed here; do not invent other objects, people or events.")
    return "[WHERE YOU ARE: live, from ELI VR]\n" + "\n".join(f"  {line}" for line in lines)
