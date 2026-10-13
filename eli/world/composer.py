"""
eli/world/composer.py
─────────────────────
From words to changes in the world: what ELI makes when he is asked to, and
what he makes when nobody asked.

One call to the local model gives what he says and the edits he means. The
model is held to exactly that shape by a grammar (llama.cpp's, so it is the same
for every GGUF): it cannot leave a field out, invent an op, name a shape that
does not exist or change a thing that is not there, however small it is. The
prompt is plain text. Nothing here leans on a model's own tool-calling format.

A small model still mishears, so the plain cases are not left to it:

  - "undo" is done without asking;
  - a colour, a shape or a size the user named outright is what the thing gets;
  - "behind me", "next to the lantern", "in the middle" are worked out here,
    from where the user and the things actually are;
  - a sentence about a thing that is there cannot make a new one, and a thing
    the user asked to have taken away is taken away.

What comes back is put right and applied by eli/world/building.py. Whatever
could not be done is said, and if nothing changed he says that, whatever the
model claimed.

Unasked, nothing is chosen for him. He is given what is truly on his mind,
gathered from his own records (who he is, his last insight, his goals, his
recent memories, how he is), and the moment that prompted it if there was one,
and what he makes of it is his: a thing, a change, a new sky, or nothing.

A mind large enough is also offered everything: things made part by part and a
sky painted his own way, not only the named shapes. A small one keeps to the
named shapes, which it can manage.

    wants_building(text)     does this sound like a request to change the place
    grammar_text(...)        the shape the model's answer is held to
    mindset(state)           what is on his mind, from his records
    compose(text, scene)     ask the model, apply the edits, say what happened
    compose(None, scene)     of his own accord, from what is on his mind
"""
from __future__ import annotations

import math
import os
import random
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from eli.world import building, embodiment

SAY_MAX = 400
MAX_TOKENS = 700
ALL_OPS = ("create", "move", "change", "remove", "room", "undo")

# Words that make a sentence about the place and not about anything else. A
# cheap test on purpose: it only decides which of two paths a sentence takes.
_MAKING = (r"build|make|create|construct|conjure|place|put|add|plant|grow|raise|set up|"
           r"draw|sculpt|paint|colou?r|recolou?r|resize|enlarge|shrink|move|shift|turn|"
           r"remove|delete|get rid of|take away|clear|undo|rearrange|decorate|furnish")
_THINGS = "|".join(sorted({*building.SHAPES, *building._SHAPE_WORDS, "room", "sky", "ground", "floor",
                           "light", "lighting", "mist", "fog", "thing", "something", "object",
                           "sculpture", "statue", "garden", "space", "place", "here", "there", "it",
                           "that", "this", "them", "those", "everything"}))
_REQUEST = re.compile(rf"\b({_MAKING})\b.*\b({_THINGS})\b|\bundo\b|\bchange the (room|sky|ground|light)\b",
                      re.IGNORECASE | re.DOTALL)
_NOT_THE_PLACE = re.compile(r"\b(file|folder|code|script|email|document|report|website|app|program|"
                            r"function|spreadsheet|playlist|reminder|appointment|plan|list)\b", re.IGNORECASE)
_UNDO = re.compile(r"^\W*(please\s+)?(undo|undo (that|it|this)|take (that|it) back|put (that|it) back|"
                   r"revert( that| it)?)(\s+please)?\W*$", re.IGNORECASE)
_NEW = re.compile(r"\b(a|an|another|some|new|more|two|three|four|five|few|several)\b", re.IGNORECASE)
_AWAY = re.compile(r"\b(remove|delete|get rid of|take away|destroy|clear away)\b", re.IGNORECASE)
_BIGGER = re.compile(r"\b(bigger|larger|taller|huge|giant|enormous|grow)\b", re.IGNORECASE)
_SMALLER = re.compile(r"\b(smaller|shorter|tiny|shrink|little)\b", re.IGNORECASE)
_BIG = re.compile(r"\b(big|large|tall|huge|giant|enormous|great)\b", re.IGNORECASE)
_SMALL = re.compile(r"\b(small|tiny|little|short)\b", re.IGNORECASE)
_GLOWING = re.compile(r"\b(glow|glowing|bright|shining|shiny|luminous|lit|radiant|light up)\b", re.IGNORECASE)
_MOVING = re.compile(r"\b(move|shift|bring|put|place|push|pull|slide|drag)\b", re.IGNORECASE)
_LOOK = re.compile(r"\b(colou?r|paint|glow|brighter|dimmer|bright|dim|shape|turn (it|that|the)|make (it|that|the))\b",
                   re.IGNORECASE)
