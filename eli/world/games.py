"""
eli/world/games.py
──────────────────
Small games ELI makes up for its rooms, and keeps the score of.

A game is things ELI made with rules attached. ELI composes one the way it
composes a thing (eli/world/composer.py): one call to the local model, held to
an exact shape by a grammar, from the room, where the user stands, what is on
ELI's mind and how the user did last time. The pieces are built things in the
room, each marked with its part in the game (metadata "piece"); the game itself
is an object of its own (object_type "game") that carries the rules. A body
(ELI VR) draws the pieces as it draws anything, runs the rules (motion, hits,
catches, sorting, order, finding), keeps the score, and when the game ends
posts the result (POST /v1/world/played). The result is kept on the game
object, written in ELI's journal, remembered, and passed through ELI's world as
a moment of its own (eli/world/urges.py): "how did I do" has a true answer, and
the next game can be ELI's answer to the last.

Five mechanics, which ELI combines with pieces of its own choosing:

  targets  pieces appear round the user one after another; each one hit scores
           (pointed at and triggered, or touched), one left too long is a miss
  catch    pieces come down from above one after another; each one caught in
           the hand scores, one that reaches the floor is a miss
  sort     pieces of two or more kinds, and a mat for each kind; a piece let go
           on its mat scores, on the wrong one is a miss
  order    the pieces light up in a sequence, one longer each round; hit them
           in that order
  hide     one piece is hidden somewhere in the room each round; find it, by
           looking at it or touching it

What a game is made of is ELI's: its name, what the pieces are, their colours,
how many, how long, how quick, what ELI says. What the body derives from that
(how many are up at once, how fast they move, how long each stays) comes from
those numbers and the room, never from a table of its own.

    wants_game(text)         does this ask for a game
    wants_stop(text)         does this ask for the game to stop
    kind_named(text)         the mechanic the sentence names, if one
    grammar_text(...)        the shape the model's answer is held to
    compose(text, scene)     ask the model, set the game in the room, say what it is
    current(room)            the game set or being played in a room, if any
    played(game_id, result)  the body saying how it went: kept, journalled, remembered
    stop(room)               the user giving up
    record(limit)            the last games and how they went, newest first
"""
from __future__ import annotations

import hashlib
import math
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from eli.utils.log import get_logger
from eli.world import building, composer, embodiment

log = get_logger(__name__)

KINDS = ("targets", "catch", "sort", "order", "hide")
ROLES = {"targets": "target", "catch": "catch", "sort": "piece", "order": "light", "hide": "hidden"}
MAT = "mat"                           # the role of a sorting mat
MOTIONS = ("still", "drift", "orbit")  # how targets move, when they do
# Shapes small enough to be a piece of a game.
PIECE_SHAPES = ("orb", "block", "crystal", "cone", "ring", "lamp")
# Limits of the protocol, the same way as building.MAX_PARTS: what one room and one body bear.
MAX_KINDS = 4                         # kinds of piece in one game
MAX_PIECES = 24                       # pieces in all
MIN_SECONDS, MAX_SECONDS = 10, 600
LINE_MAX = 120
KEPT = 40                             # finished games whose results are kept in the world
STATUSES = ("set", "stopping", "done")
EVENT_PLAYED = "game_played"
SOURCE = "games"

# ── what is asked ────────────────────────────────────────────────────────────
_PLAY = re.compile(
    r"\b(?:let'?s|lets|shall we|can we|could we|want to|wanna|like to|fancy)\s+(?:all\s+)?play\b"
    r"|\bplay\s+(?:a|some|another|one more|a quick|a little|a small|a new|a different|the)\s+(?:\w+\s+){0,2}?(?:game|round)s?\b"
    r"|\b(?:make|set|build|create|invent|design|think)\s+(?:up\s+)?(?:me\s+|us\s+)?(?:a|another|one more|some)\s+"
    r"(?:\w+\s+){0,3}?games?\b"
    r"|\b(?:a|another)\s+(?:quick\s+|little\s+|small\s+)?game\s+(?:please|then|now)\b"
    r"|\bgame time\b|\bchallenge me\b|\bsomething to play\b|\brematch\b|\banother (?:go|round)\b|\bplay again\b"
    r"|\b(?:let'?s|lets)\s+(?:do|have)\s+(?:a|another)\s+(?:\w+\s+){0,2}?(?:game|round)\b"
    r"|\bhide\s+(?:something|one|a\s+\w+|one\s+of\s+your\s+\w+)\s+(?:for\s+me\s+)?(?:to\s+find|and\s+i['’]?ll\s+find\s+it|somewhere)\b"
    r"|\bhide\s+and\s+seek\b|\bsimon\s+says\b"
    r"|\b(?:give\s+me|throw\s+me|i\s+want|i'?d\s+like|i\s+need)\s+(?:some\s+|a\s+few\s+|a\s+couple\s+of\s+|an?\s+|the\s+)?(?:\w+\s+)?"
    r"(?:targets?|things?|something|lights?|orbs?|balls?|pieces?|objects?|lanterns?|crystals?|blocks?|stuff)\s+to\s+(?:hit|shoot|catch|find|sort)\b"
    r"|\bsomething\s+to\s+(?:catch|find|sort|hit|shoot)\b",
    re.I)
