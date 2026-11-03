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
    report_body(body)   a client saying, once, what it can draw: the real things ELI may place
    props()             those things, from the last client that said
    context_block()     the lines for the prompt, or "" when nobody is there

Plain text on purpose: it reads the same to any local model, and asks nothing
of a model's own tool or vision format. Nothing here is written to disk, and
nothing is kept once the client stops reporting.
"""
from __future__ import annotations

import json
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

FRESH_SECONDS = 45.0       # a scene older than this is not where anyone is
SCENE_MAX = 4000           # characters of JSON a report may be
_TEXT, _LONG = 60, 240

log = get_logger(__name__)
PROPS_MAX = 400            # real things one body may offer
_lock = threading.Lock()
_scenes: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_body: Dict[str, Any] = {}       # the last body that said what it can draw


def _text(value: Any, limit: int = _TEXT) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


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
        "room_name": _text(scene.get("room_name")),
        "place": _text(scene.get("place"), 40),
        "place_name": _text(scene.get("place_name")),
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
        "looking_at": {"kind": _text(looking.get("kind"), 24), "name": _text(looking.get("name"))},
    }
    eli = scene.get("eli") if isinstance(scene.get("eli"), dict) else {}
    out["eli"] = {
        "visible": bool(eli.get("visible")),
        "pos": _triple(eli.get("pos")),
        "distance": round(max(0.0, _number(eli.get("distance"))), 1),
    }
    things = []
    for thing in (scene.get("things") or [])[:12]:
        if isinstance(thing, dict) and _text(thing.get("name")):
            things.append({"name": _text(thing.get("name")), "kind": _text(thing.get("kind"), 24),
                           "distance": round(max(0.0, _number(thing.get("distance"))), 1)})
    out["things"] = things
    return out


_present: Dict[str, Dict[str, Any]] = {}     # surface -> when it came and where, while it is here
BODY_NAMES = {"eli_vr": "ELI VR", "spatial_client": "a spatial client"}


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


def _came(clean: Dict[str, Any]) -> None:
    surface = clean["surface"]
    if surface in _present:
        return
    now = time.time()
    room = clean.get("room_name") or clean.get("room") or "its rooms"
    _present[surface] = {"since": now, "room": room, "headset": bool(clean.get("headset"))}
    _remember("A body arrived", f"{_body_name(surface, bool(clean.get('headset')))} joined ELI in {room} at "
              f"{time.strftime('%H:%M on %a %d %b %Y', time.localtime(now))}.")


def _went(surface: str) -> None:
    was = _present.pop(surface, None)
    if not was:
        return
    now = time.time()
    minutes = max(1, int(round((now - float(was["since"])) / 60.0)))
    _remember("A body left", f"{_body_name(surface, bool(was.get('headset')))} left at "
              f"{time.strftime('%H:%M on %a %d %b %Y', time.localtime(now))}, after {minutes} minute"
              f"{'s' if minutes != 1 else ''} with ELI (it came in {was['room']}).")


def present() -> List[Dict[str, Any]]:
    """The bodies here now: [{"surface", "since", "room", "headset"}]."""
    with _lock:
        return [{"surface": s, **dict(v)} for s, v in _present.items()]


def report(scene: Any) -> Dict[str, Any]:
    """Take a client's word for what is so now. Raises ValueError for something
    that is not a scene, or is too large to be one."""
    if not isinstance(scene, dict):
        raise ValueError("a scene must be an object")
    if len(json.dumps(scene, default=str)) > SCENE_MAX:
        raise ValueError("the scene is too large")
    clean = _clean(scene)
    with _lock:
        _scenes[clean["surface"]] = (time.monotonic(), clean)
        _came(clean)
    return clean


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
    """The freshest scene any client reported, or None once all have gone stale."""
    now = time.monotonic()
    with _lock:
        for surface in [s for s, (at, _) in _scenes.items() if now - at > max_age]:
            del _scenes[surface]
            _went(surface)
        if not _scenes:
            return None
        return dict(max(_scenes.values(), key=lambda item: item[0])[1])


def forget_body() -> None:
    with _lock:
        _body.clear()


def forget(surface: Optional[str] = None) -> None:
    """A client leaving, or (with no name) everyone."""
    with _lock:
        if surface is None:
            _scenes.clear()
            for gone in list(_present):
                _went(gone)
        else:
            _scenes.pop(surface, None)
            _went(surface)


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
    if scene["things"]:
        listed = ", ".join(f"{t['name']} ({t['kind']}, {t['distance']:.1f} m)" if t["kind"] else t["name"]
                           for t in scene["things"][:8])
        lines.append(f"Things of yours in the room: {listed}.")
    if scene["screen"]:
        lines.append("Their desktop is showing on a large screen in the room.")
    lines.append("This is a real 3D place the user can see. Speak as someone who is there with them. "
                 "Describe only what is listed here; do not invent other objects, people or events.")
    return "[WHERE YOU ARE: live, from ELI VR]\n" + "\n".join(f"  {line}" for line in lines)