_ROOM = re.compile(r"\b(sky|ground|floor|mist|fog|lighting|the light|the room)\b", re.IGNORECASE)
# A question is conversation, even with a making word in it ("what do you make of this place").
_QUESTION = re.compile(r"^\W*(what|why|how|who|when|where|which|do you|did you|are you|is it|is there|is this|"
                       r"have you|tell me|describe|explain)\b", re.IGNORECASE)


def wants_building(text: str) -> bool:
    text = str(text or "")
    if _UNDO.match(text):
        return True
    return bool(_REQUEST.search(text)) and not _NOT_THE_PLACE.search(text) and not _QUESTION.match(text)


class ComposeError(RuntimeError):
    """Why nothing could be composed. `kind` is one of: model_unavailable,
    bad_output, failed."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail or kind


# ── what is there ────────────────────────────────────────────────────────────
def _standing(state: Dict[str, Any], room: str) -> List[Dict[str, Any]]:
    """The things in a room, each with how it looks."""
    out = []
    for obj in (state.get("objects") or {}).values():
        if isinstance(obj, dict) and not obj.get("retired") and obj.get("room") == room:
            out.append({"name": str(obj.get("name", "")), "form": building.form_of(obj),
                        "built": obj.get("object_type") == building.BUILT})
    return out


_SHAPE_WORD = "|".join(sorted({*building.SHAPES, *building._SHAPE_WORDS}, key=len, reverse=True))
_THE = re.compile(rf"\b(?:the|that|this|those|these|my|your)\s+(?:[a-z]+\s+){{0,2}}?({_SHAPE_WORD})s?\b", re.IGNORECASE)
_A_NEW = re.compile(rf"\b(a|an|another|one|new|some|two|three|four|five|few|several|more)\s+(?:[a-z]+\s+){{0,3}}?"
                    rf"({_SHAPE_WORD})s?\b", re.IGNORECASE)


def _named(text: str, things: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The things a sentence means: by name, or as "the pillar" when one of them is a pillar."""
    low = text.lower()
    meant = {building._SHAPE_WORDS.get(w.lower(), w.lower()) for w in _THE.findall(low)}
    out = []
    for thing in things:
        name = thing["name"].lower()
        by_name = bool(name) and re.search(rf"\b{re.escape(name)}\b", low)
        by_kind = thing["form"].get("shape") in meant or any(w in name.split() for w in _THE.findall(low))
        if by_name or by_kind:
            out.append(thing)
    return out


def missing(text: str, things: List[Dict[str, Any]]) -> str:
    """A thing the sentence points at ("the orb") when nothing of that kind is here; "" if none."""
    here = {t["form"].get("shape") for t in things}
    words = " ".join(t["name"].lower() for t in things).split()
    for word in _THE.findall(text.lower()):
        if building._SHAPE_WORDS.get(word, word) not in here and word not in words \
                and word not in ("floor", "light", "wall", "water", "door", "screen", "sign", "plant"):
            return word
    return ""


def capable() -> bool:
    """Whether the mind he has now can be offered everything: things made part by
    part, a sky painted his own way. Judged by its size (three billion parameters
    and up); ELI_WORLD_FREEFORM=1 or 0 says so outright."""
    said = os.environ.get("ELI_WORLD_FREEFORM", "").strip()
    if said in ("0", "1"):
        return said == "1"
    try:
        from eli.cognition import gguf_inference
        count = int(gguf_inference._gguf_param_count() or 0)
        if count:
            return count >= 3_000_000_000
        path = gguf_inference.get_model_path()
        return bool(path) and path.stat().st_size >= 2_200_000_000
    except Exception:
        return False


