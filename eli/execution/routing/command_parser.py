"""
eli/execution/routing/command_parser.py
───────────────────────────────────────
The command language: a sentence read as what it tells ELI to do.

A command has a form: something in front of it (a greeting, "could you", "please"), a verb, what
the verb is about, and what qualifies it ("on spotify", "for five minutes", "at 7am", "again").
The rules in router_enhanced and its stages each know one phrasing of one command as a pattern
over its words; this reads the form first, so a request the patterns have not met ("could you
fire up spotify", "set a timer for twenty minutes", "jot down that ...", "close it") is understood
by its parts and carried to the action the catalogue gives those parts.

    read(text)          the Command in a sentence: its verb as the catalogue says it and as it
                        was said, its object, its modifiers by preposition; None when the
                        sentence is not telling ELI to do something
    route_command(...)  the stage: a route for the Command's frame, or None for the rules to try;
                        `strict` keeps it to frames whose object is an atom (an address, a path,
                        a duration, a time of day, a known site, what the last command was about)

The vocabulary is ELI's own. The verbs of each frame are the ones the capability catalogue's
activation phrases start with (eli/tools/registry/capabilities_doc.py; the test holds them to
it), with what else is said for them (SAID_FOR: "fire up", "put on", "jot down"); a catalogue
phrase with nothing variable in it ("show my habits") is a literal of the language and names
its action outright. The atoms are the router's own readers: a web address and a path
(routing/_helpers), a known site (app_aliases.WEBSITE_ALIASES), a streaming service
(media_runtime), a time of day (runtime/agenda), the number words of a duration (here). The
frames (FRAMES) say which verb with which kind of object is which action, and how the parts
become the action's arguments, in the shape the executor already takes from the rules. A
pronoun ("close it", "open it again") stands for what the last command was about, which the
engine puts on the turn's facts.

Not read here: a chain ("open spotify then play jazz") is divided first by
runtime/command_splitter and each part arrives alone; a question or a statement is not a
command (command_splitter.instruction says which) and goes on to the rules and the chat; a
shell command, a deletion, or a change to who the user is, is explicit-only and never comes
from a reading of form.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple

from eli.execution.routing._helpers import _is_likely_url, _known_dir_path, _mk, _normalize_url

# ── the number words of a duration ───────────────────────────────────────────────────────────────
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_NUMBER_WORD = "|".join([*_UNITS, *_TENS, "hundred"])
_SPELLED = re.compile(rf"\b((?:{_NUMBER_WORD})(?:[\s-]+(?:and[\s-]+)?(?:{_NUMBER_WORD}))*)\b", re.I)
_TIME_UNIT = r"(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"
# "half an hour", "an hour and a half", "quarter of an hour", "an hour", "a minute": said without digits
_FRACTIONS = (
    (re.compile(r"\b(?:an?\s+)?hour\s+and\s+a\s+half\b", re.I), "90 minutes"),
    (re.compile(r"\b(\d+)\s+and\s+a\s+half\s+hours?\b", re.I), lambda m: f"{int(m.group(1)) * 60 + 30} minutes"),
    (re.compile(r"\bhalf\s+an?\s+hour\b", re.I), "30 minutes"),
    (re.compile(r"\b(?:a\s+)?quarter\s+of\s+an?\s+hour\b", re.I), "15 minutes"),
    (re.compile(r"\bhalf\s+a\s+minute\b", re.I), "30 seconds"),
    (re.compile(rf"\ban?\s+({_TIME_UNIT})\b", re.I), lambda m: f"1 {m.group(1)}"),
    (re.compile(rf"\ba\s+couple\s+of\s+({_TIME_UNIT})\b", re.I), lambda m: f"2 {m.group(1)}"),
)


def _spelled_value(words: str) -> int:
    total = 0
    for w in re.split(r"[\s-]+", words.lower()):
        if w in _UNITS:
            total += _UNITS[w]
        elif w in _TENS:
            total += _TENS[w]
        elif w == "hundred":
            total = (total or 1) * 100
    return total


def in_digits(text: str) -> str:
    """The text with its spelled numbers as digits: "twenty five minutes" is "25 minutes", "half an
    hour" is "30 minutes", "an hour and a half" is "90 minutes"."""
    out = re.sub(r"\ba\s+(?=hundred\b)", "", str(text or ""), flags=re.I)
    out = _SPELLED.sub(lambda m: str(_spelled_value(m.group(1))), out)
    for rx, to in _FRACTIONS:
        out = rx.sub(to, out)
    return out


_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)
_PER_UNIT = {"h": 3600, "m": 60, "s": 1}


