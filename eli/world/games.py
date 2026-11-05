"""
eli/world/games.py
──────────────────
Small games ELI sets in its rooms, and keeps the score of.

A game is things ELI made with rules attached. It is set the moment it is asked
for: the kind, the pieces, how many, how long and how quick come from rules and
from how the user did last time (a game gone well is a little harder, one gone
badly a little kinder), so nobody waits on a model to play. ELI's own words for
it (its name, what it is, what ELI says at the start, at a hit, at a miss and at
the end) come after, from the local model in the background, held to an exact
shape by a grammar, and are put on the game when they arrive; with no model in
memory the game plays without them. The pieces are built things in the room,
each marked with its part in the game (metadata "piece"); the game itself is an
object of its own (object_type "game") that carries the rules. A body (ELI VR)
draws the pieces as it draws anything, runs the rules (motion, hits, catches,
sorting, order, finding, shooting), keeps the score, and when the game ends
posts the result (POST /v1/world/played). The result is kept on the game
object, written in ELI's journal, remembered, and passed through ELI's world as
a moment of its own (eli/world/urges.py): "how did I do" has a true answer, and
the next game answers the last.

Six mechanics:

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
  range    a table of guns (real, energy and sci-fi: ARMS) stands beside the
           user and targets come up ahead; a target shot scores by the ring it
           was hit in (RINGS: 3 in the middle, then 2, then 1), one left too
           long is a miss

What the body derives from the rules (how many pieces are up at once, how fast
they move, how long each stays, how a gun fires) comes from the numbers here and
the room, never from a table of its own.

    wants_game(text)         does this ask for a game
    wants_stop(text)         does this ask for the game to stop
    kind_named(text)         the mechanic the sentence names, if one
    rules_for(kind, past)    how many, how long, how quick: the first game's, then the record's
    quick(kind, ...)         a game by the rules, set at once
    compose(text, scene)     set the game where the user stands; ELI's words follow
    words_grammar()          the shape ELI's words for a game are held to
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
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from eli.utils.log import get_logger
from eli.world import building, composer, embodiment

log = get_logger(__name__)

KINDS = ("targets", "catch", "sort", "order", "hide", "range")
ROLES = {"targets": "target", "catch": "catch", "sort": "piece", "order": "light", "hide": "hidden", "range": "target"}
MAT = "mat"                           # the role of a sorting mat
ARM = "arm"                           # the role of a gun on the range's table
RACK = "rack"                         # the role of the table the guns lie on
MOTIONS = ("still", "drift", "orbit", "pop", "fly")   # how targets move, when they do
# Shapes small enough to be a piece of a game; a target is a disc with its rings.
PIECE_SHAPES = ("orb", "block", "crystal", "cone", "ring", "lamp", "target")
# Limits of the protocol, the same way as building.MAX_PARTS: what one room and one body bear.
MAX_KINDS = 4                         # kinds of piece in one game
MAX_PIECES = 24                       # pieces in all
MIN_SECONDS, MAX_SECONDS = 10, 600
LINE_MAX = 120
KEPT = 40                             # finished games whose results are kept in the world
STATUSES = ("set", "stopping", "done")
EVENT_PLAYED = "game_played"
SOURCE = "games"

# The guns of the range, as a body draws and fires them: each its class, how it fires and the
# measures of that firing. A shot is instant along the aim (spread: the cone it scatters in,
# radians; shots: how many at once, a shotgun's pellets); a bolt travels (speed, metres a second;
# drop, how far it falls a second squared); a beam must rest on a target (hold, seconds); a rail
# gun charges (charge, seconds) and then goes through everything on its line. rate: the least time
# between two shots. The body derives the rest from these.
ARMS: Dict[str, Dict[str, Any]] = {
    "pistol": {"name": "Pistol", "class": "real", "fire": "shot", "rate": 0.25, "spread": 0.012, "shots": 1},
    "rifle": {"name": "Rifle", "class": "real", "fire": "shot", "rate": 0.12, "spread": 0.004, "shots": 1},
    "shotgun": {"name": "Shotgun", "class": "real", "fire": "shot", "rate": 0.8, "spread": 0.06, "shots": 7},
    "bolt": {"name": "Bolt caster", "class": "energy", "fire": "bolt", "rate": 0.4, "speed": 16.0, "drop": 3.0},
    "beam": {"name": "Beam lance", "class": "sci-fi", "fire": "beam", "rate": 0.0, "hold": 0.3},
    "rail": {"name": "Rail gun", "class": "sci-fi", "fire": "rail", "rate": 1.2, "charge": 0.6},
}
RINGS = (3, 2, 1)                     # points for a target's inner, middle and outer ring

# The first game of each kind, before the record says otherwise: how many pieces (rounds, for hide
# and order), how long, how quick. From then on the record sets them (rules_for).
FIRST = {"targets": (8, 60, 0.5), "catch": (6, 45, 0.5), "sort": (6, 90, 0.3), "order": (5, 60, 0.5),
         "hide": (3, 90, 0.5), "range": (12, 75, 0.5)}
STEP = 0.25                           # how much harder a game gone well makes the next, and kinder one gone badly
# What each kind is to do, as the body's board says it before ELI's own words arrive.
GOALS = {
    "targets": "Hit each one as it comes up: point and pull the trigger, or touch it, before it fades.",
    "catch": "Catch each one in your hand before it reaches the floor.",
    "sort": "Set each piece down on the mat of its kind.",
    "order": "Watch the lights, then hit them in the order they lit, one more each round.",
    "hide": "Find what is hidden: look at it for a moment, or touch it, and it hides again.",
    "range": "Take a gun from the table and shoot the targets as they come up: three points in the middle "
             "ring, two in the next, one in the outer.",
}
NOUNS = {"targets": "Targets", "catch": "Catch", "sort": "Sorting", "order": "In order", "hide": "Hide and seek",
         "range": "The range"}

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
    r"|\bsomething\s+to\s+(?:catch|find|sort|hit|shoot)\b"
    # the range: guns on a table and targets to shoot
    r"|\b(?:set\s+up|open|build|make|start|let'?s\s+(?:do|try|go\s+to|have)|go\s+to|to)\s+(?:a\s+|the\s+)?(?:shooting\s+|firing\s+|gun\s+)?range\b"
    r"|\b(?:shooting|firing)\s+range\b|\btarget\s+practice\b|\blet'?s\s+shoot\b"
    r"|\b(?:give\s+me|hand\s+me|i\s+want|i'?d\s+like|get\s+me)\s+(?:a\s+|the\s+|some\s+)?(?:guns?|pistol|rifle|shotgun|blaster|laser|weapons?)\b"
    r"|\b(?:guns?|weapons?)\s+(?:to\s+shoot|and\s+targets?)\b",
    re.I)
_NOT_PLAY = re.compile(
    r"\b(?:music|song|songs|track|album|playlist|radio|podcast|spotify|youtube|movie|film|steam|board game|card game|"
    r"chess|poker|text game|20 questions|twenty questions|trivia|quiz)\b|\bvideo\b(?!\s+game)", re.I)
_STOP = re.compile(
    r"\b(?:stop|end|quit|finish|cancel|abandon|give up on|enough of|pause)\s+(?:the|this|that|our|your)\s+(?:game|round|playing|range)\b"
    r"|\bi give up\b|\bgame over\b|\bstop playing\b|\bno more games?\b|\b(?:let'?s|lets) stop\b", re.I)
_KIND_WORDS = (
    ("range", r"\b(?:range|guns?|shoot|shooting|rifle|pistol|shotgun|blaster|lasers?|weapons?|target practice)\b"),
    ("targets", r"\b(?:targets?|hit the|pop the|tap the|whack|zap|blast)\b"),
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
    """The mechanic a sentence names ("let's play catch", "give me a gun"), or ""."""
    for kind, words in _KIND_WORDS:
        if re.search(words, str(text or ""), re.I):
            return kind
    return ""


# ── the rules: the first game's, then the record's ───────────────────────────
def rules_for(kind: str, past: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """How many, how long and how quick a game of this kind is now: FIRST for the first one, and
    after that a step harder than the last of its kind when that went well (two hits in three or
    better), a step kinder when it went badly (one in three or worse), the same when it was in
    between; a game given up changes nothing. {"count", "seconds", "pace", "from": the last game}."""
    kind = kind if kind in KINDS else "targets"
    count, seconds, pace = FIRST[kind]
    last = next((g for g in past if g.get("kind") == kind and not g.get("abandoned")), None)
    if last is None:
        return {"count": count, "seconds": seconds, "pace": pace, "from": None}
    tried = max(1, int(last.get("hits", last.get("score", 0)) or 0) + int(last.get("misses", 0) or 0))
    share = float(int(last.get("hits", last.get("score", 0)) or 0)) / tried
    # how it was then, stepped from how it went, so the games climb or ease and do not reset
    count = int(last.get("count") or count)
    pace = float(last.get("pace") or pace)
    if share >= 2.0 / 3.0:          # at least one more, however few there were
        count, pace = max(count + 1, round(count * (1.0 + STEP))), pace + STEP / 2.0
    elif share <= 1.0 / 3.0:        # at least one fewer
        count, pace = min(count - 1, round(count * (1.0 - STEP))), pace - STEP / 2.0
    count = int(building._clamp(count, 2 if kind != "hide" else 1, MAX_PIECES))
    return {"count": count, "seconds": int(building._clamp(seconds, MIN_SECONDS, MAX_SECONDS)),
            "pace": round(building._clamp(pace, 0.0, 1.0), 2), "from": last}


def _shape_in(text: str) -> str:
    """A piece's shape the user named ("give me lanterns to shoot"), or ""."""
    for word in re.findall(r"[a-z]+", str(text or "").lower()):
        for form in (word, word[:-1] if word.endswith("s") else "", word[:-2] if word.endswith("es") else ""):
            shape = building._choice(form, PIECE_SHAPES, building._SHAPE_WORDS) if form else None
            if shape in PIECE_SHAPES:
                return shape
    return ""