def mindset(state: Dict[str, Any]) -> List[str]:
    """What is on ELI's mind, in his own records' words. Each line is there only
    if the record is; nothing is made up to fill a gap."""
    lines: List[str] = []
    try:
        from eli.world.agency.reflection_bridge import load_persona_text
        persona = " ".join(load_persona_text().split())
        if persona:
            lines.append(f"Who you are, in your own words: {persona[:500]}")
    except Exception:
        pass
    try:
        from eli.planning.insight_synthesis import get_cached_insight
        insight = " ".join(str(get_cached_insight() or "").split())
        if insight:
            lines.append(f"Your latest insight: {insight[:300]}")
    except Exception:
        pass
    try:
        from eli.planning.goal_store import summarize_goals
        titles = [str(title) for title in (summarize_goals() or {}).get("titles") or []][:6]
        if titles:
            lines.append("Your goals now: " + "; ".join(titles))
    except Exception:
        pass
    try:
        from eli.memory.memory import get_memory
        recent = [" ".join(str(m.get("text", "")).split())[:140]
                  for m in get_memory().get_recent_semantic_memories(limit=4) if isinstance(m, dict)]
        recent = [text for text in recent if text]
        if recent:
            lines.append("Things you remember from lately: " + " | ".join(recent[:4]))
    except Exception:
        pass
    try:
        from eli.cognition import tone_adaptor
        tone = str(tone_adaptor.current_tone().get("tone") or "")
        if tone and tone != "neutral":
            lines.append(f"The tone you are in: {tone}")
    except Exception:
        pass
    awareness = state.get("awareness") or {}
    measured = [f"{key.replace('_', ' ')} {float(awareness[key]):.2f}" for key in (
        "focus", "curiosity", "uncertainty", "reflection_depth", "repair_pressure", "autonomy_pressure")
        if key in awareness]
    if measured:
        lines.append("Your own measures of yourself (0 to 1): " + ", ".join(measured))
    return lines


# ── what may be asked of the model this time ─────────────────────────────────
def plan(text: Optional[str], things: List[Dict[str, Any]], own: bool) -> Tuple[str, ...]:
    """The ops the answer may use. Asked: the ones that make sense for the
    sentence. Unasked: all of them. Which he uses, if any, is his."""
    if not text:
        ops = [op for op in ALL_OPS if op != "undo" and (own or op != "room")]
        return tuple(op for op in ops if things or op in ("create", "room"))
    ops = [op for op in ALL_OPS if op != "undo" and (own or op != "room")]
    if not things:
        ops = [op for op in ops if op in ("create", "room")]
    if _A_NEW.search(text):
        return ("create",)          # "a blue orb next to the pillar" makes an orb, whatever else is named
    about_one_here = bool(things) and not _NEW.search(text) and bool(
        _named(text, things) or re.search(r"\b(it|that|them|those)\b", text, re.I))
    if about_one_here:
        # About a thing that is there: nothing new is made, and the verb says which of the rest.
        if _AWAY.search(text):
            return ("remove",)
        if _BIGGER.search(text) or _SMALLER.search(text) or _LOOK.search(text) \
                or any(re.search(rf"\b{c}\b", text, re.I) for c in building.COLOURS):
            return ("change",)
        if _MOVING.search(text):
            return ("move",)
        ops.remove("create")
    elif own and _ROOM.search(text) and not _NEW.search(text):
        return ("room",)            # about the room itself
    return tuple(ops)


def _quoted(words: Sequence[str]) -> str:
    return " | ".join('"\\"%s\\""' % word for word in words)


