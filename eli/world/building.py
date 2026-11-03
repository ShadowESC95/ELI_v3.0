"""
eli/world/building.py
─────────────────────
ELI's hand on its own world: placing things in the rooms, moving and changing
them, taking them away again, setting the sky, light and ground of the one
room that is ELI's own, and the light of every other room.

Nothing here draws. An edit is a small dict; this module checks it, turns it
into the world's own actions, and those go the way every world action goes:
through the autonomy engine, the world policy and the provenance ledger, into
the world state. A spatial client (ELI VR) then sees the result in the world
feed and draws it.

    apply(edits, actor=..., summary=...)   check, record and apply a batch
    form_of(obj)                           how an object looks and where it stands
    mood_of(spaces, room)                  sky, light, ground and mist of ELI's room
    look_of(rooms, room)                   the light ELI gave a room of the house

Two ways to say what a thing is. A *shape* is one word (pillar, tree, lamp):
enough for the smallest model. A list of *parts* is anything at all: boxes,
balls, cones, rings, a profile turned on a lathe, an outline pulled up into a
solid, a tube along a path, a line of text, each with its own place, turn,
size, colour, metal, glow, clearness and motion. Nothing ELI makes has to come
from a list. The same holds for its room: a sky by name, or painted from two
colours, stars and a sun wherever ELI puts it; ground by name, or any colour,
flat or rising into hills.

Anything a model gets slightly wrong is put right here rather than refused.

What the policy judges is what an action does. The words a thing is given (its
name, what it means) are not part of the action: they ride in the event that
caused it, and the ledger keeps both.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from eli.world.core.schemas import (
    EliWorldState, WorldAction, WorldActionType, WorldEvent, WorldObject,
)

EVENT_TYPE = "world_edit"
BUILT = "built"                      # object_type of a thing made this way

# The one room that is ELI's to shape whole. The nine rooms of the house keep
# their own sky, ground and air; ELI may put things in them and give each a
# light of its choosing (HOUSE_LOOK), worn by the room's glowing fixtures and
# kept with the room. ELI's own room is
# kept in the world's `spaces`, beside the house.
OWN_ROOM = "own_room"
OWN_ROOM_INFO: Dict[str, Any] = {
    "name": "ELI's Room",
    "purpose": "ELI's own space. Nothing is here but what ELI has made.",
    "theme": "open ground under an open sky",
    "unlocked": True,
}

SHAPES = (
    "orb", "pillar", "block", "slab", "arch", "ring", "crystal", "tree",
    "lamp", "board", "cone", "stair", "table", "seat", "pool", "beacon", "painting",
)
PROP = "prop"                        # the shape of a real thing the body has (a chair, a lamp)
# A painting is ELI's own: a style and the colours it is painted in.
ART_STYLES = ("rings", "stripes", "waves", "cells", "blooms", "horizon", "rays", "drift")
SKIES = ("stars", "dawn", "mist", "sunset", "void")
PAINTED = "painted"                  # a sky that is not one of those: ELI's own colours
GROUNDS = ("stone", "marble", "wood", "sand", "snow", "moss", "metal", "dark")
DEFAULT_MOOD: Dict[str, Any] = {"sky": "stars", "light": "#9fd8ff", "ground": "stone", "mist": 0.15}
HOUSE_LOOK = ("light",)

# What a thing can be made of, part by part.
PART_KINDS = ("box", "ball", "cylinder", "cone", "torus", "capsule", "prism", "lathe", "extrude", "tube", "text")
MADE = "made"                        # the shape of a thing given as parts
MAX_PARTS = 24
MAX_POINTS = 24                      # in a profile, an outline or a path
PART_REACH = 6.0                     # how far from the thing's foot a part may be
TEXT_MAX = 80

COLOURS = {
    "white": "#f2f2f2", "black": "#1a1a1a", "grey": "#8a8f98", "silver": "#c8ccd4",
    "red": "#e5484d", "orange": "#f08a3c", "amber": "#f5b942", "yellow": "#f2d94e",
    "gold": "#e2b44a", "green": "#4fbf73", "teal": "#2fb8a8", "cyan": "#4fd2f2",
    "blue": "#4c7df0", "violet": "#8b6cf0", "purple": "#a85ad6", "pink": "#f078b4",
    "brown": "#8a5a3c",
}

# Positions are metres from the middle of the room's floor: [right, up, forward].
REACH = 9.0            # how far from the middle a thing may stand
MAX_HEIGHT = 6.0
MIN_SIZE, MAX_SIZE = 0.1, 4.0
MAX_EDITS = 12         # in one batch
MAX_PER_ROOM = 60      # things standing in one room
NAME_MAX, MEANING_MAX, NOTE_MAX = 48, 160, 400

_OPS = {
    "create": "create", "add": "create", "build": "create", "make": "create",
    "place": "create", "new": "create",
    "move": "move", "put": "move", "shift": "move",
    "change": "change", "edit": "change", "alter": "change", "recolour": "change",
    "recolor": "change", "paint": "change", "resize": "change", "rename": "change",
    "remove": "remove", "delete": "remove", "retire": "remove", "destroy": "remove",
    "restore": "restore",
    "room": "room", "mood": "room", "alter_room": "room", "sky": "room",
    "note": "note", "journal": "note",
    "undo": "undo",
}
_SHAPE_WORDS = {
    "sphere": "orb", "ball": "orb", "globe": "orb", "column": "pillar", "post": "pillar",
    "cube": "block", "box": "block", "plate": "slab", "platform": "slab", "floor": "slab",
    "wall": "slab", "gate": "arch", "door": "arch", "doorway": "arch", "torus": "ring",
    "halo": "ring", "hoop": "ring", "gem": "crystal", "shard": "crystal", "plant": "tree",
    "bush": "tree", "light": "lamp", "lantern": "lamp", "sign": "board", "screen": "board",
    "panel": "board", "pyramid": "cone", "spike": "cone", "steps": "stair",
    "stairs": "stair", "desk": "table", "bench": "table", "chair": "seat", "stool": "seat",
    "pond": "pool", "water": "pool", "fountain": "pool", "tower": "beacon",
    "spire": "beacon", "obelisk": "beacon", "picture": "painting", "canvas": "painting",
    "artwork": "painting", "art": "painting", "poster": "painting", "mural": "painting",
}
_SKY_WORDS = {"night": "stars", "space": "stars", "starry": "stars", "morning": "dawn",
              "sunrise": "dawn", "fog": "mist", "foggy": "mist", "misty": "mist",
              "forest": "mist", "evening": "sunset", "dusk": "sunset", "sea": "sunset",
              "black": "void", "empty": "void", "dark": "void", "nothing": "void"}
_GROUND_WORDS = {"rock": "stone", "concrete": "stone", "timber": "wood", "planks": "wood",
                 "dune": "sand", "desert": "sand", "ice": "snow", "grass": "moss",
                 "earth": "moss", "forest": "moss", "steel": "metal", "glass": "dark",
                 "black": "dark", "white": "marble"}

# How the things the world makes by itself look, when nobody gave them a form.
_TYPE_FORMS = {
    "diagnostic_station": ("table", "#4fd2f2"), "reflection_tool": ("board", "#8b6cf0"),
    "upgrade_station": ("beacon", "#4fbf73"), "debug_marker": ("cone", "#e5484d"),
    "workbench": ("table", "#f08a3c"), "uncertainty_container": ("crystal", "#f5b942"),
    "evidence_surface": ("board", "#c8ccd4"), "reasoning_artifact": ("orb", "#9fd8ff"),
}
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


# ── reading an edit ──────────────────────────────────────────────────────────
def _number(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _first(raw: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw[key]
    return None


def _triple(value: Any, fallback: float, low: float, high: float) -> List[float]:
    """Three numbers from a list, a single number, or nothing."""
    if isinstance(value, (int, float)):
        value = [value, value, value]
    if isinstance(value, dict):
        value = [_first(value, "x", "right", "w"), _first(value, "y", "up", "h"), _first(value, "z", "forward", "d")]
    if not isinstance(value, (list, tuple)):
        value = []
    out = [round(_clamp(_number(v, fallback), low, high), 3) for v in list(value)[:3]]
    return out + [out[-1] if out else fallback] * (3 - len(out))


def _points(value: Any, width: int, low: float, high: float) -> List[List[float]]:
    """A list of points, each of `width` numbers, as many as are well formed."""
    out: List[List[float]] = []
    for point in value if isinstance(value, (list, tuple)) else []:
        if isinstance(point, (list, tuple)) and len(point) >= width and len(out) < MAX_POINTS:
            out.append([round(_clamp(_number(n, 0.0), low, high), 3) for n in point[:width]])
    return out


def part(raw: Any) -> Optional[Dict[str, Any]]:
    """One part of a thing made exact, or None if it is not a part at all."""
    if not isinstance(raw, dict):
        return None
    kind = _choice(_first(raw, "kind", "type", "shape", "part"), PART_KINDS, {
        "cube": "box", "block": "box", "sphere": "ball", "orb": "ball", "pillar": "cylinder",
        "column": "cylinder", "disc": "cylinder", "ring": "torus", "pyramid": "cone", "label": "text",
        "words": "text", "revolve": "lathe", "polygon": "extrude", "pipe": "tube", "line": "tube"})
    if kind is None:
        return None
    out: Dict[str, Any] = {
        "kind": kind,
        "pos": _triple(_first(raw, "pos", "at", "position"), 0.0, -PART_REACH, PART_REACH),
        "rot": _triple(_first(raw, "rot", "rotation", "turn"), 0.0, -360.0, 360.0),
        "size": _triple(_first(raw, "size", "scale"), 1.0, 0.02, 8.0),
        "colour": colour(_first(raw, "colour", "color")) or "#9fd8ff",
    }
    out["pos"][1] = max(out["pos"][1], -1.0)
    for key, default in (("metal", 0.1), ("rough", 0.5), ("glow", 0.0), ("clear", 0.0), ("pulse", 0.0)):
        if raw.get(key) is not None:
            out[key] = round(_clamp(_number(raw.get(key), default), 0.0, 1.0), 2)
    if raw.get("spin") is not None:
        out["spin"] = round(_clamp(_number(raw.get("spin"), 0.0), -360.0, 360.0), 1)
    if raw.get("bob") is not None:
        out["bob"] = round(_clamp(_number(raw.get("bob"), 0.0), 0.0, 1.0), 2)
    if kind == "lathe":
        out["profile"] = _points(_first(raw, "profile", "points"), 2, 0.0, PART_REACH)
        if len(out["profile"]) < 2:
            return None
    elif kind == "extrude":
        out["outline"] = _points(_first(raw, "outline", "points"), 2, -PART_REACH, PART_REACH)
        if len(out["outline"]) < 3:
            return None
    elif kind == "tube":
        out["path"] = _points(_first(raw, "path", "points"), 3, -PART_REACH, PART_REACH)
        out["radius"] = round(_clamp(_number(raw.get("radius"), 0.05), 0.01, 1.0), 3)
        if len(out["path"]) < 2:
            return None
    elif kind == "text":
        out["text"] = _text(_first(raw, "text", "says", "words"), TEXT_MAX)
        if not out["text"]:
            return None
    return out


def parts(raw: Any) -> List[Dict[str, Any]]:
    """The parts of a thing, made exact; the ones that are not parts are dropped."""
    made = [part(item) for item in raw] if isinstance(raw, (list, tuple)) else []
    return [p for p in made if p is not None][:MAX_PARTS]


def art(raw: Any) -> Optional[Dict[str, Any]]:
    """A painting's style and colours, put right: a style ELI VR can paint and two to four colours."""
    if not isinstance(raw, dict):
        return None
    style = str(raw.get("style") or "").strip().lower()
    tints = [c for c in (colour(v) for v in (raw.get("colours") or raw.get("colors") or [])) if c][:4]
    if style not in ART_STYLES and not tints:
        return None
    while len(tints) < 2:
        tints.append(["#1b2a4a", "#f5b942"][len(tints)])
    return {"style": style if style in ART_STYLES else ART_STYLES[0], "colours": tints}