def duration_seconds(text: str) -> Optional[int]:
    """The duration a phrase names, in seconds, every part summed ("1 hour 30 minutes" is 5400;
    the rules took the first part only); None when it names none."""
    total = 0.0
    found = False
    for m in _DURATION.finditer(in_digits(text)):
        total += float(m.group(1)) * _PER_UNIT[m.group(2).lower()[0]]
        found = True
    return int(round(total)) if found and total > 0 else None


def clock_time(text: str) -> Optional[str]:
    """The time of day a phrase names, as HH:MM in 24-hour form ("7am" is 07:00, "half past six" is
    06:30, "7:30pm" is 19:30), from the agenda's reader (runtime/agenda.py). An hour said without
    am or pm is taken as said: "6:30" is 06:30. None when none is named."""
    from eli.runtime.agenda import _clock_candidates
    found = _clock_candidates(in_digits(text))
    if not found:
        return None
    _start, _end, hour, minute, ampm = found[0]
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


# ── the parts of a command ────────────────────────────────────────────────────────────────────────
@dataclass
class Command:
    verb: str                                    # the frame's verb, as the catalogue says it
    said: str                                    # the verb as said ("fire up")
    object: str                                  # what the verb is about, as said, without its article
    modifiers: Dict[str, str] = field(default_factory=dict)   # preposition -> phrase
    text: str = ""                               # the instruction, with what stood in front of it removed
    verbs: Tuple[str, ...] = ()                  # every frame verb the said form may be ("start": open, set)


_ARTICLE = re.compile(r"^(?:the|a|an|some|my|me|us)\s+", re.I)
_PRONOUN = re.compile(r"^(?:it|that|this|them|that one|this one|the same|the same one)$", re.I)
_AGAIN = re.compile(r"\s+(?:again|back up|up again|once more|one more time)$", re.I)
_MEDIA_NOUNS = frozenset(("music", "track", "song", "video", "tune", "playback", "media", "audio", "film",
                          "movie", "episode", "show", "podcast", "stream", "album", "playlist"))
_FILLER = frozenset(("the", "a", "an", "this", "that", "it", "my", "current", "same", "one", "playing", "now",
                     "please", "again"))
_VOLUME_NOUNS = frozenset(("", "volume", "sound", "music", "audio", "level"))
_A_BIT = re.compile(r"\s+(?:a\s+(?:bit|little|touch|notch|tad|lot|bit\s+more|little\s+more)|slightly|somewhat|"
                    r"a\s+little\s+bit|more|less)$", re.I)
_BY_PERCENT = re.compile(r"\bby\s+(\d{1,3})\s*%?$", re.I)
_SEARCH_LEAD = re.compile(r"^(?:(?:through\s+|in\s+|on\s+|across\s+)?(?:the\s+)?(?:web|internet|net|online)\s+for\s+"
                          r"|for\s+|up\s+|me\s+|about\s+)", re.I)
_SEARCH_TAIL = re.compile(r"\s+(?:online|on\s+the\s+(?:web|internet|net)|on\s+google)$", re.I)
# A search of ELI's own memory, notes or the conversation is recall, not the web: the rules read it.
_OWN_PAST = re.compile(r"\b(?:your|our|my)\s+(?:memory|memories|records|notes?|history|logs?|conversations?|chats?|"
                       r"messages?|files?|documents?|emails?)\b|\b(?:i|we)\s+(?:said|told|mentioned|asked|wrote|talked)\b"
                       r"|^(?:through|in|inside|within)\b", re.I)
_NOTE_LEAD = re.compile(r"^(?:that\s+|saying\s+|saying\s+that\s+|of\s+|this:?\s+|:\s*)", re.I)
_SCREEN = re.compile(r"^(?:screenshot|screen\s*shot|screen\s*grab|screen\s*capture|capture\s+of\s+(?:the|my)\s+screen|"
                     r"screen)$", re.I)
_SCREEN_OF = re.compile(r"^(?:the|my)\s+(?:screen|desktop|display)$|^(?:this|it|everything)$", re.I)
_BROWSER = re.compile(r"^(?:web\s+)?browser$", re.I)
_IDE = re.compile(r"^(?:ide|editor|built[- ]in\s+ide|eli\s+ide|internal\s+ide|ide\s+tab)$", re.I)


def _bare(phrase: str) -> str:
    """A phrase without its filler: "the current song" is "song", "it" is ""."""
    return " ".join(w for w in str(phrase or "").lower().split() if w not in _FILLER)