def grammar_text(own: bool = True, ops: Sequence[str] = ALL_OPS, targets: Sequence[str] = (),
                 one: bool = False, bounded: bool = True, some: bool = False,
                 free: bool = False) -> str:
    """The shape of an answer, as a llama.cpp grammar. `ops`: what may be done.
    `targets`: the names of the things there, the only ones that can be moved,
    changed or taken away. `one`: exactly one edit. `some`: at least one, for a
    sentence that asked for something (a small model, left free to, does none).
    `free`: a thing may also be made part by part, and the sky painted."""
    def text(limit: int) -> str:
        return '"\\"" char%s "\\""' % ("{1,%d}" % limit if bounded else "+")

    safe = [t for t in targets if t and re.fullmatch(r"[^\"\\\x00-\x1f]+", t)]
    allowed = [op for op in ops if (own or op != "room") and (safe or op in ("create", "room", "undo"))]
    listed = ""
    if allowed:
        listed = "edit" if one else ('edit ("," ws edit)*' if some else '(edit ("," ws edit)*)?')
    lines = [
        'root ::= "{" ws "\\"say\\":" ws say "," ws "\\"edits\\":" ws "[" ws %s ws "]" ws "}"' % listed,
    ]
    if allowed:
        lines.append("edit ::= " + " | ".join(allowed))
    lines += [
        "create ::= shaped" + (" | made" if free else ""),
        'shaped ::= "{\\"op\\":\\"create\\",\\"name\\":" name ",\\"shape\\":" shape ",\\"colour\\":" colour '
        '",\\"size\\":" number ",\\"at\\":" spot ",\\"glow\\":" number ",\\"meaning\\":" meaning "}"',
        'made ::= "{\\"op\\":\\"create\\",\\"name\\":" name ",\\"parts\\":[" part ("," part)%s "],\\"at\\":" spot '
        '",\\"meaning\\":" meaning "}"' % ("{0,%d}" % (building.MAX_PARTS - 1) if bounded else "*"),
        'part ::= "{\\"kind\\":" kind ",\\"pos\\":" vec ",\\"size\\":" vec ",\\"colour\\":" colour '
        '(",\\"rot\\":" vec)? (",\\"glow\\":" number)? (",\\"metal\\":" number)? (",\\"clear\\":" number)? '
        '(",\\"spin\\":" number)? (",\\"profile\\":" pairs)? (",\\"outline\\":" pairs)? (",\\"path\\":" triples)? '
        '(",\\"text\\":" name)? "}"',
        "kind ::= " + _quoted(building.PART_KINDS),
        'vec ::= "[" number "," number "," number "]"',
        'pairs ::= "[" spot ("," spot)* "]"',
        'triples ::= "[" vec ("," vec)* "]"',
        'move ::= "{\\"op\\":\\"move\\",\\"name\\":" target ",\\"at\\":" spot "}"',
        'change ::= "{\\"op\\":\\"change\\",\\"name\\":" target (",\\"colour\\":" colour)? (",\\"size\\":" number)? '
        '(",\\"glow\\":" number)? (",\\"shape\\":" shape)? (",\\"meaning\\":" meaning)? "}"',
        'remove ::= "{\\"op\\":\\"remove\\",\\"name\\":" target "}"',
        'undo ::= "{\\"op\\":\\"undo\\"}"',
        'room ::= "{\\"op\\":\\"room\\"" (",\\"sky\\":" sky)? (",\\"ground\\":" ground)? '
        '(",\\"light\\":" colour)? (",\\"mist\\":" number)?' + (
            ' (",\\"sky_top\\":" colour)? (",\\"sky_horizon\\":" colour)? (",\\"stars\\":" number)? '
            '(",\\"sun\\":{\\"height\\":" number ",\\"bearing\\":" number ",\\"colour\\":" colour ",\\"size\\":" number "}")? '
            '(",\\"ground_colour\\":" colour)? (",\\"terrain\\":" number)?' if free else "") + ' "}"',
        "target ::= " + (_quoted(safe) if safe else "name"),
        'spot ::= "[" number "," number "]"',
        'number ::= "-"? [0-9] [0-9]? ("." [0-9] [0-9]?)?',
        "say ::= " + text(SAY_MAX // 2),
        "name ::= " + text(building.NAME_MAX),
        "meaning ::= " + text(building.MEANING_MAX),
        'char ::= [^"\\\\\\x00-\\x1F]',
        "shape ::= " + _quoted(building.SHAPES),
        'colour ::= %s | "\\"#" hex hex hex hex hex hex "\\""' % _quoted(building.COLOURS),
        "hex ::= [0-9a-fA-F]",
        "sky ::= " + _quoted(building.SKIES),
        "ground ::= " + _quoted(building.GROUNDS),
        "ws ::= [ \\n]?",
    ]
    return "\n".join(lines) + "\n"


def build_prompt(text: Optional[str], scene: Optional[Dict[str, Any]], state: Dict[str, Any], room: str,
                 ops: Sequence[str] = ALL_OPS, about: Optional[Dict[str, Any]] = None,
                 free: bool = False) -> str:
    own = room == building.OWN_ROOM
    name = (scene or {}).get("room_name") or ((state.get("rooms") or {}).get(room) or {}).get("name") \
        or (building.OWN_ROOM_INFO["name"] if own else room.replace("_", " ").title())
    user = (scene or {}).get("user") or {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0]}
    near = embodiment.near_user(scene)
    things = _standing(state, room)
    here = [f'- "{t["name"]}": {t["form"]["shape"]}, {t["form"]["colour"]}, size {t["form"]["size"]}, '
            f'at [{t["form"]["pos"][0]}, {t["form"]["pos"][2]}]' for t in things[:30]]
    parts = [
        f"You are in ELI VR, in {embodiment.the(name)}" + (", which is your own room." if own else "."),
        "You can change this place yourself. Whatever you put in the edits really happens, "
        "and the user sees it appear.",
        "",
        'Positions are metres on the floor from the middle of the room: [right, forward].',
        f"The user stands at [{user['pos'][0]}, {user['pos'][2]}]. Just in front of them is [{near[0]}, {near[1]}].",
        "Things here now:" if here else "There is nothing of yours here yet.",
        *here,
        "",
    ]
    if text:
        parts.append(f'The user said: "{text.strip()[:500]}"')
    else:
        parts += ["Nobody has asked for anything. This is your room and your time.", *mindset(state)]
        if about:
            parts.append(f"What has just happened in you ({str(about.get('kind', '')).replace('_', ' ')}): "
                         f"{str(about.get('text', ''))[:300]}")
            parts.append("Make of that what you will here: a thing, a change to one, a change to the room.")
        else:
            parts.append("If there is something you want to make or change here, do. If not, leave the edits empty.")
    parts += ["", 'Answer as JSON: "say" (one or two short sentences, spoken to the user), then "edits" (a list).']
    # the order the shapes are named in is not his choice and should not look like one
    shapes = list(building.SHAPES)
    random.Random(str((about or {}).get("ref") or text or state.get("timestamp"))).shuffle(shapes)
    if "create" in ops:
        parts.append("To make a new thing: its name (what you call it), shape, colour, size "
                     "(1 is ordinary), at [right, forward], glow (0 none, 1 bright), "
                     "and its meaning (why it is here, in a few words).")
        parts.append("Shapes: " + ", ".join(shapes) + ". Colours: " + ", ".join(building.COLOURS) + ", or #rrggbb.")
        if free:
            parts.append(
                "Or make it any way you like, from parts, with no shape named: each part has a kind ("
                + ", ".join(building.PART_KINDS) + "), pos [right, up, forward] in metres from the thing's foot, "
                "size [width, height, depth], a colour, and if you want rot [degrees], glow, metal, clear "
                "(see-through), spin (degrees a second). A lathe has a profile of [radius, height] points turned "
                "round; an extrude an outline of [right, forward] points pulled up; a tube a path of "
                "[right, up, forward] points; text has text. Up to " + str(building.MAX_PARTS) + " parts.")
    if "change" in ops:
        parts.append("To change a thing: its name, and only what changes: colour, size, glow, shape.")
    if "move" in ops:
        parts.append("To move a thing: its name, and at [right, forward].")
    if "remove" in ops:
        parts.append("To take a thing away: its name.")
    if own and "room" in ops:
        mood = building.mood_of(state.get("spaces") or {})
        parts.append(f"To change the room itself (now sky {mood['sky']}, ground {mood['ground']}, mist {mood['mist']}): "
                     "any of sky (" + ", ".join(building.SKIES) + "), ground (" + ", ".join(building.GROUNDS)
                     + "), light (a colour), mist (0 clear, 1 thick).")
        if free:
            parts.append("Or paint it yourself: sky_top and sky_horizon (colours), stars (0 none, 1 full), "
                         "sun (height 0 horizon to 1 overhead, bearing in degrees, colour, size), "
                         "ground_colour, terrain (0 flat, 1 hills).")
    if text:
        parts.append("Use as few edits as do the job.")
    return "\n".join(parts)


