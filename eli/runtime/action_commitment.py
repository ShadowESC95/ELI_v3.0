"""Detect when ELI's reply COMMITS to performing an action.

The rule (user-requested, 2026-06): no fake actions. If ELI says it will do something
("let me check the news", "fetching now", "I'll re-run that"), the caller must
actually re-run the pipeline and DO it — never let the promise stand as theatre,
and never emit fill-in placeholders ("[Story 1]", "checking…").

This module is pure/deterministic (no engine deps) so it can be unit-tested and
reused by any consumer. It returns the clause to re-dispatch; the caller routes
it through the real router→executor so the actual task runs.
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any, Dict, Optional

# Verbs ELI uses when promising to actually perform/redo a task: the phrasal ones below, and the verbs
# of looking something up or over that ELI's own actions are named with (read, list, scan, examine,
# analyse...: cognition.self_claims.looking_verbs, the one list the claim check uses too). "I will now
# read the actual content of key files" was not a promise to this module, and nothing read them.
# Deliberately excludes vague ones ("get back to you", "think", "know") to avoid false hits.
_PHRASAL_VERBS = (r"re-?check|re-?fetch|look(?:ing)?\s+(?:up|into|through|over)|go(?:ing)?\s+through|"
                  r"pull\s+(?:up|it\s+up)|run|re-?run|verify|confirm|find\s+out|update|refresh|look\s+that\s+up")


def _verb_forms(verbs) -> str:
    stems = sorted({v[:-1] if v.endswith("e") else v for v in verbs}, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(v) for v in stems) + r")\w{0,3}"


_COMMIT_LEAD = (r"\b(?:let\s+me|let'?s|i'?ll|i\s+will|i\s+am\s+going\s+to|i'?m\s+going\s+to|"
                r"allow\s+me\s+to|give\s+me\s+(?:a\s+)?(?:moment|sec(?:ond)?)|one\s+moment|"
                r"hang\s+on|on\s+it,?)\b[^.!?\n]{0,60}?\b")


@lru_cache(maxsize=1)
def _commit_patterns():
    """(_COMMIT_RE, _LOOK_RE, _DOING_RE), built on first use from the claim check's list of looking
    verbs. A looking verb is a promise to act only on one of the things ELI's actions act on ("let me
    read the key files"); "let me read that back to you" is saying it again."""
    try:
        from eli.cognition.self_claims import looking_verbs
        looking = set(looking_verbs()) - {"check", "fetch", "search"}
    except Exception:
        looking = set()
    # "let me … <verb>", "I'll … <verb>", "give me a moment … <verb>", etc.
    commit = re.compile(_COMMIT_LEAD + r"(?:check|fetch|search|" + _PHRASAL_VERBS + r")\b", re.I)
    look = re.compile(_COMMIT_LEAD + _verb_forms(looking) + r"\b", re.I) if looking else None
    # Present-tense "doing it now" theatre.
    doing = re.compile(
        r"\b(?:checking|re-?checking|fetching|re-?fetching|searching|"
        r"looking\s+that\s+up|running\s+that|pulling\s+(?:that|it)\s+up|verifying)\b"
        r"(?:\s*\.\.\.|\s+now|\s+for\s+you)?",
        re.I,
    )
    return commit, look, doing


def _looks_at_something(text: str, at: int) -> bool:
    """Whether what follows a looking verb names a thing ELI's actions act on, or a path."""
    rest = text[at:at + 100]
    rest = re.split(r"[.!?\n]", rest, maxsplit=1)[0]
    if re.search(r"`[^`]+`|[~.]?/[\w.-]+/", rest):
        return True
    try:
        from eli.cognition.self_claims import machine_things
        things = machine_things()
    except Exception:
        return False
    return any(w in things for w in re.findall(r"[a-z]+", rest.lower()))


# Obvious fabricated fill-ins / fake-fetch markers — always a fake action.
_FAKE_THEATRE_RE = re.compile(
    r"\bchecking\s*\.\.\.|\(\s*fetching|\[\s*story\s*\d|\[\s*insert\b|\[\s*headline\s*\d",
    re.I,
)