def _last_command() -> Dict[str, Any]:
    """What the turn's facts say the last command was (the engine puts it there), when recent."""
    from eli.kernel.request_context import turn_facts_var
    last = (turn_facts_var.get() or {}).get("last_command") or {}
    if not isinstance(last, dict) or not last.get("action"):
        return {}
    from eli.runtime.pending_proposal import _TTL_SECONDS
    try:
        if time.time() - float(last.get("ts") or 0.0) > _TTL_SECONDS:
            return {}
    except (TypeError, ValueError):
        return {}
    return last


def what_was_played(query: str) -> str:
    """A play's query with a pronoun for what was last asked to play ("play it on youtube", "no,
    play that one again"): that query, or "" when nothing was. Any other query as it is."""
    said = str(query or "").strip()
    if not _PRONOUN.match(_AGAIN.sub("", said).strip()):
        return said
    last = _last_command()
    return str(((last.get("args") or {}).get("query")) or "").strip() if "PLAY" in str(last.get("action") or "") else ""


def last_object() -> str:
    """What the last command was about: the app, address, path or query in its arguments."""
    args = _last_command().get("args") or {}
    for key in ("name", "target", "url", "path", "query"):
        value = args.get(key)
        if isinstance(value, str) and value.strip() and not _PRONOUN.match(value.strip()):
            return value.strip()
    return ""


# "that folder", "this file", "the directory", "what that folder contains": a place named by pointing
# back at what the last command opened or listed.
_POINTS_AT_A_PLACE = re.compile(
    r"^(?:(?:what|everything|all)\s+(?:is\s+)?(?:in\s+)?)?(?:the\s+contents?\s+of\s+)?"
    r"(?:it|that|this|there|(?:that|this|the|those|these)\s+(?:same\s+)?(?:folder|directory|dir|file|files|document|place))"
    r"(?:\s+(?:contains?|has|holds|has\s+in\s+it|again))?$", re.I)


def place_referred_to(phrase: str) -> Optional[str]:
    """The place a phrase points back at ("it", "that folder", "what that folder contains"): the
    path the last command acted on, when it names one that exists. "" when the phrase points back
    and there is nothing it can mean (a path is never made of the word "it"); None when the phrase
    is not a pointer."""
    said = re.sub(r"\s+", " ", str(phrase or "").strip().strip(".,;:!?")).strip()
    if not _POINTS_AT_A_PLACE.match(said):
        return None
    was = last_object()
    if not was:
        return ""
    import os
    from eli.execution.routing._helpers import _known_dir_path
    for candidate in (was, _known_dir_path(was.lower()) or ""):
        if candidate and os.path.exists(os.path.expanduser(candidate)):
            return os.path.abspath(os.path.expanduser(candidate))
    return ""


# ── the frames ───────────────────────────────────────────────────────────────────────────────────
# What else is said for a catalogue verb. The first form of each frame's `verbs` is the catalogue's.
SAID_FOR: Dict[str, Tuple[str, ...]] = {
    "open": ("open", "launch", "start", "run", "fire up", "bring up", "pull up", "boot up", "start up", "open up",
             "go to"),
    "close": ("close", "quit", "exit", "kill", "shut", "close down", "terminate"),
    "focus": ("focus", "focus on", "switch to", "jump to", "go over to"),
    "maximise": ("maximise", "maximize", "fullscreen", "full screen", "make fullscreen"),
    "minimise": ("minimise", "minimize", "hide"),
    "play": ("play", "put on", "stick on", "throw on", "queue up", "start playing"),
    "pause": ("pause", "hold"),
    "stop": ("stop", "stop playing", "end"),
    "next": ("next", "skip", "skip to the next", "go to the next", "move on to the next", "jump to the next"),
    "previous": ("previous", "go back", "back", "go back to the previous", "go back to the last", "replay the last",
                 "rewind to the previous", "back to the previous"),
    "search": ("search", "search for", "search the web for", "google", "look up", "look for", "find", "find me",
               "web search", "web search for", "do a web search for", "do a search for", "search online for"),
    "screenshot": ("take", "grab", "capture", "snap", "get", "make", "screenshot"),
    "note": ("write", "jot down", "note down", "write down", "take down", "note", "make a note", "take a note",
             "write a note", "add a note", "save a note", "note that"),
    "set": ("set", "create", "start", "make", "set up", "give me"),
    "wake": ("wake me", "wake me up", "get me up"),
    "turn up": ("turn up", "raise", "increase", "crank up", "bump up", "turn the volume up"),
    "turn down": ("turn down", "lower", "decrease", "turn the volume down"),
}


@dataclass(frozen=True)
class Frame:
    name: str                       # names the route: matched_by "parser.<name>"
    verb: str                       # key of SAID_FOR
    action: str
    kind: str                       # what the object is: url | place | app | text | media | duration | clock | screen | volume
    modifiers: Tuple[str, ...] = () # prepositions read as modifiers
    strict: bool = False            # a frame whose object is an atom: run before the rules
    confidence: float = 0.9