_NOT_PLAY = re.compile(
    r"\b(?:music|song|songs|track|album|playlist|radio|podcast|spotify|youtube|movie|film|steam|board game|card game|"
    r"chess|poker|text game|20 questions|twenty questions|trivia|quiz)\b|\bvideo\b(?!\s+game)", re.I)
_STOP = re.compile(
    r"\b(?:stop|end|quit|finish|cancel|abandon|give up on|enough of|pause)\s+(?:the|this|that|our|your)\s+(?:game|round|playing)\b"
    r"|\bi give up\b|\bgame over\b|\bstop playing\b|\bno more games?\b|\b(?:let'?s|lets) stop\b", re.I)
_KIND_WORDS = (
    ("targets", r"\b(?:targets?|shoot|shooting|hit the|pop the|tap the|whack|zap|blast)\b"),
    ("catch", r"\b(?:catch|catching|falling)\b"),
    ("sort", r"\b(?:sort|sorting|matching|match the|tidy|put (?:them|these|the \w+) (?:on|in|into) (?:the|their))\b"),
    ("order", r"\b(?:order|sequence|simon|pattern|memory game|remember the)\b"),
    ("hide", r"\b(?:hide|hidden|hide and seek|find|seek|hunt|look for|search for)\b"),
)


def wants_game(text: str) -> bool:
    text = str(text or "")
    return bool(_PLAY.search(text)) and not _NOT_PLAY.search(text)


def wants_stop(text: str) -> bool:
    return bool(_STOP.search(str(text or "")))


def kind_named(text: str) -> str:
    """The mechanic a sentence names ("let's play catch"), or ""."""
    for kind, words in _KIND_WORDS:
        if re.search(words, str(text or ""), re.I):
            return kind
    return ""