# User directives/challenges that mean "actually (re-)do the task": the caller re-runs the last real
# action instead of letting ELI chat or defend itself. Requires "again / it / that" or a doubt ("are
# you actually ...ing") so a fresh request ("check the news") isn't treated as a redo.
_REDO_RE = re.compile(
    r"\b(?:"
    r"(?:do|run|check|fetch|search|look)\s+(?:it|that)\s+again|"
    r"(?:check|fetch|search|run|look)\s+(?:it|that|again)|"
    r"re-?(?:run|check|fetch|do|try)\b|"
    r"try\s+(?:that\s+|it\s+)?again|"
    r"(?:are|were)\s+you\s+(?:actually|even|really)\s+\w+ing|"
    r"did\s+you\s+(?:actually|even|really)\s+\w+|"
    r"go\s+(?:on|ahead)\s+(?:then|and\s+\w+)|"
    r"actually\s+(?:do|run|check|fetch|search)\s+it|"
    # It didn't happen: "you did not open spotify", "spotify didn't open", "nothing happened".
    # Live: "You did not open spotify again!!!" got a made-up account of checking the logs.
    r"you\s+(?:did\s*n[o']?t|didn'?t|never|have\s*n[o']?t|haven'?t)\s+(?:actually\s+|even\s+|really\s+)?\w+|"
    # The same, asked: "why did you not fetch the real story", "why didn't you open it".
    r"(?:did\s+you\s+not|didn'?t\s+you|have\s*n'?t\s+you)\s+(?:actually\s+|even\s+|really\s+)?\w+|"
    r"\b(?!(?:i|we)\b)\w+\s+(?:did\s*n[o']?t|didn'?t|never|has\s*n[o']?t|hasn'?t|won'?t)\s+"
    r"(?:open|opened|play|played|start|started|launch|launched|load|loaded|work|worked|happen|happened)|"
    r"nothing\s+(?:happened|opened|played|started|is\s+playing)"
    r")\b",
    re.I,
)


def is_redo_directive(text: str) -> bool:
    """True when the user is telling ELI to actually (re-)perform the task —
    so the caller can re-run the LAST real action rather than route to chat."""
    s = str(text or "")
    m = _REDO_RE.search(s)
    if not m:
        return False
    # Exclude "I'll check it myself" — the USER doing it, not a directive to ELI.
    pre = s[max(0, m.start() - 25):m.start()].lower()
    if "myself" in s.lower() or re.search(r"\b(?:i|i'?ll|i'?m|we|we'?ll)\b", pre):
        return False
    return True


# Re-running the last action on "did you actually X?" is only right when X is
# that action and it just happened. Live (2026-10-02): "did you actually read the
# files" re-ran a Spotify pause from seven minutes earlier and paused the music.
REDO_MAX_AGE_S = 300.0

# Verbs that point back at whatever ELI last did, not at one kind of action.
_GENERIC_REDO_VERBS = {
    "do", "doing", "did", "done", "run", "running", "check", "checking", "try",
    "trying", "go", "look", "looking", "fetch", "fetching", "search", "searching",
    "it", "that", "again", "work", "working", "worked", "happen", "happening", "happened",
}
# Words that say nothing about WHAT should have happened.
_NOT_AN_OBJECT = {
    "the", "a", "an", "it", "that", "this", "again", "me", "my", "for", "properly", "even",
    "actually", "really", "when", "i", "asked", "you", "to", "at", "all", "fucking", "fuckin",
    "bloody", "just", "yet", "now", "please", "song", "track", "music", "app", "still",
    "not", "didn't", "didnt", "never", "has", "hasn't", "hasnt", "have", "haven't", "havent",
    "won't", "wont", "nothing", "is", "what", "which", "who", "how", "why", "where", "whatever", "series", "show",
}


def _redo_verb(text: str) -> Optional[str]:
    m = _REDO_RE.search(str(text or ""))
    if not m:
        return None
    words = re.findall(r"[a-z]+", m.group(0).lower())
    return words[-1] if words else None