FRAMES: Tuple[Frame, ...] = (
    Frame("timer", "set", "SET_TIMER", "duration", ("for",), strict=True, confidence=0.95),
    Frame("alarm", "set", "SET_ALARM", "clock", ("for", "at"), strict=True, confidence=0.95),
    Frame("wake", "wake", "SET_ALARM", "clock", ("at", "for"), strict=True, confidence=0.95),
    Frame("screenshot", "screenshot", "SCREENSHOT", "screen", ("of",), strict=True, confidence=0.95),
    Frame("open_url", "open", "OPEN_URL", "url", ("in",), strict=True, confidence=0.95),
    Frame("open_place", "open", "OPEN_FILE_SYSTEM", "place", ("in",), strict=True, confidence=0.95),
    Frame("open_browser", "open", "OPEN_BROWSER", "browser", (), strict=True, confidence=0.95),
    Frame("open_ide", "open", "OPEN_IDE", "ide", (), strict=True, confidence=0.95),
    Frame("open_app", "open", "OPEN_APP", "app", ("in",), confidence=0.9),
    Frame("close_app", "close", "CLOSE_APP", "app", (), confidence=0.9),
    Frame("focus_app", "focus", "FOCUS_APP", "app", (), confidence=0.88),
    Frame("maximise_app", "maximise", "MAXIMISE_WINDOW", "app", (), confidence=0.9),
    Frame("minimise_app", "minimise", "MINIMIZE_APP", "app", (), confidence=0.9),
    Frame("next", "next", "NEXT_MEDIA", "media", (), confidence=0.9),
    Frame("previous", "previous", "PREVIOUS_MEDIA", "media", (), confidence=0.9),
    Frame("pause", "pause", "PAUSE_MEDIA", "media", (), confidence=0.9),
    Frame("stop", "stop", "STOP_MEDIA", "media", (), confidence=0.9),
    Frame("play", "play", "PLAY_MEDIA", "text", ("on",), confidence=0.88),
    Frame("search", "search", "WEB_SEARCH", "text", (), confidence=0.88),
    Frame("note", "note", "WRITE_NOTE", "text", (), confidence=0.9),
    # the volume's own words are atoms: these run before the rules (see also volume_said)
    Frame("volume_up", "turn up", "VOLUME", "volume", (), strict=True, confidence=0.9),
    Frame("volume_down", "turn down", "VOLUME", "volume", (), strict=True, confidence=0.9),
)

# Verbs that may stand in front of a timer or an alarm ("start a timer", "give me a 10 minute timer").
_TIMER_HEAD = re.compile(r"^(?:timer|countdown|count\s*down)\b", re.I)
_ALARM_HEAD = re.compile(r"^(?:alarm|wake[- ]up\s+call)\b", re.I)
_NOT_AN_APP = re.compile(r"^(?:window|this|everything|all|all\s+windows|workspace\b.*|desktop|tab|it|that)$", re.I)
# An app is named by a short phrase. "pull up each of those 59 findings once more" names what ELI
# listed, not an app: words that point at things, and a long phrase, are not a name.
_POINTS_AT_THINGS = frozenset(("each", "every", "those", "these", "that", "this", "all", "them", "it", "again",
                               "more", "last", "previous", "earlier", "findings", "results", "one", "ones", "once"))
_APP_NAME_WORDS = 4


def _names_an_app(obj: str) -> bool:
    words = obj.lower().split()
    return bool(words) and len(words) <= _APP_NAME_WORDS and not any(w in _POINTS_AT_THINGS for w in words)
# "start with the outline", "run through it again": the verb opens nothing (app_launch has the same test)
_NOT_AN_OBJECT = re.compile(r"^(?:with|by|from|on|at|in|to|through|over|again|now|off|out|as|for|into|away|around|"
                            r"ahead|here|there|up|down)\b", re.I)
# "go to youtube.com" opens an address; "go to sleep" opens nothing
_ADDRESS_ONLY = frozenset(("go to",))
# "turn it down a bit", "turn the volume up": the verb's particle comes after its object
_PHRASAL = re.compile(r"^turn\s+(.+?)\s+(up|down)(?:\s+(.*))?$", re.I)


def _instruction(text: str) -> str:
    """The instruction in a sentence without what stands in front of it or the courtesy after it;
    "" when the sentence tells ELI nothing to do."""
    from eli.runtime.command_splitter import instruction
    said = instruction(str(text or ""))
    if not said:
        return ""
    from eli.execution.router_enhanced import _without_courtesy
    return _without_courtesy(said).strip(" .,!;:")