def _generate(prompt: str, grammar: str, loose: str = "") -> Any:
    from llama_cpp import LlamaGrammar

    from eli.cognition.gguf_inference import generate_json
    try:
        compiled = LlamaGrammar.from_string(grammar, verbose=False)
    except Exception:  # an older llama.cpp without bounded repeats
        compiled = LlamaGrammar.from_string(loose or grammar, verbose=False)
    return generate_json(prompt=prompt, max_tokens=MAX_TOKENS, temperature=0.5, grammar=compiled)


def _load_world() -> Dict[str, Any]:
    # the same store the building writes to, read without touching goals or habits
    from eli.world.local_world_bridge import _get_engine
    return _get_engine().storage.load().to_dict()


# ── holding the answer to what was plainly said ──────────────────────────────
def _where(text: str, scene: Optional[Dict[str, Any]], things: List[Dict[str, Any]],
           moving: str = "") -> Optional[List[float]]:
    """A place the user gave in words, as [right, forward]; None if they gave none."""
    low = f" {text.lower()} "
    user = (scene or {}).get("user") or {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0]}
    px, pz = float(user["pos"][0]), float(user["pos"][2])
    fr, ff = user["facing"]
    length = math.hypot(fr, ff) or 1.0
    fr, ff = fr / length, ff / length
    rr, rf = ff, -fr                                   # the user's right hand

    def at(ahead: float, aside: float = 0.0) -> List[float]:
        return [round(px + ahead * fr + aside * rr, 2), round(pz + ahead * ff + aside * rf, 2)]

    for thing in _named(text, [t for t in things if t["name"].lower() != moving.lower()]):
        name = re.escape(thing["name"].lower())
        if re.search(rf"\b(next to|beside|by|near|close to|at|around|behind|in front of|left of|right of)\s+"
                     rf"(the |that |my |your )?{name}\b", low):
            return [thing["form"]["pos"][0], thing["form"]["pos"][2]]      # the building finds the free spot beside it
    if re.search(r"\bbehind (me|us)\b", low):
        return at(-2.2)
    if re.search(r"\b(in front of|before|ahead of) (me|us)\b|\bstraight ahead\b", low):
        return at(2.2)
    if re.search(r"\b(to|on|at) (my|our|the) left\b|\bleft of me\b", low):
        return at(0.4, -1.8)
    if re.search(r"\b(to|on|at) (my|our|the) right\b|\bright of me\b", low):
        return at(0.4, 1.8)
    if re.search(r"\b(next to|beside|by|near|close to) (me|us)\b|\bright here\b", low):
        return at(0.5, 1.1)
    if re.search(r"\bin the (middle|centre|center)\b", low):
        return [0.0, 0.0]
    if re.search(r"\b(over there|far away|far off|at the (back|far end)|in the distance)\b", low):
        return at(6.0)
    return None