def redo_applies(text: str, last_cmd: Optional[Dict[str, Any]],
                 now: Optional[float] = None) -> bool:
    """is_redo_directive, and the last action is recent and is what it's about."""
    if not last_cmd or not is_redo_directive(text):
        return False
    age = (time.time() if now is None else float(now)) - float(last_cmd.get("ts") or 0.0)
    if age > REDO_MAX_AGE_S:
        return False
    # A complaint that names what didn't happen ("you did not open spotify") is about the last
    # command only if that command was about the same thing.
    verb = _redo_verb(text)
    s = str(text or "")
    m = _REDO_RE.search(s)
    said = re.findall(r"[a-z0-9']+", m.group(0).lower()) + re.findall(r"[a-z0-9']+", s[m.end():].lower())[:4]
    named = [w for w in said if w != verb and w not in _NOT_AN_OBJECT and w not in _GENERIC_REDO_VERBS]
    if named:
        # What the last command was about: what was said, its arguments, and what the catalogue
        # says that action does ("you didn't fetch the story" is about a news fetch because stories
        # are what the catalogue says it fetches). Not the catalogue's example phrases: they name
        # particular things, and "launch firefox" there made "you did not open firefox" a redo of
        # "open spotify".
        try:
            from eli.tools.registry.capabilities_doc import action_words
            about = action_words(str(last_cmd.get("action") or ""), with_phrases=False)
        except Exception:
            about = ""
        last_words = " ".join([str(last_cmd.get("input") or ""), str(last_cmd.get("action") or "").replace("_", " "),
                               about]
                              + [str(v) for v in (last_cmd.get("args") or {}).values()]).lower()
        if not any(w in last_words for w in named):
            return False
    if not verb or verb in _GENERIC_REDO_VERBS:
        return True
    stem = re.sub(r"(?:ing|ed)$", "", verb)  # pausing -> paus, played -> play
    tokens = [t for t in str(last_cmd.get("action") or "").lower().split("_") if len(t) >= 3]
    return any(t.startswith(stem[:4]) or stem.startswith(t[:4]) for t in tokens)


# Words that point at something already said rather than name it
_POINTS_BACK = frozenset(("the", "a", "an", "this", "that", "these", "those", "it", "them", "one", "ones", "other",
                          "another", "same", "thing", "previous", "last", "first", "second", "next", "earlier",
                          "before", "again", "instead", "please", "mate", "dude"))