def _verb_forms() -> List[Tuple[str, Tuple[str, ...]]]:
    """(said form, the verbs it may be) longest first, so "search the web for" is read before
    "search"; "start" is open or set, and the frames decide by the object."""
    by_form: Dict[str, List[str]] = {}
    for verb, said in SAID_FOR.items():
        for form in said:
            by_form.setdefault(form, []).append(verb)
    return sorted(((form, tuple(verbs)) for form, verbs in by_form.items()), key=lambda f: -len(f[0]))


_FORMS = _verb_forms()


def _split_modifiers(obj: str, preps: Tuple[str, ...]) -> Tuple[str, Dict[str, str]]:
    """The object before its last modifier and the modifiers by preposition: "juicy by x on spotify"
    with ("on",) is ("juicy by x", {"on": "spotify"})."""
    mods: Dict[str, str] = {}
    rest = obj
    for prep in preps:
        m = re.search(rf"\s+{prep}\s+(.+)$", rest, re.I)
        if m and not mods:
            mods[prep] = m.group(1).strip()
            rest = rest[:m.start()].strip()
    if not rest and not mods:
        return "", {}
    return rest, mods


def read(text: str) -> Optional[Command]:
    """The Command a sentence is, or None when it is not one: no verb of the language starts it."""
    said = _instruction(text)
    if not said:
        return None
    low = said.lower()
    phrasal = _PHRASAL.match(said)
    if phrasal:
        verb = f"turn {phrasal.group(2).lower()}"
        obj = " ".join(p for p in (phrasal.group(1), phrasal.group(3)) if p)
        return Command(verb=verb, said=f"turn … {phrasal.group(2).lower()}", object=_ARTICLE.sub("", obj), text=said,
                       verbs=(verb,))
    for form, verbs in _FORMS:
        if low == form or low.startswith(form + " "):
            rest = said[len(form):].strip()
            if rest and _NOT_AN_OBJECT.match(rest) and set(verbs) & {"open", "close", "focus", "play", "set"}:
                continue
            obj = _ARTICLE.sub("", rest) if rest else ""
            return Command(verb=verbs[0], said=form, object=obj, text=said, verbs=verbs)
    return None


def _classify_open(obj: str) -> Tuple[str, Any]:
    """What an open-verb's object is: ("url", address) | ("place", path) | ("browser", None) |
    ("ide", None) | ("app", name)."""
    from eli.execution.app_aliases import WEBSITE_ALIASES
    low = obj.lower().strip()
    low = re.sub(r"\s+app$", "", low)
    if _BROWSER.match(low):
        return "browser", None
    if _IDE.match(low):
        return "ide", None
    if low in WEBSITE_ALIASES:
        return "url", WEBSITE_ALIASES[low]
    if _is_likely_url(obj):
        return "url", _normalize_url(obj)
    if low in ("trash", "/trash"):
        return "place", "trash"
    if obj.startswith(("/", "~", "./", "../")) or "/" in obj or "\\" in obj:
        return "place", obj
    known = _known_dir_path(low)
    if known:
        return "place", known
    return "app", obj