# ── the shape the answer is held to ──────────────────────────────────────────
def grammar_text(kinds: Sequence[str] = KINDS, props: Sequence[str] = (), bounded: bool = True) -> str:
    """A llama.cpp grammar for one game. `kinds`: the mechanics the answer may choose from (one,
    when the user named it). `props`: the real things the body draws, which may be pieces."""
    def text(limit: int) -> str:
        return '"\\"" char%s "\\""' % ("{1,%d}" % limit if bounded else "+")

    allowed = [k for k in kinds if k in KINDS] or list(KINDS)
    real = [p for p in props if re.fullmatch(r"[A-Za-z0-9_-]+", str(p))]
    more = "{0,%d}" % (MAX_KINDS - 1) if bounded else "*"
    lines = [
        'root ::= "{" ws "\\"say\\":" ws say "," ws "\\"game\\":" ws game ws "}"',
        'game ::= "{\\"name\\":" name ",\\"kind\\":" kind ",\\"goal\\":" goal ",\\"pieces\\":[" piece ("," piece)%s "]" '
        '(",\\"mats\\":[" mat ("," mat)%s "]")? ",\\"count\\":" count ",\\"seconds\\":" seconds ",\\"pace\\":" unit '
        '(",\\"motion\\":" motion)? ",\\"lines\\":{\\"start\\":" line ",\\"hit\\":" line ",\\"miss\\":" line '
        '",\\"end\\":" line "}}"' % (more, more),
        "piece ::= shaped" + (" | real" if real else ""),
        'shaped ::= "{\\"name\\":" name ",\\"shape\\":" shape ",\\"colour\\":" colour ",\\"size\\":" number '
        '(",\\"glow\\":" unit)? ",\\"tag\\":" tag "}"',
        'real ::= "{\\"name\\":" name ",\\"prop\\":" prop ",\\"colour\\":" colour (",\\"size\\":" number)? ",\\"tag\\":" tag "}"',
        'mat ::= "{\\"name\\":" name ",\\"colour\\":" colour ",\\"tag\\":" tag "}"',
        "kind ::= " + composer._quoted(allowed),
        "motion ::= " + composer._quoted(MOTIONS),
        'tag ::= "\\"" [a-z] [a-z0-9]%s "\\""' % ("{0,11}" if bounded else "*"),
        "count ::= [1-9] [0-9]?",
        "seconds ::= [1-9] [0-9] [0-9]?",
        'unit ::= "1" | "0" ("." [0-9] [0-9]?)?',
        'number ::= [0-9] ("." [0-9] [0-9]?)?',
        "say ::= " + text(composer.SAY_MAX // 2),
        "name ::= " + text(building.NAME_MAX),
        "goal ::= " + text(building.MEANING_MAX),
        "line ::= " + text(LINE_MAX),
        'char ::= [^"\\\\\\x00-\\x1F]',
        "shape ::= " + composer._quoted(PIECE_SHAPES),
        "prop ::= " + (composer._quoted(real) if real else '"\\"none\\""'),
        'colour ::= %s | "\\"#" hex hex hex hex hex hex "\\""' % composer._quoted(building.COLOURS),
        "hex ::= [0-9a-fA-F]",
        "ws ::= [ \\n]?",
    ]
    return "\n".join(lines) + "\n"


_EXPLAINED = {
    "targets": "pieces appear round the user one after another; each one hit scores (pointed at with the trigger, "
               "or touched), one left too long is a miss",
    "catch": "pieces come down from above one after another; each one caught in the hand scores, one that reaches "
             "the floor is a miss",
    "sort": "pieces of two or more kinds (by tag), and a mat for each kind; a piece let go on its mat scores, on "
            "the wrong one is a miss",
    "order": "the pieces light up in a sequence, one longer each round; the user hits them in that order",
    "hide": "one piece is hidden somewhere in the room each round; the user finds it by looking at it or touching it",
}


def build_prompt(text: Optional[str], scene: Optional[Dict[str, Any]], state: Dict[str, Any], room: str,
                 past: Sequence[Dict[str, Any]] = (), props: Sequence[Dict[str, Any]] = (),
                 kinds: Sequence[str] = KINDS) -> str:
    own = room == building.OWN_ROOM
    name = (scene or {}).get("room_name") or ((state.get("rooms") or {}).get(room) or {}).get("name") \
        or (building.OWN_ROOM_INFO["name"] if own else room.replace("_", " ").title())
    user = (scene or {}).get("user") or {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0]}
    kinds = [k for k in kinds if k in KINDS] or list(KINDS)
    parts = [
        f"You are in ELI VR, in {embodiment.the(name)}, with the user, and you are making up a small game for the "
        "two of you to play there, right now.",
        "The user can look round, walk a little, point at a thing and pull the trigger, grab a thing with the grip and "
        f"let it go. They stand at [{user['pos'][0]}, {user['pos'][2]}] (metres on the floor from the middle of the room).",
        "",
    ]
    if len(kinds) == 1:
        parts.append(f"The kind of game: {kinds[0]}: {_EXPLAINED[kinds[0]]}.")
    else:
        parts.append("The kinds of game you can make:")
        parts += [f"- {k}: {_EXPLAINED[k]}" for k in kinds]
    parts.append("")
    if text:
        parts.append(f'The user said: "{text.strip()[:500]}"')
    if past:
        parts.append("How the games went before, newest first:")
        for game in past[:5]:
            parts.append(f"- {game['name']} ({game['kind']}, {game['room']}): "
                         + ("stopped early, " if game.get("abandoned") else "")
                         + f"{game['score']} {_noun(game['kind'])}, {game['misses']} misses, {game['seconds']} s, "
                         + game.get("when", ""))
    else:
        parts.append("You have not played together before.")
    parts += composer.mindset(state)
    parts += [
        "",
        'Answer as JSON: "say" (one or two short sentences to the user: what the game is and what to do), then "game": '
        "name; kind; goal (one sentence: what to do); pieces (one to four kinds of piece, each with a name, a shape ("
        + ", ".join(PIECE_SHAPES) + ")" + (", or a real thing by its prop id" if props else "")
        + ", a colour (" + ", ".join(building.COLOURS) + ", or #rrggbb), size (1 is ordinary; a small thing is harder "
        "to hit), glow (0 none, 1 bright), and a tag, one short word); for sort, mats, one for each tag (name, colour, "
        f"tag); count (how many pieces in all, up to {MAX_PIECES}); seconds (how long the game lasts, {MIN_SECONDS} "
        f"to {MAX_SECONDS}); pace (0 slow, 1 quick); for targets, motion (still, drift, orbit); and lines: what you "
        "say at the start, at a hit, at a miss, and at the end.",
        "Make it about something the two of you share if there is one: a memory, a goal, this room. If the user did "
        "well last time, make it a little harder; if badly, a little kinder. Say the goal plainly.",
    ]
    if props:
        parts.append("Real things you can use as pieces, by prop id: " + "; ".join(
            f'{p["id"]} ({p["name"]}, {p["size"][1]} m tall)' for p in props[:40]) + ".")
    return "\n".join(parts)