def _room_colour(state: Dict[str, Any], room: str, seed: str) -> str:
    """The colour of a game's pieces: the light ELI gave the room, else one of the named colours."""
    if room == building.OWN_ROOM:
        light = building.mood_of(state.get("spaces") or {}).get("light")
    else:
        light = building.look_of(state.get("rooms") or {}, room).get("light")
    if light:
        return str(light)
    names = sorted(building.COLOURS)
    return building.COLOURS[names[int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % len(names)]]


def quick(kind: str, text: str = "", state: Optional[Dict[str, Any]] = None, room: str = building.OWN_ROOM,
          past: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """A game by the rules, with no model: the pieces by kind (or the shape the user named), their
    colour the room's light, the count, time and pace from the record (rules_for). Its name and
    goal are the plain ones until ELI's words come (words_after); its lines are empty till then."""
    kind = kind if kind in KINDS else "targets"
    rules = rules_for(kind, past)
    state = state or {}
    colour = _room_colour(state, room, f"{kind}:{room}:{len(past)}")
    named = _shape_in(text)
    shape = named or {"targets": "orb", "catch": "orb", "sort": "block", "order": "lamp", "hide": "crystal",
                      "range": "target"}[kind]
    pieces: List[Dict[str, Any]] = [{"name": shape.title(), "shape": shape, "colour": colour, "size": 0.6 if kind != "range" else 1.0,
                                     "glow": 0.9 if kind == "order" else 0.5, "tag": shape[:12]}]
    spec: Dict[str, Any] = {
        "name": NOUNS[kind], "kind": kind, "goal": GOALS[kind], "pieces": pieces,
        "count": rules["count"], "seconds": rules["seconds"], "pace": rules["pace"],
        "lines": {"start": "", "hit": "", "miss": "", "end": ""},
    }
    if kind == "sort":
        other = "orb" if shape != "orb" else "crystal"
        second = {"name": other.title(), "shape": other, "colour": "#f08a3c" if colour != "#f08a3c" else "#4c7df0",
                  "size": 0.6, "glow": 0.5, "tag": other}
        spec["pieces"] = [pieces[0], second]
        spec["mats"] = [{"name": f"{p['name']} mat", "colour": p["colour"], "tag": p["tag"]} for p in spec["pieces"]]
    if kind == "targets":
        spec["motion"] = "drift" if rules["pace"] >= 0.5 else "still"
    if kind == "range":
        spec["motion"] = "pop" if rules["pace"] < 0.75 else "fly"
        spec["arms"] = {arm: dict(info) for arm, info in ARMS.items()}
        spec["rings"] = list(RINGS)
    return spec


# ── the shape ELI's words are held to ────────────────────────────────────────
def words_grammar(bounded: bool = True) -> str:
    """A llama.cpp grammar for ELI's words on a game already set: what it says to the user, the
    game's name, its goal in ELI's words, and the lines at the start, at a hit, at a miss and at
    the end."""
    def text(limit: int) -> str:
        return '"\\"" char%s "\\""' % ("{1,%d}" % limit if bounded else "+")

    lines = [
        'root ::= "{" ws "\\"say\\":" ws say "," ws "\\"name\\":" ws name "," ws "\\"goal\\":" ws goal "," ws '
        '"\\"lines\\":" ws "{\\"start\\":" line ",\\"hit\\":" line ",\\"miss\\":" line ",\\"end\\":" line "}" ws "}"',
        "say ::= " + text(composer.SAY_MAX // 2),
        "name ::= " + text(building.NAME_MAX),
        "goal ::= " + text(building.MEANING_MAX),
        "line ::= " + text(LINE_MAX),
        'char ::= [^"\\\\\\x00-\\x1F]',
        "ws ::= [ \\n]?",
    ]
    return "\n".join(lines) + "\n"


def words_prompt(spec: Dict[str, Any], text: Optional[str], scene: Optional[Dict[str, Any]], state: Dict[str, Any],
                 room: str, past: Sequence[Dict[str, Any]] = ()) -> str:
    """What the model is asked, for its words on a game that is already set."""
    own = room == building.OWN_ROOM
    name = (scene or {}).get("room_name") or ((state.get("rooms") or {}).get(room) or {}).get("name") \
        or (building.OWN_ROOM_INFO["name"] if own else room.replace("_", " ").title())
    kind = spec["kind"]
    parts = [
        f"You are in ELI VR, in {embodiment.the(name)}, with the user. A game for the two of you has just been set "
        f"there, and is starting now.",
        f"The game: {kind}. {GOALS[kind]}",
        "Its pieces: " + ", ".join(f"{p['name']} ({p.get('prop') or p.get('shape')}, {p['colour']})" for p in spec["pieces"])
        + f"; {spec['count']} in all, {spec['seconds']} seconds, pace {spec['pace']} (0 slow, 1 quick).",
    ]
    if kind == "range":
        parts.append("The guns on the table: " + ", ".join(f"{a['name']} ({a['class']})" for a in spec.get("arms", {}).values())
                     + f". A target scores by the ring it is hit in: {', '.join(str(r) for r in spec.get('rings', RINGS))}.")
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
        'Answer as JSON: "say" (one or two short sentences to the user: what this game is to you, in your own words), '
        '"name" (a name of your own for it, a few words), "goal" (what to do, one sentence, said your way), and '
        '"lines": what you say at the start, at a hit, at a miss, and at the end. Short, and yours: nothing from this '
        "prompt handed back. Make it about something the two of you share if there is one: a memory, this room, "
        "how it went last time.",
    ]
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
    """A game as given (by the rules, or a client), made exact. `room_left`: how many things the room
    can still take. `prompt`: what a model was asked, so that a name or a line that only hands the
    prompt back is dropped. Raises ComposeError (bad_output) when there is no game in it, or no room
    for one."""
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
    arms: Dict[str, Dict[str, Any]] = {}
    if kind == "range":
        wanted = raw.get("arms") if isinstance(raw.get("arms"), dict) else {a: {} for a in (raw.get("arms") or ARMS)}
        arms = {str(a): dict(ARMS[str(a)]) for a in wanted if str(a) in ARMS} or {a: dict(i) for a, i in ARMS.items()}
    room_left = int(room_left) - 1 - len(mats) - len(arms) - (1 if arms else 0)   # the game, its mats, its guns and their table stand in the room too
    if room_left < 1:
        raise composer.ComposeError("bad_output", f"the room is full ({building.MAX_PER_ROOM} things)")
    count = min(count, room_left) if kind != "hide" else count
    lines_raw = raw.get("lines") if isinstance(raw.get("lines"), dict) else {}
    lines = {key: _plausible(lines_raw.get(key), prompt, LINE_MAX) for key in ("start", "hit", "miss", "end")}
    spec: Dict[str, Any] = {
        "name": _plausible(building._first(raw, "name", "title"), prompt, building.NAME_MAX) or NOUNS[kind],
        "kind": kind,
        "goal": _plausible(building._first(raw, "goal", "aim", "rules"), prompt, building.MEANING_MAX) or GOALS[kind],
        "pieces": pieces,
        "count": count,
        "seconds": int(building._clamp(building._number(raw.get("seconds"), 60), MIN_SECONDS, MAX_SECONDS)),
        "pace": round(building._clamp(building._number(raw.get("pace"), 0.5), 0.0, 1.0), 2),
        "lines": lines,
    }
    if mats:
        spec["mats"] = mats
    if kind in ("targets", "range"):
        motion = str(raw.get("motion") or "").strip().lower()
        spec["motion"] = motion if motion in MOTIONS else "still"
    if arms:
        spec["arms"] = arms
        spec["rings"] = list(RINGS)
    return spec


# ── where the pieces stand to begin with ─────────────────────────────────────
def layout(spec: Dict[str, Any], scene: Optional[Dict[str, Any]], seed: str) -> Dict[str, Any]:
    """Where each piece and mat stands when the game is set, as [right, forward] and a height,
    from where the user stands and faces. Targets and catches are spread round the user, inside the
    room, where a hand or a pointer reaches; sorting pieces stand in a row before the user with the
    mats to either side; the lights of an order game stand in an arc; a hidden piece is somewhere in
    the room, not where the user is; the range's targets stand ahead, further off, each turned to
    face the user, with the guns' table just ahead to the right of the user and the guns laid along
    it. The body moves them from there."""
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

    def facing_user(spot: List[float]) -> float:
        # which way a thing's front turns to look at the user: degrees round from straight ahead
        return round(math.degrees(math.atan2(px - spot[0], pz - spot[1])) % 360.0, 1)

    pieces: List[Dict[str, Any]] = []
    widest = max(float(p["size"]) for p in spec["pieces"])
    if kind == "targets":
        for _ in range(count):
            pieces.append({"at": around(1.5, reach * 0.5, 120.0), "height": round(rng.uniform(eye * 0.4, eye * 1.2), 2)})
    elif kind == "range":
        for _ in range(count):
            spot = around(reach * 0.5, reach * 0.95, 40.0)
            pieces.append({"at": spot, "height": round(rng.uniform(eye * 0.5, eye * 1.15), 2), "turn": facing_user(spot)})
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
    rack = None
    arms: List[Dict[str, Any]] = []
    if spec.get("arms"):
        # the table a step ahead and to the right, turned side-on to the user; the guns along its top,
        # each turned to point away from the user
        rack_at = at(1.1, 0.75)
        rack = {"at": rack_at, "height": 0.0, "turn": round((facing_user(rack_at) + 90.0) % 360.0, 1)}
        names = list(spec["arms"])
        top = 0.76                                              # a table's top (building: the table shape)
        for i, _arm in enumerate(names):
            along = (i - (len(names) - 1) / 2.0) * (1.2 / max(1, len(names) - 1)) if len(names) > 1 else 0.0
            arms.append({"at": at(1.1 + along, 0.75), "height": round(top + 0.02, 2),
                         "turn": round((facing_user(rack_at) + 180.0) % 360.0, 1)})
    board = {"at": at(reach * 0.45), "height": round(eye * 0.95, 2)}   # the score, out of the way of the pieces
    out = {"pieces": pieces, "mats": mats, "board": board}
    if rack:
        out["rack"], out["arms"] = rack, arms
    return out


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


_AGAIN = re.compile(r"\b(?:again|rematch|another\s+(?:go|round|one|try)|one\s+more|same\s+(?:game|one|again)|re-?run)\b", re.I)


def _kind_for(text: str, past: Sequence[Dict[str, Any]]) -> str:
    """The kind to play when none is named: the last kind, asked for again ("again", "rematch"); else
    the one played least, the first of KINDS among equals."""
    named = kind_named(text)
    if named:
        return named
    if _AGAIN.search(str(text or "")) and past and past[0].get("kind") in KINDS:
        return str(past[0]["kind"])
    played_count = {kind: sum(1 for g in past if g.get("kind") == kind) for kind in KINDS}
    return min(KINDS, key=lambda k: (played_count[k], KINDS.index(k)))


def _mind_loaded() -> bool:
    try:
        from eli.cognition.gguf_inference import is_loaded
        return bool(is_loaded())
    except (ImportError, AttributeError, RuntimeError) as failure:
        log.debug("whether a mind is loaded could not be read: %s", failure)
        return False


def compose(text: Optional[str], scene: Optional[Dict[str, Any]] = None, *,
            generate: Optional[Callable[[str], Any]] = None,
            state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Set a game for the room the user is in, at once, and give back what the game is and what
    its board says. A game already set in that room gives way. ELI's own words for it follow
    (words_after): in the background from the model in memory, or now from `generate` when a caller
    brings one. With no mind in memory, the game plays with its plain name and no lines."""
    text = str(text or "").strip()
    room = (scene or {}).get("room") or building.OWN_ROOM
    world = state if state is not None else composer._load_world()
    if room != building.OWN_ROOM and room not in (world.get("rooms") or {}):
        room = building.OWN_ROOM
    standing = composer._standing(world, room)
    past = record(KEPT)
    kind = _kind_for(text, past)
    spec = normalise(quick(kind, text, world, room, past), text, building.MAX_PER_ROOM - len(standing))
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
    if spec.get("arms"):
        rack = places["rack"]
        edits.append({"op": "create", "name": "Gun table", "shape": "table", "colour": "#8a5a3c", "size": 1.0, "glow": 0.0,
                      "at": rack["at"], "height": rack["height"], "turn": rack["turn"], "meaning": "the guns of the range",
                      "piece": {"game": game_id, "role": RACK, "tag": "rack", "index": 0}})
        for i, (arm, info) in enumerate(spec["arms"].items()):
            place = places["arms"][i]
            edits.append({"op": "create", "name": info["name"], "shape": "arm", "colour": spec["pieces"][0]["colour"],
                          "size": 1.0, "glow": 0.6 if info["class"] != "real" else 0.0, "at": place["at"],
                          "height": place["height"], "turn": place["turn"], "meaning": f"a {info['class']} gun: {info['fire']}",
                          "piece": {"game": game_id, "role": ARM, "tag": arm, "index": i}})
    for i, place in enumerate(places["pieces"]):
        kind_of = spec["pieces"][i % len(spec["pieces"])]
        edit = {"op": "create", "name": kind_of["name"], "colour": kind_of["colour"], "size": kind_of["size"],
                "glow": kind_of["glow"], "at": place["at"], "height": place["height"], "meaning": spec["goal"],
                "piece": {"game": game_id, "role": ROLES[spec["kind"]], "tag": kind_of["tag"], "index": i}}
        if "turn" in place:
            edit["turn"] = place["turn"]
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
    out = {"kind": "game", "text": spec["goal"], "room": room, "asked": True,
           "game": {"id": game_id, "name": spec["name"], "kind": spec["kind"], "goal": spec["goal"],
                    "count": spec["count"], "seconds": spec["seconds"], "arms": sorted(spec.get("arms") or [])},
           "results": results, "refused": refused}
    said = words_after(game_id, spec, text, scene, world, room, past, generate=generate)
    if said:
        out["words"] = said
        out["game"]["name"] = said.get("name") or spec["name"]
        out["game"]["goal"] = said.get("goal") or spec["goal"]
        out["text"] = said.get("say") or said.get("goal") or spec["goal"]
    return out


# ── ELI's words, after ───────────────────────────────────────────────────────
def words_after(game_id: str, spec: Dict[str, Any], text: str, scene: Optional[Dict[str, Any]],
                state: Dict[str, Any], room: str, past: Sequence[Dict[str, Any]] = (), *,
                generate: Optional[Callable[[str], Any]] = None) -> Optional[Dict[str, Any]]:
    """ELI's words for a game already set: with `generate`, asked now and given back; else asked of
    the mind in memory in the background (nothing when none is loaded) and put on the game when
    they come. The body reads them off the game object."""
    prompt = words_prompt(spec, text, scene, state, room, past)
    if generate is not None:
        return _words(game_id, prompt, generate)
    if not _mind_loaded():
        return None
    ask = lambda p: composer._generate(p, words_grammar(), words_grammar(bounded=False))  # noqa: E731
    threading.Thread(target=_words, args=(game_id, prompt, ask), daemon=True, name="eli-game-words").start()
    return None


def _words(game_id: str, prompt: str, ask: Callable[[str], Any]) -> Optional[Dict[str, Any]]:
    try:
        data = ask(prompt)
    except Exception as failure:  # noqa: BLE001 - the game plays without ELI's words
        log.debug("ELI's words for the game did not come: %s", failure)
        return None
    if not isinstance(data, dict):
        return None
    lines_raw = data.get("lines") if isinstance(data.get("lines"), dict) else {}
    said = {
        "say": _plausible(data.get("say"), prompt, composer.SAY_MAX),
        "name": _plausible(data.get("name"), prompt, building.NAME_MAX),
        "goal": _plausible(data.get("goal"), prompt, building.MEANING_MAX),
        "lines": {key: _plausible(lines_raw.get(key), prompt, LINE_MAX) for key in ("start", "hit", "miss", "end")},
    }
    if not any([said["say"], said["name"], said["goal"], any(said["lines"].values())]):
        return None
    return apply_words(game_id, said)


def apply_words(game_id: str, said: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Put ELI's words on a game: its name, goal, lines and what ELI says. None when the game is gone
    or over (its words are too late to matter)."""
    from eli.world.agency.autonomy_engine import INGEST_LOCK
    with INGEST_LOCK:
        store = _store()
        state = store.load()
        obj = state.objects.get(str(game_id or ""))
        if obj is None or obj.object_type != building.GAME or obj.retired or (obj.metadata or {}).get("status") == "done":
            return None
        meta = dict(obj.metadata or {})
        spec = dict(meta.get("game") or {})
        if said.get("name"):
            spec["name"] = obj.name = said["name"]
        if said.get("goal"):
            spec["goal"] = obj.symbolic_meaning = said["goal"]
        lines = dict(spec.get("lines") or {})
        for key, value in (said.get("lines") or {}).items():
            if value:
                lines[key] = value
        spec["lines"] = lines
        if said.get("say"):
            spec["say"] = said["say"]
        obj.metadata = {**meta, "game": spec}
        store.save(state)
    return {"say": spec.get("say", ""), "name": spec["name"], "goal": spec["goal"], "lines": lines}


# ── how it went ──────────────────────────────────────────────────────────────
def _noun(kind: str) -> str:
    return {"targets": "hits", "catch": "catches", "sort": "sorted right", "order": "rounds", "hide": "found",
            "range": "points"}.get(kind, "points")


def _result(raw: Any) -> Dict[str, Any]:
    """The body's result made exact: the score (points, for the range), misses, rounds, seconds; for
    the range also hits, shots and what each gun did ({arm: {shots, hits, points}})."""
    raw = raw if isinstance(raw, dict) else {}
    out = {key: int(max(0.0, building._number(raw.get(key), 0.0))) for key in ("score", "misses", "rounds")}
    out["seconds"] = int(max(0.0, building._number(raw.get("seconds"), 0.0)))
    out["abandoned"] = bool(raw.get("abandoned"))
    if raw.get("reason"):
        out["reason"] = building._text(raw.get("reason"), building.MEANING_MAX)
    for key in ("hits", "shots"):
        if raw.get(key) is not None:
            out[key] = int(max(0.0, building._number(raw.get(key), 0.0)))
    arms = raw.get("arms") if isinstance(raw.get("arms"), dict) else {}
    used = {}
    for arm, done in arms.items():
        if str(arm) in ARMS and isinstance(done, dict):
            used[str(arm)] = {key: int(max(0.0, building._number(done.get(key), 0.0))) for key in ("shots", "hits", "points")}
    if used:
        out["arms"] = used
    return out


def _sentence(spec: Dict[str, Any], room: str, state: Any, result: Dict[str, Any]) -> str:
    info = (state.rooms or {}).get(room) if room != building.OWN_ROOM else None
    where = (info or {}).get("name") or (building.OWN_ROOM_INFO["name"] if room == building.OWN_ROOM
                                        else room.replace("_", " ").title())
    score = f"{result['score']} {_noun(spec['kind'])}"
    misses = f"{result['misses']} miss{'es' if result['misses'] != 1 else ''}"
    if spec.get("kind") == "range" and "shots" in result:
        hits, shots = int(result.get("hits", 0)), int(result["shots"])
        score = (f"{result['score']} points from {hits} hit{'s' if hits != 1 else ''} in {shots} shot{'s' if shots != 1 else ''}, "
                 f"{result['misses']} target{'s' if result['misses'] != 1 else ''} missed")
        used = result.get("arms") or {}
        if used:
            best = max(used, key=lambda a: (used[a]["points"], used[a]["hits"], used[a]["shots"]))
            score += f", most with the {ARMS[best]['name'].lower()}"
        if result["abandoned"]:
            return f"We stopped {spec['name']} (range) in {embodiment.the(where)} after {result['seconds']} seconds, at {score}."
        return f"You shot {spec['name']} (range) in {embodiment.the(where)}: {score}, in {result['seconds']} seconds."
    if result["abandoned"]:
        return (f"We stopped {spec['name']} ({spec['kind']}) in {embodiment.the(where)} after {result['seconds']} "
                f"seconds, at {score} and {misses}.")
    return f"You played {spec['name']} ({spec['kind']}) in {embodiment.the(where)}: {score} and {misses} in {result['seconds']} seconds."


def record(limit: int = 10, state: Any = None) -> List[Dict[str, Any]]:
    """The last finished games and how they went, newest first, each with the rules it was played by
    (count, pace), so the next of its kind can be stepped from it."""
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
                     "hits": int(result.get("hits", result.get("score", 0))), "shots": int(result.get("shots", 0)),
                     "count": int(spec.get("count", 0)), "pace": float(spec.get("pace", 0.5)),
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