def corrected_action(text: str, last_cmd: Optional[Dict[str, Any]],
                     now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """"no dude, ORBIT and Northwind" right after a search for the wrong thing: the last action again,
    on what the correction names ({"action", "args"}). The search is redone, where the reply used
    to be the model's guess about the new words ("Northwind is a band"). None unless the message is
    a correction naming something (not a question, not an instruction of its own) and the last
    action, recent, acted on one named thing (route_contracts.TARGET_ARGS)."""
    from eli.execution.route_contracts import TARGET_ARGS
    from eli.runtime.command_splitter import correction, starts_with_command
    named = correction(text)
    # "i said play the film on that site!", "no, open the other folder": the correction is an
    # instruction of its own, the one that was not carried out. It is read as the instruction it is,
    # whatever ran last: live it went to chat, and the model refused on its own.
    if named and "?" not in named and starts_with_command(named, after_lead=True):
        return _instruction_again(named)
    if not last_cmd:
        return None
    age = (time.time() if now is None else float(now)) - float(last_cmd.get("ts") or 0.0)
    if age > REDO_MAX_AGE_S:
        return None
    if not named or "?" in named or starts_with_command(named) or len(named.split()) > 12:
        return None
    if not [w for w in re.findall(r"[a-z0-9']+", named.lower()) if w not in _POINTS_BACK]:
        return None                     # "the other one" points back; it names nothing new
    args = dict(last_cmd.get("args") or {})
    key = next((k for k in TARGET_ARGS if isinstance(args.get(k), str) and args[k].strip()), None)
    if key is None:
        return None
    args[key] = named
    if key == "name" and isinstance(args.get("target"), str):
        args["target"] = named          # app actions carry the name twice
    return {"action": str(last_cmd.get("action") or ""), "args": args}


def _instruction_again(instruction: str) -> Optional[Dict[str, Any]]:
    """The instruction a correction repeats, as the router reads it ({"action", "args"}), or None when
    the router finds no action in it."""
    try:
        from eli.execution.router_enhanced import route
        routed = route(instruction) or {}
    except Exception:
        return None
    action = str(routed.get("action") or "").upper()
    if action in ("", "CHAT", "UNKNOWN", "NOOP"):
        return None
    return {"action": action, "args": dict(routed.get("args") or {}), "instruction": instruction}


# "Go deeper on <topic>" phrasings. When the previous turn was a news briefing,
# the captured topic is what the user wants re-fetched specifically — not the
# whole briefing again. Pure/deterministic so the engine can gate on it.
_DEEPEN_RE = re.compile(
    r"\b(?:"
    r"look(?:ing)?\s+(?:closer|deeper)?\s*(?:in)?to|"
    r"dig\s+(?:in)?to|delve\s+(?:in)?to|"
    r"go\s+deeper\s+(?:on|into)|"
    r"more\s+(?:on|about)|"
    r"tell\s+me\s+more\s+(?:on|about)|"
    r"expand\s+(?:on|about)|elaborate\s+on|"
    r"(?:read|look)\s+(?:closer|more)\s+(?:on|about|into)"
    r")\s+(.+)$",
    re.I,
)

# Trailing nouns that are framing, not part of the topic ("the Hubble story").
_DEEPEN_TAIL = re.compile(
    r"\s+(?:story|stories|article|articles|news|headline|headlines|situation|"
    r"thing|topic|piece|report|please|for\s+(?:me|us))\s*$",
    re.I,
)
_DEEPEN_STOP = {"the", "a", "an", "that", "this", "it", "them", "those", "these"}


def extract_deepen_topic(text: str) -> str:
    """Return the topic the user wants to go deeper on, or "".

    "look closer into Hubble" -> "Hubble"; "tell me more about the JWST story"
    -> "JWST". The caller gates use of this on context (e.g. the previous turn
    was a news briefing) so it never hijacks an unrelated "look into the bug".
    """
    s = str(text or "").strip()
    if not s:
        return ""
    m = _DEEPEN_RE.search(s)
    if not m:
        return ""
    topic = m.group(1).strip().strip("?.!,;:\"' ")
    # Strip a trailing framing noun, possibly more than one ("the Hubble news
    # story" -> "Hubble").
    prev = None
    while prev != topic:
        prev = topic
        topic = _DEEPEN_TAIL.sub("", topic).strip()
    toks = [w for w in re.split(r"\s+", topic) if w]
    while toks and toks[0].lower() in _DEEPEN_STOP:
        toks.pop(0)
    while toks and toks[-1].lower() in _DEEPEN_STOP:
        toks.pop()
    topic = " ".join(toks).strip()
    # Reject runaway captures (a whole frustrated sentence). A deepen topic can be a descriptive
    # phrase ("magnetic fields that help binary star systems form" is 8 words), so allow up to 12.
    # The old 6-word cap dropped real article topics and stopped topic-deepen for anything past a
    # one- or two-word subject.
    if not topic or len(toks) > 12:
        return ""
    return topic


# A promise that waits on the user or on a condition, or that says only what ELI could do, is an offer,
# not an act to carry out now: "if you want the listing, say 'show it' and I'll run it", "I'll check
# the forecast once you tell me the town", "I can help by checking the service". Read in the clause
# that holds the verb: a condition on the user anywhere in it; a condition or an instruction to the
# user ahead of the verb; a statement of what ELI can, could or would do ahead of it.
_WAITS_ON_THE_USER = re.compile(
    r"\b(?:if|once|when|whenever|after|as\s+soon\s+as|unless)\s+you(?:'?re|'?d|'?ve|'?ll)?\b"
    r"|\bshould\s+you\b|\bwould\s+you\s+like\b|\bwant\s+me\s+to\b", re.I)
_CONDITION_AHEAD = re.compile(
    r"^\W*(?:if|unless|in\s+case|once|whenever)\b"
    r"|\b(?:say|tell\s+me|let\s+me\s+know|ask(?:\s+me)?|give\s+me|send\s+me|type|confirm)\b[^.!?\n]*\band\b", re.I)
_CAPABILITY_AHEAD = re.compile(
    r"\b(?:i|we)\s+(?:can|could|would|might|may)\b|\b(?:i|we)'d\b|\b(?:happy|glad|able)\s+to\b", re.I)


def _boundary():
    """The sentence boundary the claim check reads replies by (self_claims._BOUNDARY), one copy."""
    try:
        from eli.cognition.self_claims import _BOUNDARY
        return _BOUNDARY
    except Exception:
        return re.compile(r"[.!?]+[\"'’”)\]*_]*\s+|\n")


def _clause_around(s: str, start: int, end: int) -> tuple:
    """(start, end) of the sentence that holds s[start:end]: from the last boundary before it (a
    '?' or '!' as well as a '.' or a line break) to the first after it."""
    boundary = _boundary()
    begin = 0
    for b in boundary.finditer(s, 0, start):
        begin = b.end()
    after = boundary.search(s, end)
    return begin, (after.start() if after else len(s))


def is_an_offer(clause: str, verb_start: int, verb_end: int) -> bool:
    """Whether the promise at clause[verb_start:verb_end] is an offer or a condition, not an act."""
    return bool(_WAITS_ON_THE_USER.search(clause)
                or _CONDITION_AHEAD.search(clause[:verb_end])
                or _CAPABILITY_AHEAD.search(clause[:verb_start]))


def detect_action_commitment(text: str) -> Optional[Dict[str, str]]:
    """Return {clause, matched} if `text` promises/fakes an action, else None.

    `clause` is the sentence holding the commitment — re-route it through the
    real pipeline so the router resolves the concrete task (e.g. "let me check
    the latest news" → NEWS_FETCH).
    """
    s = str(text or "").strip()
    if not s:
        return None
    _commit_re, _look_re, _doing_re = _commit_patterns()
    looked = _look_re.search(s) if _look_re is not None else None
    if looked and not _looks_at_something(s, looked.end()):
        looked = None
    m = _commit_re.search(s) or looked or _doing_re.search(s) or _FAKE_THEATRE_RE.search(s)
    if not m:
        return None
    # Past or perfect-continuous narration ("I've been checking", "I was searching", "I checked
    # earlier") isn't a commitment to act now, and re-running it buries the user in an action they
    # never asked for. Neither is a RETROSPECTIVE / NEGATED mention of the verb — ELI describing what
    # it did NOT do ("I defaulted to the old pattern INSTEAD OF verifying the slot", "I should have
    # verified", "I failed to check"). Live: "…instead of verifying the current bi-weekly
    # slot" fired a follow-through whose query was that whole 200-char sentence, which the web lookup
    # searched verbatim and got junk. Only a forward commitment ("let me check", "I'll fetch",
    # "checking now") triggers followthrough.
    start, end = _clause_around(s, m.start(), m.end())
    _pre = s[max(start, m.start() - 40):m.start()].lower()     # in this sentence, not the one before
    if re.search(r"\b(?:been|was|were|have\s+been|had\s+been|i'?ve\s+been|"
                 r"earlier|already|recently|just\s+(?:checked|finished)|"
                 r"instead\s+of|rather\s+than|without|should(?:'?ve|\s+have)|"
                 r"could(?:'?ve|\s+have)|failed\s+to|forgot\s+to|neglected\s+to|"
                 r"did\s*n[o']?t|did\s+not|never)\b", _pre):
        return None
    if is_an_offer(s[start:end], m.start() - start, m.end() - start):
        return None
    clause = s[start:end].strip(" .!?\n") or s
    return {"clause": clause, "matched": m.group(0).strip()}