# ── reading what the model made ──────────────────────────────────────────────
def _tag(value: Any, fallback: str) -> str:
    word = re.sub(r"[^a-z0-9]", "", str(value or "").lower())[:12]
    return word or fallback


def _piece(raw: Any, index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = {
        "name": building._text(building._first(raw, "name", "title"), building.NAME_MAX) or f"Piece {index + 1}",
        "colour": building.colour(building._first(raw, "colour", "color")) or "#9fd8ff",
        "size": round(building._clamp(building._number(building._first(raw, "size", "scale"), 1.0),
                                      building.MIN_SIZE, building.MAX_SIZE), 2),
        "glow": round(building._clamp(building._number(raw.get("glow"), 0.3), 0.0, 1.0), 2),
        "tag": _tag(raw.get("tag"), f"p{index + 1}"),
    }
    real = building.prop_of(building._first(raw, "prop", "model"))
    shape = building._choice(building._first(raw, "shape", "kind", "type"), PIECE_SHAPES, building._SHAPE_WORDS)
    if real:
        out["prop"] = real
    else:
        out["shape"] = shape if shape in PIECE_SHAPES else "orb"
    return out


def _plausible(value: Any, prompt: str, limit: int) -> str:
    """Words of ELI's own: not a line of the prompt handed back, not punctuation alone."""
    words = building._text(value, limit)
    if len(re.sub(r"[^a-z]", "", words.lower())) < 2 or words[0] in ">-*#.|[":
        return ""
    plain = words.lower().strip(" .:;,!?\"'")
    for line in prompt.splitlines():
        line = line.strip().lstrip("-*> ").lower().strip(" .:;,!?\"'")
        if len(line) >= 12 and (plain == line or (plain in line and len(plain) >= 16)):
            return ""
    return words


def normalise(raw: Any, text: str = "", room_left: int = building.MAX_PER_ROOM, prompt: str = "") -> Dict[str, Any]:
    """A game as the model gave it, made exact. `room_left`: how many things the room can still
    take. `prompt`: what the model was asked, so that a name or a line that only hands the
    prompt back is dropped (a small model does that). Raises ComposeError (bad_output) when there
    is no game in it, or no room for one."""
    if not isinstance(raw, dict):
        raise composer.ComposeError("bad_output", "the model gave no game")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        kind = kind_named(text) or kind_named(kind) or "targets"
    pieces = [p for p in (_piece(item, i) for i, item in enumerate(raw.get("pieces") or [])) if p][:MAX_KINDS]
    if not pieces:
        pieces = [_piece({"name": "Light", "shape": "orb", "colour": "amber", "glow": 0.8}, 0)]
    if kind == "hide":
        pieces = pieces[:1]
    if kind == "sort":
        if len(pieces) < 2:
            second = dict(pieces[0])
            second["name"], second["colour"] = second["name"] + " 2", "#f08a3c" if pieces[0]["colour"] != "#f08a3c" else "#4c7df0"
            pieces.append(second)
        seen: Dict[str, int] = {}
        for i, piece in enumerate(pieces):          # two kinds never share a tag
            if piece["tag"] in seen:
                piece["tag"] = f"{piece['tag']}{i + 1}"
            seen[piece["tag"]] = i
    count = int(building._clamp(building._number(raw.get("count"), len(pieces)), 1, MAX_PIECES))
    if kind == "sort":
        count = max(count, len(pieces))
    if kind == "order":
        count = max(count, 2)
    mats: List[Dict[str, Any]] = []
    if kind == "sort":
        given = {}
        for item in raw.get("mats") or raw.get("zones") or []:
            if isinstance(item, dict):
                given[_tag(item.get("tag"), "")] = item
        for piece in pieces:
            item = given.get(piece["tag"], {})
            mats.append({"name": building._text(item.get("name"), building.NAME_MAX) or f"{piece['name']} mat",
                         "colour": building.colour(building._first(item, "colour", "color")) or piece["colour"],
                         "tag": piece["tag"]})
    room_left = int(room_left) - 1 - len(mats)       # the game itself and its mats stand in the room too
    if room_left < 1:
        raise composer.ComposeError("bad_output", f"the room is full ({building.MAX_PER_ROOM} things)")
    count = min(count, room_left) if kind != "hide" else count
    lines_raw = raw.get("lines") if isinstance(raw.get("lines"), dict) else {}
    lines = {key: _plausible(lines_raw.get(key), prompt, LINE_MAX) for key in ("start", "hit", "miss", "end")}
    spec: Dict[str, Any] = {
        "name": _plausible(building._first(raw, "name", "title"), prompt, building.NAME_MAX) or f"{kind.title()} game",
        "kind": kind,
        "goal": _plausible(building._first(raw, "goal", "aim", "rules"), prompt, building.MEANING_MAX),
        "pieces": pieces,
        "count": count,
        "seconds": int(building._clamp(building._number(raw.get("seconds"), 60), MIN_SECONDS, MAX_SECONDS)),
        "pace": round(building._clamp(building._number(raw.get("pace"), 0.5), 0.0, 1.0), 2),
        "lines": lines,
    }
    if mats:
        spec["mats"] = mats
    if kind == "targets":
        motion = str(raw.get("motion") or "").strip().lower()
        spec["motion"] = motion if motion in MOTIONS else "still"
    return spec


# ── where the pieces stand to begin with ─────────────────────────────────────
def layout(spec: Dict[str, Any], scene: Optional[Dict[str, Any]], seed: str) -> Dict[str, Any]:
    """Where each piece and mat stands when the game is set, as [right, forward] and a height,
    from where the user stands and faces. Targets and catches are spread round the user, inside the
    room, where a hand or a pointer reaches; sorting pieces stand in a row before the user with the
    mats to either side; the lights of an order game stand in an arc; a hidden piece is somewhere in
    the room, not where the user is. The body moves them from there."""
    user = (scene or {}).get("user") or {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0]}
    px, pz, eye = float(user["pos"][0]), float(user["pos"][2]), max(1.0, float(user["pos"][1]))
    fr, ff = float(user["facing"][0]), float(user["facing"][1])
    length = math.hypot(fr, ff) or 1.0
    fr, ff = fr / length, ff / length
    rng = random.Random(seed)
    kind, count = spec["kind"], int(spec["count"])
    reach = building.REACH

    def at(ahead: float, aside: float = 0.0) -> List[float]:
        spot = [px + ahead * fr + aside * ff, pz + ahead * ff - aside * fr]
        far = math.hypot(*spot)
        if far > reach:
            spot = [spot[0] * reach / far, spot[1] * reach / far]
        return [round(spot[0], 2), round(spot[1], 2)]

    def around(low: float, high: float, spread_deg: float) -> List[float]:
        bearing = math.radians(rng.uniform(-spread_deg, spread_deg))
        distance = rng.uniform(low, high)
        return at(distance * math.cos(bearing), distance * math.sin(bearing))

    pieces: List[Dict[str, Any]] = []
    widest = max(float(p["size"]) for p in spec["pieces"])
    if kind == "targets":
        for _ in range(count):
            pieces.append({"at": around(1.5, reach * 0.5, 120.0), "height": round(rng.uniform(eye * 0.4, eye * 1.2), 2)})
    elif kind == "catch":
        for _ in range(count):
            pieces.append({"at": around(1.5, reach * 0.4, 90.0), "height": building.MAX_HEIGHT})
    elif kind == "sort":
        step = widest + 0.4
        row = 0.5 * (widest + building.STANDING_ROOM) + 0.5     # clear of where the user stands
        per_row = max(1, int((reach * 0.8) // step))             # a row no wider than the room is deep
        for i in range(count):
            r, c = divmod(i, per_row)
            in_row = min(per_row, count - r * per_row)
            pieces.append({"at": at(row + r * step, (c - (in_row - 1) / 2.0) * step), "height": 0.0})
    elif kind == "order":
        span = math.radians(120.0)
        for i in range(count):
            angle = -span / 2.0 + span * (i / max(1, count - 1)) if count > 1 else 0.0
            pieces.append({"at": at(2.5 * math.cos(angle), 2.5 * math.sin(angle)), "height": round(eye * 0.8, 2)})
    else:                                                   # hide: anywhere, not here
        pieces.append({"at": around(2.5, reach * 0.8, 180.0), "height": round(rng.uniform(0.0, eye), 2)})
    mats = []
    if spec.get("mats"):
        step = widest + 0.4
        row = 0.5 * (widest + building.STANDING_ROOM) + 0.5
        half = (min(count, max(1, int((reach * 0.8) // step))) - 1) / 2.0 * step
        clear = half + 0.5 * (widest + 1.0) + 0.6              # past the end of the row, with room between
        for i, _mat in enumerate(spec["mats"]):
            side = (i % 2 * 2 - 1) * (clear + 1.6 * (i // 2))  # left, right, further left, further right
            mats.append({"at": at(row, side), "height": 0.0})
    board = {"at": at(reach * 0.45), "height": round(eye * 0.95, 2)}   # the score, out of the way of the pieces
    return {"pieces": pieces, "mats": mats, "board": board}


# ── the way in ───────────────────────────────────────────────────────────────
def _store():
    from eli.world.local_world_bridge import _get_engine
    return _get_engine().storage


def _new_id(name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "game"
    seed = f"{stem}:{time.time()}:{random.random()}"
    return f"game_{stem}_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]}"


def _games(state: Any) -> List[Any]:
    return [o for o in state.objects.values() if o.object_type == building.GAME]


def current(room: str = "", state: Any = None) -> Optional[Dict[str, Any]]:
    """The game set or being played (status set or stopping) in a room, or in any room with no room
    given: {"id", "room", "status", "game"}. None when there is none."""
    state = state if state is not None else _store().load()
    for obj in _games(state):
        meta = obj.metadata or {}
        if obj.retired or meta.get("status") not in ("set", "stopping"):
            continue
        if room and obj.room != room:
            continue
        return {"id": obj.object_id, "room": obj.room, "status": meta.get("status"), "game": dict(meta.get("game") or {})}
    return None


def compose(text: Optional[str], scene: Optional[Dict[str, Any]] = None, *,
            generate: Optional[Callable[[str], Any]] = None,
            state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Ask ELI for a game for the room the user is in, set it there, and give back what ELI says
    and what the game is. A game already set in that room gives way."""
    text = str(text or "").strip()
    room = (scene or {}).get("room") or building.OWN_ROOM
    world = state if state is not None else composer._load_world()
    if room != building.OWN_ROOM and room not in (world.get("rooms") or {}):
        room = building.OWN_ROOM
    standing = composer._standing(world, room)
    named = kind_named(text)
    kinds = (named,) if named else KINDS
    real = embodiment.props()
    prompt = build_prompt(text, scene, world, room, record(5), real, kinds)
    ids = [p["id"] for p in real]
    ask = generate or (lambda p: composer._generate(p, grammar_text(kinds, ids), grammar_text(kinds, ids, bounded=False)))
    data: Any = None
    for attempt in (1, 2):
        try:
            data = ask(prompt)
        except Exception as failure:  # noqa: BLE001 - told apart below, never raised raw
            message = str(failure)
            if re.search(r"not available|not initialized|no model", message, re.I):
                raise composer.ComposeError("model_unavailable", message) from failure
            if attempt == 2:
                raise composer.ComposeError("failed", message) from failure
            continue
        if isinstance(data, dict):
            break
    if not isinstance(data, dict):
        raise composer.ComposeError("bad_output", "the model did not answer with an object")
    spec = normalise(data.get("game"), text, building.MAX_PER_ROOM - len(standing), prompt)
    if named and spec["kind"] != named:
        spec = normalise({**(data.get("game") or {}), "kind": named}, text, building.MAX_PER_ROOM - len(standing), prompt)
    say = " ".join(str(data.get("say") or "").split())[:composer.SAY_MAX]
    if say.lower().strip(" .!?") == text.lower().strip(" .!?"):
        say = ""                        # the user's own words handed back are not an answer

    old = current(room)
    if old is not None:
        played(old["id"], {"abandoned": True}, said="another game was set in its place")
    game_id = _new_id(spec["name"])
    places = layout(spec, scene, game_id)
    user = (scene or {}).get("user") or {}
    avoid = (user["pos"][0], user["pos"][2]) if user.get("pos") else None
    edits: List[Dict[str, Any]] = [{
        "op": "create", "id": game_id, "name": spec["name"], "shape": "board", "colour": spec["pieces"][0]["colour"],
        "size": 1.0, "glow": 0.3, "at": places["board"]["at"], "height": places["board"]["height"],
        "meaning": spec["goal"], "game": spec,
    }]
    for i, mat in enumerate(spec.get("mats") or []):
        edits.append({"op": "create", "name": mat["name"], "shape": "mat", "colour": mat["colour"], "size": 1.0,
                      "glow": 0.5, "at": places["mats"][i]["at"], "meaning": f"where the {mat['tag']} pieces go",
                      "piece": {"game": game_id, "role": MAT, "tag": mat["tag"], "index": i}})
    for i, place in enumerate(places["pieces"]):
        kind_of = spec["pieces"][i % len(spec["pieces"])]
        edit = {"op": "create", "name": kind_of["name"], "colour": kind_of["colour"], "size": kind_of["size"],
                "glow": kind_of["glow"], "at": place["at"], "height": place["height"], "meaning": spec["goal"],
                "piece": {"game": game_id, "role": ROLES[spec["kind"]], "tag": kind_of["tag"], "index": i}}
        if kind_of.get("prop"):
            edit["prop"] = kind_of["prop"]
        else:
            edit["shape"] = kind_of["shape"]
        edits.append(edit)
    results: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    for start in range(0, len(edits), building.MAX_EDITS):
        out = building.apply(edits[start:start + building.MAX_EDITS], actor="eli", asked=True, default_room=room,
                             summary=f"A game: {spec['name']}"[:200], source=SOURCE, avoid=avoid, final=True)
        results += out["results"]
        refused += out["refused"]
    if not results or not results[0].get("ok"):
        reason = (results[0].get("reason") if results else "") or (refused[0]["reason"] if refused else "nothing was set")
        raise composer.ComposeError("failed", f"the game could not be set: {reason}")
    made = [r for r in results if r.get("ok")]
    if len(made) < len(edits):
        log.warning("a game was set short of %d of its pieces", len(edits) - len(made))
    if not say:
        say = spec["goal"] or f"{spec['name']}: ready when you are."
    return {"kind": "game", "text": say, "room": room, "asked": True,
            "game": {"id": game_id, "name": spec["name"], "kind": spec["kind"], "goal": spec["goal"],
                     "count": spec["count"], "seconds": spec["seconds"]},
            "results": results, "refused": refused}


# ── how it went ──────────────────────────────────────────────────────────────
def _noun(kind: str) -> str:
    return {"targets": "hits", "catch": "catches", "sort": "sorted right", "order": "rounds", "hide": "found"}.get(kind, "points")


def _result(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    out = {key: int(max(0.0, building._number(raw.get(key), 0.0))) for key in ("score", "misses", "rounds")}
    out["seconds"] = int(max(0.0, building._number(raw.get("seconds"), 0.0)))
    out["abandoned"] = bool(raw.get("abandoned"))
    if raw.get("reason"):
        out["reason"] = building._text(raw.get("reason"), building.MEANING_MAX)
    return out


def _sentence(spec: Dict[str, Any], room: str, state: Any, result: Dict[str, Any]) -> str:
    info = (state.rooms or {}).get(room) if room != building.OWN_ROOM else None
    where = (info or {}).get("name") or (building.OWN_ROOM_INFO["name"] if room == building.OWN_ROOM
                                        else room.replace("_", " ").title())
    score = f"{result['score']} {_noun(spec['kind'])}"
    misses = f"{result['misses']} miss{'es' if result['misses'] != 1 else ''}"
    if result["abandoned"]:
        return (f"We stopped {spec['name']} ({spec['kind']}) in {embodiment.the(where)} after {result['seconds']} "
                f"seconds, at {score} and {misses}.")
    return f"You played {spec['name']} ({spec['kind']}) in {embodiment.the(where)}: {score} and {misses} in {result['seconds']} seconds."


def record(limit: int = 10, state: Any = None) -> List[Dict[str, Any]]:
    """The last finished games and how they went, newest first."""
    state = state if state is not None else _store().load()
    done = []
    for obj in _games(state):
        meta = obj.metadata or {}
        if meta.get("status") != "done" or not isinstance(meta.get("result"), dict):
            continue
        spec, result = meta.get("game") or {}, meta["result"]
        ended = float(meta.get("ended") or obj.timestamp)
        done.append({"id": obj.object_id, "name": str(spec.get("name") or obj.name), "kind": str(spec.get("kind") or ""),
                     "room": obj.room, "score": int(result.get("score", 0)), "misses": int(result.get("misses", 0)),
                     "rounds": int(result.get("rounds", 0)), "seconds": int(result.get("seconds", 0)),
                     "abandoned": bool(result.get("abandoned")), "ended": ended,
                     "when": time.strftime("%a %d %b %Y %H:%M", time.localtime(ended))})
    done.sort(key=lambda g: -g["ended"])
    return done[:limit]


def best_so_far(kind: str, score: int, state: Any = None) -> bool:
    """Whether a score beats every finished game of this kind before it."""
    before = [g["score"] for g in record(KEPT, state) if g["kind"] == kind and not g["abandoned"]]
    return score > 0 and all(score > s for s in before)


def played(game_id: str, result: Any, *, said: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """The body saying how a game went. The result is kept on the game, its pieces leave the room
    (through the world's policy, and are not kept), the game is written in ELI's journal, remembered,
    and passed through ELI's world as a moment of its own. A game already finished is left as it is."""
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    store = _store()
    state = store.load()
    obj = state.objects.get(str(game_id or ""))
    if obj is None or obj.object_type != building.GAME:
        raise ValueError(f"there is no game {game_id!r}")
    meta = dict(obj.metadata or {})
    spec = dict(meta.get("game") or {})
    if meta.get("status") == "done":
        return {"ok": True, "text": "", "already": True, "result": meta.get("result"), "best": False}
    clean = _result(result)
    if said and not clean.get("reason"):
        clean["reason"] = said
    pieces = [o.object_id for o in state.objects.values()
              if not o.retired and str(((o.metadata or {}).get("piece") or {}).get("game")) == obj.object_id]
    sentence = _sentence(spec, obj.room, state, clean)
    best = not clean["abandoned"] and best_so_far(spec.get("kind", ""), clean["score"], state)
    building.apply([{"op": "remove", "id": p} for p in pieces] + [{"op": "remove", "id": obj.object_id}],
                   actor="eli", asked=False, default_room=obj.room, summary=f"{spec.get('name', 'The game')} ended"[:200],
                   source=SOURCE, final=True)
    ended = time.time() if now is None else now
    with INGEST_LOCK:
        state = store.load()
        for piece in pieces:
            state.objects.pop(piece, None)                  # a game's pieces are not kept once it is over
        game = state.objects.get(obj.object_id)
        if game is not None:
            game.metadata = {**(game.metadata or {}), "status": "done", "result": clean, "ended": ended}
        finished = sorted((o for o in _games(state) if (o.metadata or {}).get("status") == "done"),
                          key=lambda o: float((o.metadata or {}).get("ended") or o.timestamp))
        for old in finished[:-KEPT]:
            state.objects.pop(old.object_id, None)
        store.save(state)
    try:
        from eli.world.persistence.journal import append_journal_entry
        append_journal_entry(title=f"A game: {spec.get('name', 'untitled')}", body=sentence, source="eli_world_games")
    except OSError:
        log.warning("a game could not be journalled", exc_info=True)
    try:
        from eli.memory.memory import get_memory
        get_memory().store_memory(sentence, tags=["game", "play", "vr"], source=SOURCE, kind="event")
    except Exception:
        log.warning("a game could not be remembered", exc_info=True)
    from eli.world.world_event_bus import fire_world_event
    fire_world_event(EVENT_PLAYED, SOURCE, sentence, {"game": obj.object_id, "kind": spec.get("kind"), "result": clean})
    text = ""
    if best:
        text = f"That is your best at {spec.get('kind', 'this')} so far."
    return {"ok": True, "text": text, "result": clean, "best": best, "said": sentence}


def stop(room: str = "", said: str = "") -> Dict[str, Any]:
    """The user giving a game up. With a body there to say the score, the game is marked as stopping
    and the body posts how far it got; with none, it is finished here as abandoned."""
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    game = current(room)
    if game is None:
        return {"kind": "game", "text": "There is no game on.", "stopped": False, "results": [], "refused": []}
    if embodiment.present() and game["status"] == "set":
        with INGEST_LOCK:
            store = _store()
            state = store.load()
            obj = state.objects.get(game["id"])
            if obj is not None:
                obj.metadata = {**(obj.metadata or {}), "status": "stopping"}
                store.save(state)
        return {"kind": "game", "text": f"Stopping {game['game'].get('name', 'the game')}.", "stopped": True,
                "game": {"id": game["id"]}, "results": [], "refused": []}
    out = played(game["id"], {"abandoned": True}, said=said or "the user stopped it")
    return {"kind": "game", "text": f"Stopped {game['game'].get('name', 'the game')}.", "stopped": True,
            "game": {"id": game["id"]}, "results": [], "refused": [], "said": out.get("said", "")}