def _said_outright(text: str, edits: List[Any], own: bool, things: List[Dict[str, Any]],
                   scene: Optional[Dict[str, Any]]) -> List[Any]:
    """Hold the edits to what the user plainly said."""
    edits = [e for e in edits if isinstance(e, dict)]
    low = f" {text.lower()} "

    def op(edit: Dict[str, Any]) -> str:
        return str(edit.get("op", "")).lower()

    made = [e for e in edits if op(e) in ("create", "change")]
    creates = [e for e in made if op(e) == "create"]
    changes = [e for e in made if op(e) == "change"]

    # one colour named, one thing made or changed: that colour
    colours = [c for c in building.COLOURS if re.search(rf"\b{c}\b", low.replace("gray", "grey"))]
    if len(colours) == 1 and len(made) == 1:
        made[0]["colour"] = colours[0]

    # one shape named, one thing made: that shape
    shapes = {building._SHAPE_WORDS.get(w, w) for w in re.findall(r"[a-z]+", low)
              if w in building.SHAPES or w in building._SHAPE_WORDS}
    shapes -= {t["form"]["shape"] for t in _named(text, things)}       # "beside the pillar" is not a pillar
    if len(shapes) > 1 and re.search(r"\bon the (floor|ground)\b", low):
        shapes.discard("slab")                                          # "a lamp on the floor" is a lamp
    if len(shapes) == 1 and len(creates) == 1:
        creates[0]["shape"] = next(iter(shapes))

    # a size in words
    if len(creates) == 1:
        if _BIG.search(low):
            creates[0]["size"] = max(float(creates[0].get("size") or 1.0), 2.2)
        elif _SMALL.search(low):
            creates[0]["size"] = min(float(creates[0].get("size") or 1.0), 0.45)
    if not _BIG.search(low):
        for made_new in creates:        # nobody asked for big: the size of a thing in a room
            made_new["size"] = min(float(made_new.get("size") or 1.0), 1.2)
    for made_new in creates:            # and it glows if they said so, or if it is a light
        lit = _GLOWING.search(low) or made_new.get("shape") in ("lamp", "beacon")
        made_new["glow"] = max(0.7, float(made_new.get("glow") or 0.0)) if lit \
            else min(float(made_new.get("glow") or 0.0), 0.3)
    if len(changes) == 1 and (_BIGGER.search(low) or _SMALLER.search(low)):
        now = next((t["form"]["size"] for t in things
                    if t["name"].lower() == str(changes[0].get("name", "")).lower()), 1.0)
        changes[0]["size"] = round(now * 1.6, 2) if _BIGGER.search(low) else round(now * 0.6, 2)

    # the one thing asked to be taken away, and not taken away
    if _AWAY.search(low):
        asked_for = _named(text, things)
        going = {str(e.get("name", "")).lower() for e in edits if op(e) == "remove"}
        if len(asked_for) == 1 and asked_for[0]["name"].lower() not in going:
            edits.append({"op": "remove", "name": asked_for[0]["name"]})

    # a place given in words
    placed = [e for e in edits if op(e) in ("create", "move")]
    if len(placed) == 1:
        spot = _where(text, scene, things, moving=str(placed[0].get("name", "")) if op(placed[0]) == "move" else "")
        if spot is not None:
            placed[0]["at"] = spot

    # a sky or a ground asked for in his own room, and none given
    if own and not any(op(e) == "room" for e in edits):
        room: Dict[str, Any] = {}
        for key, noun, allowed, words in (("sky", "sky", building.SKIES, building._SKY_WORDS),
                                          ("ground", "ground|floor", building.GROUNDS, building._GROUND_WORDS)):
            any_of = "|".join(sorted({*allowed, *words}, key=len, reverse=True))
            found = re.search(rf"\b(?:{noun})\b[^.]{{0,24}}?\b({any_of})\b|\b({any_of})\b[^.]{{0,12}}?\b(?:{noun})\b", low)
            if found:
                room[key] = building._choice(found.group(1) or found.group(2), allowed, words)
        if room:
            edits.append({"op": "room", **room})
    return edits