def _by_the_rules(frame: Frame, cmd: Command, obj: str, entities: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The command said in the catalogue's words ("fire up spotify" as "open spotify"), put to the
    rules, which know more of objects than a frame does (a file manager, a site said by name, a
    browser with a query). Their route when they give a command; None when they do not, or when
    the words are the ones already read (the rules had them)."""
    canonical = f"{frame.verb} {obj}".strip()
    if canonical.lower() == cmd.text.lower():
        return None
    from eli.execution.route_contracts import is_command
    from eli.execution.router_enhanced import route
    hit = route(canonical)
    if not isinstance(hit, dict) or not is_command(hit):
        return None
    meta = dict(hit.get("meta") or {})
    meta["matched_by"] = f"parser.{frame.name}>{meta.get('matched_by') or 'rule'}"
    meta["entities"] = {**entities, "canonical": canonical, **(meta.get("entities") or {})}
    return {**hit, "meta": meta}


def _realise(frame: Frame, cmd: Command, *, strict: bool) -> Optional[Dict[str, Any]]:
    """The route for a Command read under a frame, or None when the frame does not fit it."""
    obj, mods = _split_modifiers(cmd.object, frame.modifiers)
    entities = {"verb": cmd.said, "object": obj, **({"modifiers": mods} if mods else {})}
    pronoun = bool(obj) and bool(_PRONOUN.match(_AGAIN.sub("", obj).strip()))
    if frame.kind == "duration":
        if not _TIMER_HEAD.match(obj) and not (cmd.verb == "set" and _TIMER_HEAD.search(obj)):
            return None
        seconds = duration_seconds(" ".join([obj, *mods.values()]))
        if seconds is None:
            return None
        return _mk(frame.action, {"duration": seconds}, frame.confidence, matched_by=f"parser.{frame.name}",
                   entities={**entities, "seconds": seconds})
    if frame.kind == "clock":
        if cmd.verb == "set" and not _ALARM_HEAD.match(obj):
            return None
        when = clock_time(" ".join([obj, *mods.values()]))
        if when is None:
            return None
        return _mk(frame.action, {"time": when}, frame.confidence, matched_by=f"parser.{frame.name}",
                   entities={**entities, "time": when})
    if frame.kind == "screen":
        said_screenshot = cmd.said == "screenshot"
        if said_screenshot and obj and not _SCREEN_OF.match(obj):
            return None
        if not said_screenshot and not _SCREEN.match(obj):
            return None
        if mods and not _SCREEN_OF.match(mods.get("of", "")):
            return None
        return _mk(frame.action, {"region": "full"}, frame.confidence, matched_by=f"parser.{frame.name}",
                   entities=entities)
    if frame.kind in ("url", "place", "browser", "ide", "app"):
        if mods:
            return None                      # "open github.com in firefox": the rules read the browser
        if not obj:
            return None
        if pronoun:
            was = last_object()
            if not was:
                return None
            obj = was
            entities["object"] = was
        kind, value = _classify_open(obj) if cmd.verb == "open" else ("app", obj)
        if kind != frame.kind:
            return None
        if kind == "app":
            if _NOT_AN_APP.match(obj) or (strict and not pronoun) or cmd.said in _ADDRESS_ONLY:
                return None
            if not pronoun and not _names_an_app(obj):
                return None
            by_rule = _by_the_rules(frame, cmd, obj, entities)
            if by_rule is not None:
                return by_rule
            from eli.execution.app_aliases import APP_ALIASES
            name = APP_ALIASES.get(obj.lower(), obj)
            args = {"name": name, "target": name} if frame.action in ("OPEN_APP", "MAXIMISE_WINDOW", "MINIMIZE_APP") \
                else {"name": name}
        elif kind == "url":
            args = {"url": value}
        elif kind == "place":
            args = {"path": value}
        elif kind == "ide":
            args = {"name": "ide"}
        else:
            args = {}
        return _mk(frame.action, args, frame.confidence, matched_by=f"parser.{frame.name}", entities=entities)
    if frame.kind == "media":
        bare = _bare(obj)
        if not obj or (bare and bare not in _MEDIA_NOUNS) or (not bare and not pronoun):
            return None
        return _mk(frame.action, {}, frame.confidence, matched_by=f"parser.{frame.name}", entities=entities)
    if frame.kind == "volume":
        direction = "up" if frame.verb == "turn up" else "down"
        delta = None
        m = _BY_PERCENT.search(obj)
        if m:
            delta = int(m.group(1))
            obj = obj[:m.start()].strip()
        obj = _A_BIT.sub("", obj).strip()
        tail = re.search(r"\s+(up|down)$", obj, re.I)
        if tail:
            direction = tail.group(1).lower()
            obj = obj[:tail.start()].strip()
        if _bare(obj) not in _VOLUME_NOUNS:
            return None
        args = {"direction": direction, **({"delta": delta} if delta else {})}
        return _mk(frame.action, args, frame.confidence, matched_by=f"parser.{frame.name}", entities=entities)
    if frame.kind == "text":
        if strict:
            return None
        if frame.name == "play":
            if not obj or _bare(obj) in _MEDIA_NOUNS or not _bare(obj):
                return None
            by_rule = _by_the_rules(frame, cmd, cmd.object, entities)
            if by_rule is not None:
                return by_rule
            target = None
            if mods.get("on"):
                from eli.execution.media_runtime import normalize_streaming_target
                target = normalize_streaming_target(mods["on"]) or mods["on"].lower()
            return _mk(frame.action, {"query": obj, "target": target}, frame.confidence,
                       matched_by=f"parser.{frame.name}", entities={**entities, "query": obj, "target": target})
        if frame.name == "search":
            query = _SEARCH_TAIL.sub("", _SEARCH_LEAD.sub("", cmd.object).strip()).strip(" ?.!")
            if not query or query.lower() in ("it", "that", "this") or _OWN_PAST.search(query):
                return None
            return _mk(frame.action, {"query": query}, frame.confidence, matched_by=f"parser.{frame.name}",
                       entities={**entities, "query": query})
        if frame.name == "note":
            body = cmd.object
            if cmd.said in ("write", "note", "take down"):
                # "write a note saying X", "note that X"; "write X" alone is not a note
                m = re.match(r"^(?:(?:a|the|down)\s+)*notes?(?:\s+down)?\s*[:\s]\s*(.+)$", body, re.I | re.S)
                if not m and cmd.said != "note":
                    return None
                body = m.group(1) if m else body
            body = _NOTE_LEAD.sub("", body).strip()
            if not body:
                return None
            return _mk(frame.action, {"text": body}, frame.confidence, matched_by=f"parser.{frame.name}",
                       entities={**entities, "text": body})
    return None


# ── the volume, said by its noun ─────────────────────────────────────────────────────────────────
# "volume 60%", "vol 60", "set the volume to 60", "vol down 10%", "volume up by 5", "sound off": the
# noun first, then a direction or a level. The one reading of this; the portable contract, the voice
# contract and the router's audio rules each held a copy, every absolute copy needed the full word
# "volume", and the down rule dropped its number. A step that is not said is left to the effector
# (no step of its own here).
_VOLUME_NOUN = r"(?:vol(?:ume)?|sound|audio)"
_VOLUME_SAID = re.compile(
    rf"^(?:(?:set|change|put|make|turn)\s+)?(?:the\s+|my\s+)?{_VOLUME_NOUN}"
    r"(?:\s+(?P<dir>up|down|louder|quieter|higher|lower))?"
    r"(?:\s+(?:to|at|by))?"
    r"(?:\s+(?P<num>\d{1,3})\s*(?:%|percent|per\s*cent)?|\s+(?P<word>max(?:imum)?|full|off|zero|mute|half))?"
    r"(?:\s+(?:please|now|thanks))*\s*[.!]*$", re.I)


_LOUDER = re.compile(r"^(?:(?:a\s+)?(?:bit|little|touch|tad)\s+)?(louder|quieter)(?:\s+(?:please|now|thanks))*\s*[.!]*$", re.I)


# Asking what the volume is: "current volume?", "what's the vol at", "how loud is it", "volume level".
_VOLUME_ASKED = re.compile(
    rf"^(?:(?:what(?:'s|\s+is)|whats|check|tell\s+me|show(?:\s+me)?)\s+)?(?:the\s+|my\s+)?(?:current\s+)?{_VOLUME_NOUN}"
    r"(?:\s+(?:level|status|now|set\s+to|at))*\s*[?.!]*$"
    rf"|^(?:what(?:'s|\s+is)|whats)\s+(?:the\s+|my\s+)?(?:current\s+)?{_VOLUME_NOUN}\s+(?:level|at|set\s+to)\b.*$"
    r"|^how\s+loud\s+(?:is\s+(?:it|the\s+(?:sound|music|volume|audio))|am\s+i|are\s+you)(?:\s+(?:now|set))?\s*[?.!]*$",
    re.I)


def volume_said(text: str) -> Optional[Dict[str, Any]]:
    """The VOLUME route for a message that names the volume and what to do with it, or None.
    Absolute when a level is named without a direction ({"level", "percent", "mode": "absolute"}),
    relative with a direction ({"direction", "delta"?}), a reading when it asks what the volume is
    ({"direction": "get"}: the effector reads the level back)."""
    said = _instruction(text) or str(text or "").strip()
    if _VOLUME_ASKED.match(said) and not re.search(r"\d", said):
        # a reading, asked as a question by nature: the fit test for a change does not apply
        return _mk("VOLUME", {"direction": "get"}, 0.95, matched_by="parser.volume_get", entities={"said": said},
                   meta_extra={"spoken_as_remark": True})
    louder = _LOUDER.match(said)
    if louder:
        return _mk("VOLUME", {"direction": "up" if louder.group(1).lower() == "louder" else "down"}, 0.95,
                   matched_by="parser.volume_louder", entities={"said": said})
    m = _VOLUME_SAID.match(in_digits(said).strip())
    if not m or not (m.group("dir") or m.group("num") or m.group("word")):
        return None
    word = (m.group("word") or "").lower()
    direction = (m.group("dir") or "").lower()
    direction = {"louder": "up", "higher": "up", "quieter": "down", "lower": "down"}.get(direction, direction)
    entities = {"said": said}
    if direction:
        if word:
            return None
        args: Dict[str, Any] = {"direction": direction}
        if m.group("num"):
            args["delta"] = max(1, min(100, int(m.group("num"))))
        return _mk("VOLUME", args, 0.95, matched_by=f"parser.volume_{direction}", entities=entities)
    if word in ("off", "zero", "mute"):
        level = 0
    elif word in ("max", "maximum", "full"):
        level = 100
    elif word == "half":
        level = 50
    else:
        level = max(0, min(100, int(m.group("num"))))
    return _mk("VOLUME", {"level": level, "percent": level, "mode": "absolute", "direction": "set"}, 0.99,
               matched_by="parser.volume_absolute", entities={**entities, "level": level})


# ── the literals of the language ─────────────────────────────────────────────────────────────────
_VARIABLE = re.compile(r"\b[XY]\b|…|\.\.\.|\w+\.\w{2,4}\b|\d|“|”|\(|/", re.I)
# Words that carry no meaning of their own in a literal: "show my habits" names HABIT_STATUS by
# "show" and "habits" alone.
_FUNCTION_WORDS = frozenset(("the", "a", "an", "my", "me", "your", "you", "this", "that", "it", "up", "of", "to",
                             "for", "in", "on", "at", "with", "and", "or", "all", "please", "now", "i", "how",
                             "what", "which", "some", "any", "do", "does", "are", "is", "be", "give", "get", "show",
                             "tell", "run", "check", "list", "explain", "new", "latest", "last", "current"))


def _names_its_action(said: str, action: str) -> bool:
    """Whether every word of a phrase that means something is a word of the action's own name or
    description: "stop the pomodoro" for POMODORO_STOP, not "more on the transformer story" for
    NEWS_FETCH (the story is an example, not the action)."""
    from eli.execution.route_contracts import _same_word
    from eli.tools.registry.capabilities_doc import action_words
    own = set(re.findall(r"[a-z]+", action_words(action, with_phrases=False)))
    for word in re.findall(r"[a-z]+", said.lower()):
        if word in _FUNCTION_WORDS:
            continue
        if word not in own and not any(_same_word(word, w) for w in own):
            return False
    return True


@lru_cache(maxsize=1)
def literals() -> Dict[str, str]:
    """Catalogue phrases that name their action in the action's own words and have nothing variable
    in them, by their words: "stop the pomodoro" is POMODORO_STOP. Internal and explicit-only
    actions and ELI's world (a game needs to be on) are left out, as is CHAT."""
    from eli.tools.registry.capabilities_doc import _C
    from eli.cognition.llm_intent import _EXPLICIT_ONLY_ACTIONS, _INTERNAL_ACTIONS
    out: Dict[str, str] = {}
    for action, (category, _desc, phrases) in _C.items():
        if action in _INTERNAL_ACTIONS or action in _EXPLICIT_ONLY_ACTIONS or category == "ELI's world":
            continue
        for phrase in phrases:
            said = phrase.strip("“”\"' ").lower().strip(" .!?")
            if not said or _VARIABLE.search(said) or not _names_its_action(said, action):
                continue
            out.setdefault(said, action)
    return out


def _literal(cmd_text: str) -> Optional[Dict[str, Any]]:
    action = literals().get(cmd_text.lower().strip(" .!?"))
    if not action:
        return None
    return _mk(action, {}, 0.92, matched_by="parser.literal", entities={"said": cmd_text})


# ── the stage ────────────────────────────────────────────────────────────────────────────────────
def route_command(text: str, raw: str, low: str, text_l: str, *, strict: bool = False) -> Optional[Dict[str, Any]]:
    """A route for the command a sentence is, under the first frame that fits its parts. With
    `strict`, only frames whose object is an atom, and no literals: the rules have not had their
    turn yet. None leaves the sentence to the rules."""
    volume = volume_said(raw)
    if volume is not None:
        return volume
    cmd = read(raw)
    if cmd is None:
        if strict:
            return None
        said = _instruction(raw)
        return _literal(said) if said else None
    # a pronoun with a referent is an atom too: "close it" after "open firefox" is "close firefox"
    pronoun = bool(_PRONOUN.match(_AGAIN.sub("", cmd.object).strip()))
    for frame in FRAMES:
        if frame.verb not in cmd.verbs or (strict and not frame.strict and not pronoun):
            continue
        hit = _realise(frame, cmd, strict=strict)
        if hit is not None:
            return hit
    if not strict:
        return _literal(cmd.text)
    return None


def route_command_strict(text: str, raw: str, low: str, text_l: str) -> Optional[Dict[str, Any]]:
    return route_command(text, raw, low, text_l, strict=True)


def route_command_tail(text: str, raw: str, low: str, text_l: str) -> Optional[Dict[str, Any]]:
    return route_command(text, raw, low, text_l, strict=False)


__all__ = ["Command", "FRAMES", "Frame", "SAID_FOR", "clock_time", "duration_seconds", "in_digits", "last_object",
           "place_referred_to", "volume_said", "what_was_played", "literals", "read", "route_command", "route_command_strict", "route_command_tail"]