def prop_of(value: Any) -> Optional[str]:
    """The id of a real thing the body can draw, from an id or its name; None when it has none such."""
    from eli.world import embodiment
    wanted = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    if not wanted:
        return None
    for prop in embodiment.props():
        if wanted in (re.sub(r"[^a-z0-9]+", "", prop["id"].lower()), re.sub(r"[^a-z0-9]+", "", prop["name"].lower())):
            return prop["id"]
    return None


def colour(value: Any) -> Optional[str]:
    """A colour as #rrggbb, from a name or a hex value; None if it is neither."""
    word = str(value or "").strip().lower()
    if _HEX.match(word):
        return "#" + word.lstrip("#")
    return COLOURS.get(word.replace("gray", "grey"))


def _choice(value: Any, allowed: Tuple[str, ...], words: Dict[str, str]) -> Optional[str]:
    word = str(value or "").strip().lower().replace("-", " ")
    if word in allowed:
        return word
    if word in words:
        return words[word]
    for part in word.split():           # "a tall stone pillar" -> pillar
        if part in allowed:
            return part
        if part in words:
            return words[part]
    return None


def _spot(raw: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Where on the floor: [right, forward], held inside the room."""
    at = _first(raw, "at", "pos", "position", "where")
    if isinstance(at, dict):
        at = [_first(at, "right", "x"), _first(at, "forward", "z", "y")]
    if isinstance(at, (list, tuple)) and len(at) >= 2:
        right = _number(at[0], 0.0)
        forward = _number(at[2] if len(at) >= 3 else at[1], 0.0)
    elif _first(raw, "x", "right") is not None or _first(raw, "z", "forward") is not None:
        right = _number(_first(raw, "x", "right"), 0.0)
        forward = _number(_first(raw, "z", "forward"), 0.0)
    else:
        return None
    distance = math.hypot(right, forward)
    if distance > REACH:
        right, forward = right * REACH / distance, forward * REACH / distance
    return round(right, 2), round(forward, 2)


def normalise(edits: Any, default_room: str = OWN_ROOM) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Edits as given (by a model, a client, a person) made exact, and the ones
    that cannot be made sense of, each with the reason."""
    clean: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    if isinstance(edits, dict):
        edits = [edits]
    if not isinstance(edits, list):
        return clean, [{"index": 0, "reason": "edits must be a list"}]
    for index, raw in enumerate(edits):
        if len(clean) >= MAX_EDITS:
            refused.append({"index": index, "reason": f"only {MAX_EDITS} edits at a time"})
            continue
        if not isinstance(raw, dict):
            refused.append({"index": index, "reason": "an edit must be an object"})
            continue
        op = _OPS.get(str(_first(raw, "op", "action", "do") or "").strip().lower())
        if op is None:
            refused.append({"index": index, "reason": f"unknown op {raw.get('op')!r}"})
            continue
        edit: Dict[str, Any] = {"op": op}
        room = _text(_first(raw, "room", "in"), 40).lower().replace(" ", "_")
        edit["room"] = room or default_room

        target = _text(_first(raw, "id", "object_id"), 80)
        named = _text(_first(raw, "name", "target", "object", "title"), NAME_MAX)
        if target:
            edit["id"] = target
        if named:
            edit["name"] = named

        if op in ("create", "change"):
            made = parts(raw.get("parts"))
            real = prop_of(_first(raw, "prop", "furniture", "model", "object"))
            shape = _choice(_first(raw, "shape", "kind", "type", "form"), SHAPES, _SHAPE_WORDS)
            if real:
                edit["prop"], shape = real, PROP        # a real thing the body has
            elif made:
                edit["parts"], shape = made, MADE       # anything at all, part by part
            elif shape is None and op == "create":
                shape = _choice(named, SHAPES, _SHAPE_WORDS) or "orb"
            if shape:
                edit["shape"] = shape
            tint = colour(_first(raw, "colour", "color", "tint"))
            if tint:
                edit["colour"] = tint
            if _first(raw, "size", "scale") is not None:
                edit["size"] = round(_clamp(_number(_first(raw, "size", "scale"), 1.0), MIN_SIZE, MAX_SIZE), 2)
            if raw.get("glow") is not None:
                edit["glow"] = round(_clamp(_number(raw.get("glow"), 0.0), 0.0, 1.0), 2)
            painted = art(raw.get("art"))
            if painted:
                edit["art"] = painted
                if "shape" not in edit or edit["shape"] not in (PROP, MADE):
                    shape = edit["shape"] = "painting"
            meaning = _text(_first(raw, "meaning", "why", "reason", "symbolic_meaning"), MEANING_MAX)
            if meaning:
                edit["meaning"] = meaning
            if op == "change" and raw.get("rename"):
                edit["rename"] = _text(raw.get("rename"), NAME_MAX)
        if op in ("create", "move", "change"):
            spot = _spot(raw)
            if spot is not None:
                edit["at"] = list(spot)
            if _first(raw, "turn", "facing", "yaw") is not None:
                # which way it faces, in degrees round from straight ahead
                edit["turn"] = round(_number(_first(raw, "turn", "facing", "yaw"), 0.0) % 360.0, 1)
            if _first(raw, "height", "up") is not None:
                edit["height"] = round(_clamp(_number(_first(raw, "height", "up"), 0.0), 0.0, MAX_HEIGHT), 2)
        if op == "create" and not named:
            edit["name"] = (next((p["name"] for p in _props() if p["id"] == edit.get("prop")), "")
                            or edit["shape"].title()).title()
        if op in ("move", "change", "remove", "restore") and not (target or named):
            refused.append({"index": index, "reason": f"{op} needs the name or id of a thing"})
            continue
        if op == "move" and "at" not in edit and "height" not in edit:
            refused.append({"index": index, "reason": "move needs somewhere to move to"})
            continue
        if op == "room":
            sky = _choice(raw.get("sky"), SKIES, _SKY_WORDS)
            ground = _choice(raw.get("ground"), GROUNDS, _GROUND_WORDS)
            light = colour(_first(raw, "light", "colour", "color"))
            if sky:
                edit["sky"] = sky
            if ground:
                edit["ground"] = ground
            if light:
                edit["light"] = light
            if raw.get("mist") is not None:
                edit["mist"] = round(_clamp(_number(raw.get("mist"), 0.0), 0.0, 1.0), 2)
            # a sky and a ground of its own, not from the list
            for key in ("sky_top", "sky_horizon", "ground_colour", "mist_colour"):
                tint = colour(raw.get(key))
                if tint:
                    edit[key] = tint
            for key in ("stars", "terrain"):
                if raw.get(key) is not None:
                    edit[key] = round(_clamp(_number(raw.get(key), 0.0), 0.0, 1.0), 2)
            sun = raw.get("sun")
            if isinstance(sun, dict):
                edit["sun"] = {"height": round(_clamp(_number(sun.get("height"), 0.4), -0.2, 1.0), 2),
                               "bearing": round(_number(sun.get("bearing"), 0.0) % 360.0, 1),
                               "colour": colour(sun.get("colour")) or "#fff2d8",
                               "size": round(_clamp(_number(sun.get("size"), 0.3), 0.0, 1.0), 2)}
            if any(k in edit for k in ("sky_top", "sky_horizon", "stars", "sun")) and "sky" not in edit:
                edit["sky"] = PAINTED
            if isinstance(raw.get("whole"), dict):
                edit["whole"] = dict(raw["whole"])      # the room exactly as it was: how an undo puts it back
            if len(edit) == 2:
                refused.append({"index": index, "reason": "room needs a sky, a ground, a light or mist"})
                continue
        if op == "note":
            edit["text"] = _text(_first(raw, "text", "note", "body"), NOTE_MAX)
            if not edit["text"]:
                refused.append({"index": index, "reason": "an empty note"})
                continue
        clean.append(edit)
    return clean, refused


def _props() -> List[Dict[str, Any]]:
    from eli.world import embodiment
    return embodiment.props()


# ── how a thing looks ────────────────────────────────────────────────────────
def _ring_spot(object_id: str) -> Tuple[float, float]:
    """A steady place for a thing nobody placed: on a ring round the middle,
    kept out of the way straight ahead, where a client hangs its own screen."""
    digest = hashlib.sha1(object_id.encode("utf-8")).digest()
    bearing = math.radians(50.0 + 260.0 * digest[0] / 255.0)   # 0 is straight ahead
    radius = 3.6 + 2.2 * digest[1] / 255.0
    return round(radius * math.sin(bearing), 2), round(radius * math.cos(bearing), 2)


def form_of(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Shape, colour, size, glow and position of an object, whether it was built
    with a form or made by the world itself without one."""
    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    form = dict(meta.get("form") or {}) if isinstance(meta.get("form"), dict) else {}
    shape, tint = _TYPE_FORMS.get(str(obj.get("object_type", "")), ("orb", "#9fd8ff"))
    pos = form.get("pos")
    if not (isinstance(pos, (list, tuple)) and len(pos) == 3):
        right, forward = _ring_spot(str(obj.get("object_id", "")))
        pos = [right, 0.0, forward]
    importance = _clamp(_number(obj.get("importance"), 0.5), 0.0, 1.0)
    made = parts(form.get("parts"))
    real = str(form.get("prop") or "")
    out = {
        "shape": PROP if real else MADE if made else (form.get("shape") if form.get("shape") in SHAPES else shape),
        "colour": colour(form.get("colour")) or tint,
        "size": round(_clamp(_number(form.get("size"), 0.6 + 0.8 * importance), MIN_SIZE, MAX_SIZE), 2),
        "glow": round(_clamp(_number(form.get("glow"), 0.25), 0.0, 1.0), 2),
        "pos": [round(_number(p, 0.0), 2) for p in pos],
    }
    if made:
        out["parts"] = made
    if real:
        out["prop"] = real
    if form.get("turn") is not None:
        out["turn"] = round(_number(form.get("turn"), 0.0) % 360.0, 1)
    painted = art(form.get("art"))
    if painted:
        out["art"] = painted
    return out


def mood_of(spaces: Dict[str, Any], room: str = OWN_ROOM) -> Dict[str, Any]:
    info = spaces.get(room) if isinstance(spaces, dict) else None
    mood = dict(DEFAULT_MOOD)
    if isinstance(info, dict) and isinstance(info.get("mood"), dict):
        given = info["mood"]
        if given.get("sky") in SKIES or given.get("sky") == PAINTED:
            mood["sky"] = given["sky"]
        if given.get("ground") in GROUNDS:
            mood["ground"] = given["ground"]
        mood["light"] = colour(given.get("light")) or mood["light"]
        mood["mist"] = round(_clamp(_number(given.get("mist"), mood["mist"]), 0.0, 1.0), 2)
        for key in ("sky_top", "sky_horizon", "ground_colour", "mist_colour"):
            if colour(given.get(key)):
                mood[key] = colour(given.get(key))
        for key in ("stars", "terrain"):
            if given.get(key) is not None:
                mood[key] = round(_clamp(_number(given.get(key), 0.0), 0.0, 1.0), 2)
        if isinstance(given.get("sun"), dict):
            mood["sun"] = dict(given["sun"])
    return mood


# ── from edits to the world's own actions ────────────────────────────────────
def look_of(rooms: Dict[str, Any], room: str) -> Dict[str, Any]:
    """What ELI has given a room of the house: its light. Empty when the room is
    still as it was built."""
    info = rooms.get(room) if isinstance(rooms, dict) else None
    given = info.get("mood") if isinstance(info, dict) else None
    if not isinstance(given, dict) or not colour(given.get("light")):
        return {}
    return {"light": colour(given.get("light"))}


def _relight(state: EliWorldState, edit: Dict[str, Any], room: str) -> Dict[str, Any]:
    """A room of the house takes the light ELI gives it; its sky, ground and air stay its own."""
    info = state.rooms.get(room)
    if not isinstance(info, dict):
        return {"op": "room", "ok": False, "reason": f"there is no room {room!r}"}
    before = look_of(state.rooms, room)
    undo = {"op": "room", "room": room, "whole": before}
    if "whole" in edit:
        whole = {k: v for k, v in edit["whole"].items() if k in HOUSE_LOOK}
        if whole:
            info["mood"] = whole
        else:
            info.pop("mood", None)
        return {"op": "room", "ok": True, "room": room, "inverse": undo}
    given = {k: edit[k] for k in HOUSE_LOOK if k in edit}
    if not given:
        return {"op": "room", "ok": False,
                "reason": "a room of the house keeps its own sky, ground and air: only its light can change"}
    info["mood"] = {**before, **given}
    return {"op": "room", "ok": True, "room": room, "inverse": undo}


def _standing(state: EliWorldState, room: str) -> List[WorldObject]:
    return [o for o in state.objects.values() if o.room == room and not o.retired]


def _find(state: EliWorldState, edit: Dict[str, Any], *, retired: bool = False) -> Optional[WorldObject]:
    """The thing an edit means: by id, else by name in its room, else by name anywhere."""
    wanted = edit.get("id")
    if wanted and wanted in state.objects and state.objects[wanted].retired == retired:
        return state.objects[wanted]
    name = str(edit.get("name") or "").lower()
    if not name:
        return None
    pool = [o for o in state.objects.values() if o.retired == retired]
    pool.sort(key=lambda o: (o.room != edit.get("room"), -o.timestamp))
    for exact in (True, False):
        for obj in pool:
            have = obj.name.lower()
            if (have == name) if exact else (name in have or have in name):
                return obj
    return None


def _new_id(state: EliWorldState, name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "thing"
    seed = f"{stem}:{len(state.objects)}:{state.timestamp}"
    return f"{BUILT}_{stem}_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]}"


def _free_spot(state: EliWorldState, room: str, near: Tuple[float, float], size: float,
               ignore: str = "", avoid: Optional[Any] = None) -> Tuple[float, float]:
    """The nearest place to `near` where a thing of this size does not stand in
    another, nor on the person standing at `avoid`."""
    taken = [(form_of(o.__dict__)["pos"], form_of(o.__dict__)["size"])
             for o in _standing(state, room) if o.object_id != ignore]
    if isinstance(avoid, (list, tuple)) and len(avoid) == 2:
        taken.append(([float(avoid[0]), 0.0, float(avoid[1])], 2.4))      # room to stand and to see it

    def clear(spot: Tuple[float, float]) -> bool:
        return all(math.hypot(spot[0] - pos[0], spot[1] - pos[2]) >= 0.5 * (size + other) + 0.25
                   for pos, other in taken)

    if clear(near):
        return near
    for step in range(1, 60):                       # a spiral outwards
        angle, reach = step * 2.399963, 0.45 * math.sqrt(step) * max(1.0, size)
        spot = (near[0] + reach * math.cos(angle), near[1] + reach * math.sin(angle))
        if math.hypot(*spot) <= REACH and clear(spot):
            return round(spot[0], 2), round(spot[1], 2)
    return near


_ACTION_TYPES = {
    "create": WorldActionType.CREATE_OBJECT.value,
    "move": WorldActionType.MOVE_OBJECT.value,
    "change": WorldActionType.ALTER_OBJECT.value,
    "remove": WorldActionType.RETIRE_OBJECT.value,
    "restore": WorldActionType.CREATE_OBJECT.value,
    "room": WorldActionType.ALTER_ROOM.value,
    "note": WorldActionType.JOURNAL_ENTRY.value,
}


def actions_for(state: EliWorldState, event: WorldEvent) -> List[WorldAction]:
    """The world actions a world_edit event asks for. The action says what is
    done and to what; the words stay in the event."""
    actor = str(event.payload.get("actor") or "eli")
    why = "Asked for by the user." if event.payload.get("asked") else "ELI chose to."
    actions = []
    for index, edit in enumerate(event.payload.get("edits") or []):
        kind = _ACTION_TYPES.get(str(edit.get("op")))
        if kind is None:
            continue
        actions.append(WorldAction(
            action_type=kind, actor=actor, room=str(edit.get("room") or OWN_ROOM), reason=why,
            payload={BUILT: True, "edit": index, "op": edit["op"]},
        ))
    return actions


def apply_action(state: EliWorldState, action: WorldAction, event: WorldEvent) -> None:
    """Carry out one allowed action, and write down beside the edit what
    happened and how to take it back."""
    edits = event.payload.get("edits") or []
    index = int(action.payload.get("edit", -1))
    if not 0 <= index < len(edits):
        return
    edit = edits[index]
    results = event.payload.setdefault("results", [])
    result = _carry_out(state, edit, event)
    result["index"] = index
    results.append(result)
    undone = edit.get("undoes")
    if undone and result.get("ok"):
        for earlier in state.events:
            if earlier.event_type == EVENT_TYPE and earlier.timestamp == undone:
                earlier.payload["undone"] = True


def _carry_out(state: EliWorldState, edit: Dict[str, Any], event: WorldEvent) -> Dict[str, Any]:
    op, room = edit["op"], str(edit.get("room") or OWN_ROOM)
    actor = str(event.payload.get("actor") or "eli")

    if op == "note":
        from eli.world.persistence.journal import append_journal_entry
        append_journal_entry(title="A note from the rooms", body=edit["text"], source="eli_world_building")
        return {"op": op, "ok": True}

    if op == "room":
        if room != OWN_ROOM:
            return _relight(state, edit, room)
        info = state.spaces.setdefault(OWN_ROOM, dict(OWN_ROOM_INFO))
        before = mood_of(state.spaces)
        if "whole" in edit:
            info["mood"] = dict(edit["whole"])
            return {"op": op, "ok": True, "room": room, "inverse": {"op": "room", "room": room, "whole": before}}
        kept = dict(before)
        if edit.get("sky") in SKIES:            # a sky by name takes the place of a painted one
            for key in ("sky_top", "sky_horizon", "stars", "sun"):
                kept.pop(key, None)
        if "ground" in edit:
            kept.pop("ground_colour", None)
        info["mood"] = {**kept, **{k: edit[k] for k in (
            "sky", "ground", "light", "mist", "sky_top", "sky_horizon", "stars", "sun",
            "ground_colour", "mist_colour", "terrain") if k in edit}}
        return {"op": op, "ok": True, "room": room, "inverse": {"op": "room", "room": room, "whole": before}}

    if op == "create":
        if room == OWN_ROOM:
            state.spaces.setdefault(OWN_ROOM, dict(OWN_ROOM_INFO))
        elif room not in state.rooms and room not in state.spaces:
            return {"op": op, "ok": False, "reason": f"there is no room {room!r}"}
        if len(_standing(state, room)) >= MAX_PER_ROOM:
            return {"op": op, "ok": False, "reason": f"the room is full ({MAX_PER_ROOM} things)"}
        size = float(edit.get("size", 1.0))
        near = tuple(edit["at"]) if "at" in edit else tuple(event.payload.get("near") or (0.0, 2.5))
        right, forward = _free_spot(state, room, (float(near[0]), float(near[1])), size,
                                    avoid=event.payload.get("avoid"))
        # two things in a room do not share a name: the second is "Lantern 2"
        taken = {o.name.lower() for o in _standing(state, room)}
        name, count = edit["name"], 2
        while name.lower() in taken:
            name, count = f"{edit['name']} {count}", count + 1
        edit["name"] = name
        object_id = _new_id(state, name)
        form = {"shape": edit.get("shape", "orb"), "colour": edit.get("colour", "#9fd8ff"), "size": size,
                "glow": float(edit.get("glow", 0.25)), "pos": [right, float(edit.get("height", 0.0)), forward]}
        if edit.get("parts"):
            form["parts"] = edit["parts"]
        for key in ("prop", "turn", "art"):
            if key in edit:
                form[key] = edit[key]
        if "turn" not in form and (edit.get("prop") or edit.get("shape") == "painting"):
            # a chair or a picture nobody turned faces the middle of the room
            form["turn"] = round(math.degrees(math.atan2(-right, -forward)) % 360.0, 1) if (right or forward) else 180.0
        links = [dict(link) for link in event.payload.get("links") or [] if isinstance(link, dict)]
        state.objects[object_id] = WorldObject(
            object_id=object_id, name=edit["name"], object_type=BUILT, room=room,
            symbolic_meaning=str(edit.get("meaning", "")), created_by=actor,
            reason=str(event.summary)[:200], metadata={"form": form}, links=links,
        )
        return {"op": op, "ok": True, "id": object_id, "name": edit["name"], "room": room,
                "inverse": {"op": "remove", "id": object_id, "room": room}}

    if op == "restore":
        obj = _find(state, edit, retired=True)
        if obj is None:
            return {"op": op, "ok": False, "reason": "nothing by that name was taken away"}
        obj.retired = False
        return {"op": op, "ok": True, "id": obj.object_id, "name": obj.name, "room": obj.room,
                "inverse": {"op": "remove", "id": obj.object_id, "room": obj.room}}

    obj = _find(state, edit)
    if obj is None:
        return {"op": op, "ok": False, "reason": f"there is no {edit.get('name') or edit.get('id')!r} here"}
    form = form_of(obj.__dict__)

    if op == "remove":
        if obj.object_type != BUILT:
            return {"op": op, "ok": False, "name": obj.name,
                    "reason": "that one belongs to the world itself, not to the building"}
        obj.retired = True
        return {"op": op, "ok": True, "id": obj.object_id, "name": obj.name, "room": obj.room,
                "inverse": {"op": "restore", "id": obj.object_id, "room": obj.room}}

    before = {"op": "change", "id": obj.object_id, "room": obj.room, "shape": form["shape"],
              "colour": form["colour"], "size": form["size"], "glow": form["glow"],
              "at": [form["pos"][0], form["pos"][2]], "height": form["pos"][1], "rename": obj.name}
    if form.get("parts"):
        before["parts"] = form["parts"]
    for key in ("prop", "turn", "art"):
        if key in form:
            before[key] = form[key]
    if "at" in edit:
        avoid = event.payload.get("avoid") if actor != "user" else None    # their own hand put it there
        form["pos"][0], form["pos"][2] = _free_spot(
            state, obj.room, (float(edit["at"][0]), float(edit["at"][1])), float(form["size"]),
            ignore=obj.object_id, avoid=avoid)
    if "height" in edit:
        form["pos"][1] = float(edit["height"])
    if op == "change":
        for key in ("shape", "colour", "size", "glow"):
            if key in edit:
                form[key] = edit[key]
        if edit.get("parts"):
            form["parts"] = edit["parts"]            # remade, part by part
            form.pop("prop", None)
        elif edit.get("prop"):
            form["prop"] = edit["prop"]              # now a real thing
            form.pop("parts", None)
        elif edit.get("shape") in SHAPES:
            form.pop("parts", None)                  # back to a plain shape
            form.pop("prop", None)
        for key in ("turn", "art"):
            if key in edit:
                form[key] = edit[key]
        if edit.get("rename"):
            obj.name = edit["rename"]
        if edit.get("meaning"):
            before["meaning"] = obj.symbolic_meaning
            obj.symbolic_meaning = edit["meaning"]
    obj.metadata = {**(obj.metadata or {}), "form": form}
    return {"op": op, "ok": True, "id": obj.object_id, "name": obj.name, "room": obj.room, "inverse": before}


# ── the way in ───────────────────────────────────────────────────────────────
def _expand_undo(state: EliWorldState, edits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """An undo becomes the edits that take back the last batch not yet taken back."""
    out: List[Dict[str, Any]] = []
    for edit in edits:
        if edit["op"] != "undo":
            out.append(edit)
            continue
        for earlier in reversed(state.events):
            if earlier.event_type != EVENT_TYPE or earlier.payload.get("undone"):
                continue
            inverses = [r["inverse"] for r in earlier.payload.get("results") or [] if r.get("ok") and r.get("inverse")]
            if not inverses or earlier.payload.get("is_undo"):
                continue
            out.extend({**inverse, "undoes": earlier.timestamp} for inverse in reversed(inverses))
            break
    return out


def apply(edits: Any, *, actor: str = "eli", summary: str = "", asked: bool = False,
          default_room: str = OWN_ROOM, near: Optional[Tuple[float, float]] = None,
          avoid: Optional[Tuple[float, float]] = None, links: Optional[List[Dict[str, Any]]] = None,
          source: str = "world_building") -> Dict[str, Any]:
    """Check a batch of edits, put it through the world, and say what happened
    to each. Nothing is half applied in secret: every edit comes back as done,
    or refused with the reason."""
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    from eli.world.local_world_bridge import _get_engine, append_event

    clean, refused = normalise(edits, default_room)
    if not clean:
        return {"ok": False, "results": [], "refused": refused}
    with INGEST_LOCK:
        is_undo = any(e["op"] == "undo" for e in clean)
        clean = _expand_undo(_get_engine().storage.load(), clean)
        if not clean:
            return {"ok": False, "results": [], "refused": refused + [{"index": 0, "reason": "nothing to undo"}]}
        payload: Dict[str, Any] = {"edits": clean, "actor": actor, "asked": bool(asked), "is_undo": is_undo}
        if near is not None:
            payload["near"] = [round(float(near[0]), 2), round(float(near[1]), 2)]
        if avoid is not None:
            payload["avoid"] = [round(float(avoid[0]), 2), round(float(avoid[1]), 2)]
        if links:
            payload["links"] = links        # what in its records each new thing is about
        state = append_event(EVENT_TYPE, source, str(summary)[:200], payload)
    event = (state.get("events") or [{}])[-1]
    results = list((event.get("payload") or {}).get("results") or [])
    done = {r["index"] for r in results}
    for index in range(len(clean)):
        if index not in done:       # the policy did not let it through
            results.append({"index": index, "op": clean[index]["op"], "ok": False,
                            "reason": "the world's policy did not allow it"})
    results.sort(key=lambda r: r["index"])
    for result in results:
        result.pop("inverse", None)
    return {"ok": any(r.get("ok") for r in results), "results": results, "refused": refused}