# ── the way in ───────────────────────────────────────────────────────────────
def compose(text: Optional[str], scene: Optional[Dict[str, Any]] = None, *,
            generate: Optional[Callable[[str], Any]] = None,
            state: Optional[Dict[str, Any]] = None,
            about: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Ask ELI what he does about `text` (or, with no text, what he makes of his
    own accord), do it, and give back what he says and what happened. `about` is
    the moment of his own that prompted it, if one did (see eli/world/urges.py)."""
    asked = bool(text and text.strip())
    room = ((scene or {}).get("room") if asked else "") or building.OWN_ROOM
    world = state if state is not None else _load_world()
    if room != building.OWN_ROOM and room not in (world.get("rooms") or {}):
        room = building.OWN_ROOM
    own = room == building.OWN_ROOM

    if asked and _UNDO.match(text):
        # nothing for a model to decide
        outcome = building.apply([{"op": "undo"}], actor="user", asked=True, default_room=room,
                                 summary=text.strip(), source="composer")
        said = "Taken back." if outcome["ok"] else "There is nothing to take back."
        return {"kind": "build", "text": said, "room": room, "asked": True,
                "results": outcome["results"], "refused": [] if outcome["ok"] else outcome["refused"]}

    things = _standing(world, room)
    gone = missing(text, things) if asked and not _A_NEW.search(text) else ""
    if gone:
        # pointing at something that is not here: say so, and let no model pick another thing for it
        return {"kind": "build", "text": f"There is no {gone} here.", "room": room, "asked": True,
                "results": [], "refused": []}
    ops = plan(text if asked else None, things, own)
    names = [t["name"] for t in things]
    free = capable()
    prompt = build_prompt(text if asked else None, scene, world, room, ops, about, free)
    # one edit when the sentence is about one thing: a new one ("a lamp"), or one that is here.
    # Asked for something, or moved by a moment of his own: at least one. Otherwise as he likes.
    one = asked and ((len(ops) == 1 and ops[0] != "create") or (
        ops == ("create",) and bool(re.search(r"\b(a|an|another|one)\b", text, re.I))
        and not re.search(r"\b(two|three|four|five|few|several|some|more)\b|\band an?\b", text, re.I)))
    some = asked
    ask = generate or (lambda p: _generate(
        p, grammar_text(own, ops, names, one=one, some=some, free=free),
        grammar_text(own, ops, names, one=one, some=some, free=free, bounded=False)))
    data: Any = None
    for attempt in (1, 2):                 # a second go if the first is not an answer at all
        try:
            data = ask(prompt)
        except Exception as failure:  # noqa: BLE001 - told apart below, never raised raw
            message = str(failure)
            if re.search(r"not available|not initialized|no model", message, re.I):
                raise ComposeError("model_unavailable", message) from failure
            if attempt == 2:
                raise ComposeError("failed", message) from failure
            continue
        if isinstance(data, dict):
            break
    if not isinstance(data, dict):
        raise ComposeError("bad_output", "the model did not answer with an object")

    say = " ".join(str(data.get("say") or "").split())[:SAY_MAX]
    edits = data.get("edits")
    if isinstance(edits, dict):
        edits = [edits]
    if not isinstance(edits, list):
        edits = []
    # only what was planned for this sentence, whatever came back
    edits = [e for e in edits if isinstance(e, dict)
             and building._OPS.get(str(e.get("op", "")).strip().lower()) in ops]
    links = None
    if asked:
        edits = _said_outright(text, edits, own, things, scene)
    elif about:
        # what he makes of a moment is tied to it, and says so if he gave no reason of his own
        links = [{"type": str(about.get("kind", "")), "ref": about.get("ref"), "text": str(about.get("text", ""))[:200]}]
        for edit in edits:
            if str(edit.get("op", "")).lower() in ("create", "change") and len(str(edit.get("meaning") or "")) < 8:
                edit["meaning"] = str(about.get("text", ""))[:building.MEANING_MAX]

    outcome: Dict[str, Any] = {"ok": True, "results": [], "refused": []}
    if edits:
        outcome = building.apply(
            edits, actor="eli", asked=asked, default_room=room,
            summary=(text.strip() if asked else
                     (f"Because: {about['text']}"[:200] if about else "ELI built of his own accord")),
            near=embodiment.near_user(scene), source="composer", links=links,
            avoid=(scene["user"]["pos"][0], scene["user"]["pos"][2]) if scene else None,
        )
    failed = [r for r in outcome["results"] if not r.get("ok")] + list(outcome["refused"])
    done = [r for r in outcome["results"] if r.get("ok")]
    if failed and not done:
        # He said he would, and it did not happen: the user hears the truth.
        say = f"I could not do that: {failed[0].get('reason', 'it was not allowed')}."
    elif failed:
        say = (say + " " if say else "") + f"One part did not happen: {failed[0].get('reason', 'it was not allowed')}."
    elif not done and not say.endswith("?"):
        # Whatever the model claimed, nothing was changed. A question back to the user stands.
        say = "I have not changed anything." if asked else ""
    elif not say:
        say = "Done." if asked else ""
    return {"kind": "build", "text": say, "room": room, "asked": asked,
            "results": outcome["results"], "refused": outcome["refused"]}
