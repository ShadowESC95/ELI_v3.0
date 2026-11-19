from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional


def _low(text: Any) -> str:
    try:
        return str(text or "").lower()
    except Exception:
        return ""


def wants_memory_internals(text: Any) -> bool:
    low = _low(text)
    return (
        "memory" in low
        and (
            "internally" in low
            or "internal" in low
            or "internals" in low
            or "memory system" in low
            or "db table" in low
            or "db tables" in low
            or "sqlite" in low
            or "which files" in low
            or "which db" in low
            or "which functions" in low
            or "files, which db tables" in low
            or "tables, which functions" in low
        )
    )


def wants_personal_memory(text: Any) -> bool:
    low = _low(text)
    return (
        "memory" in low
        and (
            "about me" in low
            or "remember about me" in low
            or "actually remember about me" in low
            or "what you remember" in low
            or "what do you know about me" in low
            or "my memory" in low
            or "personal memory" in low
            or "personalised" in low
            or "personalized" in low
        )
    )


def wants_personal_memory_deep_explain(text: Any) -> bool:
    if wants_memory_internals(text) and wants_personal_memory(text):
        return True
    return wants_user_model_provenance(text)


# What ELI holds about the user, said as the user says it: "my profile", "your understanding of me",
# "what you know about me". A profile, a template or an understanding of anything else is not it:
# "a template for a linkedin profile", "the aerodynamic profile of a wing" and "how humans evolved
# their understanding of fire" were answered with the user's own profile.
_USER_MODEL_SUBJECT = re.compile(
    r"\babout me\b|\bwho i am\b|\b(?:understanding|picture|model|view|knowledge|memory|memories|profile|image|idea)"
    r"\s+of me\b|\bwhat you (?:know|remember|think|have|hold|store)\b[^.?!]{0,30}\babout me\b"
    r"|\bmy\s+(?:(?:user|personal)\s+)?(?:profile|preferences?|info|information|details|persona|user model)\b"
    r"|\buser\s+(?:info|information|profile|model)\b", re.I)
_STATIC_MARKER = re.compile(r"\b(?:hard[\s-]?coded|templates?|templated|stubs?|static|canned|fixed text)\b", re.I)
_DYNAMIC_MARKER = re.compile(
    r"\b(?:dynamic(?:ally)?|emergent|evolv(?:e|es|ed|ing)|update[sd]? these preferences?|actually remember"
    r"|not updating)\b", re.I)


def wants_user_model_provenance(text: Any) -> bool:
    """Static template vs dynamic/evolving understanding of the user: whether what ELI holds about
    the user is fixed text or learned. Whole words only ("aerodynamic" is not "dynamic")."""
    low = _low(text)
    if not low or not _USER_MODEL_SUBJECT.search(low):
        return False
    return bool(_STATIC_MARKER.search(low) or _DYNAMIC_MARKER.search(low))


def personal_memory_deep_route(text: Any) -> Dict[str, Any]:
    return {
        "action": "PERSONAL_MEMORY_DEEP_EXPLAIN",
        "args": {"question": str(text or ""), "detail": "full"},
        "confidence": 0.995,
        "meta": {
            "matched_by": "eli.route_contracts.personal_memory_deep_explain",
            "need_grounding": True,
            "allow_chat_without_evidence": False,
            "task_family": "personal_memory",
            "response_contract": "personal_memory_deep_explain_with_runtime_and_user_memory",
        },
    }


def classify_precedence_route(text: Any) -> Optional[Dict[str, Any]]:
    """
    Canonical precedence contract.

    Hybrid question:
      memory internals + what you remember about me
      -> PERSONAL_MEMORY_DEEP_EXPLAIN

    Pure memory internals remains EXPLAIN_MEMORY_RUNTIME elsewhere.

    Pure personal memory remains PERSONAL_MEMORY_SUMMARY elsewhere.

    This module prevents later wrappers from re-implementing contradictory
    phrase tests.
    """
    if wants_personal_memory_deep_explain(text):
        return personal_memory_deep_route(text)
    return None


__all__ = [
    "wants_memory_internals",
    "wants_personal_memory",
    "wants_personal_memory_deep_explain",
    "wants_user_model_provenance",
    "personal_memory_deep_route",
    "classify_precedence_route",
]


# The arguments that name one thing a command acts on: an app, a site, a file, a search, a song. A
# text argument (a note, a message) may hold several sentences; these may not.
TARGET_ARGS = ("name", "target", "app", "url", "path", "query", "title", "song", "artist", "device")


def is_command(intent: Any) -> bool:
    """Whether the router's answer for a message is an action to carry out: a rule matched
    (not the chat fallback) and what it names is neither conversation nor nothing."""
    if not isinstance(intent, dict):
        return False
    action = str(intent.get("action") or "").upper()
    matched = str((intent.get("meta") or {}).get("matched_by") or "")
    try:
        confident = float(intent.get("confidence") or 0) > 0.5
    except (TypeError, ValueError):
        confident = False
    return confident and action not in ("", "CHAT", "NOOP") and matched != "fallback.chat"


# ── what kind of thing an action is, by its name ─────────────────────────────
#
# An action either changes something (plays, opens, closes, writes, runs) or reads something and
# reports it. The name says which: a reading action starts with a verb of reading or ends with
# what it reports. Kept by convention, so a new action is classed by how it is named; the few
# names the convention cannot tell are listed. (Five hand-kept sets across the engine each held
# part of this and had drifted from one another.)
_READS_START = ("GET_", "LIST_", "CHECK_", "EXPLAIN_", "SHOW_", "READ_", "ANALYZE_", "ANALYSE_", "SUMMARIZE_",
                "SUMMARISE_", "SEARCH_", "EXAMINE_", "RESOLVE_", "DIAGNOSE_", "OCR_", "TRANSCRIBE")
_READS_END = ("_STATUS", "_STATS", "_USAGE", "_AUDIT", "_REPORT", "_SUMMARY", "_LOG", "_DIAGNOSTICS", "_DIAG",
              "_EXPLAIN", "_LIST", "_TOOLS", "_DOCTOR", "_SEARCH", "_QUERY", "_GRAPH", "_PROFILE", "_FETCH",
              "_RECALL", "_LOCATE", "_PLAYING", "_CHANGES", "_PROPOSALS", "_JOBS", "_PLAYBOOK", "_READ_ANALYZE")
_READS_NAMED = frozenset(("", "CHAT", "ANSWER", "NOOP", "UNKNOWN", "HELP", "TIME", "DATE", "CAPABILITY",
                          "DIRECT_RESPONSE", "ASK_CLARIFY", "SELF_ANALYZE", "SELF_TEST", "MESSAGE_TIME_QUERY"))


def changes_something(action: Any) -> bool:
    """Whether carrying this action out changes anything: the machine, an app, a file, what is
    playing, what ELI is set to. Reading and reporting does not."""
    name = str(action or "").strip().upper()
    return not (name in _READS_NAMED or name.startswith(_READS_START) or name.endswith(_READS_END))


# Reading actions that do work to answer, rather than read what is kept: an examination, an audit, an
# analysis, a diagnosis. By name, as above.
_WORKS_START = ("EXAMINE_", "ANALYZE_", "ANALYSE_", "DIAGNOSE_", "AUTOPILOT_", "SUMMARIZE_", "SUMMARISE_", "OCR_",
                "TRANSCRIBE")
_WORKS_END = ("_AUDIT", "_DOCTOR", "_DIAGNOSTICS", "_DIAG", "_READ_ANALYZE", "_GRAPH")


def does_work(action: Any) -> bool:
    """Whether carrying a reading action out does work to find its answer (scans, lints, runs,
    analyses) rather than reading a state ELI keeps."""
    name = str(action or "").strip().upper()
    return name.startswith(_WORKS_START) or name.endswith(_WORKS_END)


# Words that carry grammar and not content: what is left of a sentence without them and without
# the command's own words is what else the sentence is about.
_GRAMMAR = frozenset("""a an the this that these those my your his her its our their me you u i it he she we they them us
to of in on at by for with from into onto over under about as and or but if then so because while when where which who
whom whose what why how not no nor do does did done doing is are was were be been being am have has had having will
would shall should can could may might must just only also very really quite too more most much many some any each
every all both either neither own same such than now here there again ever never always still yet already please pls
thanks thank ok okay hey hi hello eli up down out off bit little moment second sec minute quick quickly""".split())
_WORD = re.compile(r"[a-z0-9']+")


def _words(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for inner in value.values():
            yield from _words(inner)
    elif isinstance(value, (list, tuple, set)):
        for inner in value:
            yield from _words(inner)
    elif value is not None and not isinstance(value, bool):
        yield from _WORD.findall(str(value).lower())


def _same_word(a: str, b: str) -> bool:
    """One word in two forms: play/playing, stop/stopped, minimise/minimize."""
    a, b = a.replace("z", "s"), b.replace("z", "s")
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short[:max(3, len(short) - 1)]) and len(long_) - len(short) <= 4


_NEEDS_A_TIME = frozenset(("SET_ALARM", "SET_TIMER", "START_TIMER", "POMODORO"))
_NAMES_A_TIME = re.compile(
    r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|half|quarter|an?\s+(?:hour|minute)|"
    r"hours?|minutes?|mins?|seconds?|tomorrow|tonight|morning|noon|midnight|evening)\b", re.I)


def has_what_it_needs(text: Any, intent: Any) -> bool:
    """Whether the model's choice of action has anything to act on. Chosen for "clock off", an alarm
    had no time, and the person was told "No duration or time specified" for something never asked."""
    action = str((intent or {}).get("action") or "") if isinstance(intent, dict) else ""
    return action not in _NEEDS_A_TIME or bool(_NAMES_A_TIME.search(str(text or "")))


def fits(text: Any, intent: Any) -> bool:
    """Whether a message is telling ELI to do what the router matched in it.

    Asked of actions that change something, which are carried out at once. A rule that fires on a
    word matched them inside sentences that were about something else: a complaint stopped a film,
    and a sentence of ELI's own explanation was run as a command. Two tests of form:

      * a sentence of the message is an instruction (command_splitter.instruction), not a
        statement or a question about what happened;
      * beyond the command's own words (its verb, its action, what it captured) the message says
        little: an instruction is about its action. Long text an action captured as its argument
        (a search, a title, a note) is the command's own words. (A rule that passes the whole
        message along as an argument is therefore held to the first test only.)

    A reading action changes nothing, so it only has to be asked for: "i know what day it is" holds
    the words of a date question, and answering it with the date is not a reply.

    Some actions are asked for in a remark by nature: a game is given up with "I give up", and
    proposed with "let's play". A rule that knows that says so (meta "spoken_as_remark") and its
    own vocabulary is the whole test."""
    from eli.runtime.command_splitter import (instruction, says_rather_than_asks, sentences, starts_with_command,
                                              tells_by_form)
    if not isinstance(intent, dict):
        return True
    if (intent.get("meta") or {}).get("spoken_as_remark"):
        return True
    if not changes_something(intent.get("action")):
        return not (str(intent.get("action") or "") not in ("", "CHAT") and says_rather_than_asks(str(text or "")))
    own = set(_words(intent.get("args"))) | set(_words((intent.get("meta") or {}).get("entities")))
    own |= set(str(intent.get("action") or "").lower().split("_"))
    said, told_something = [], False
    every = sentences(str(text or ""))
    named = set(str(intent.get("action") or "").lower().split("_"))
    for sentence in every:
        told = instruction(sentence)
        words = [w for w in _WORD.findall((told or sentence).lower()) if w not in _GRAMMAR]
        # Beside other sentences an instruction is one by its form, or opens with the action's own
        # name ("note: ...", "timer 5 minutes"): a statement there is material, not an order.
        if told and len(every) > 1 and not (tells_by_form(sentence)
                                             or words[:1] and any(_same_word(words[0], n) for n in named)):
            told = ""
        if told and (words or starts_with_command(told)):   # "well done." tells nothing; "do it" does
            told_something = True
            own.update(words[:1])                  # the verb the instruction starts with
        said += words
    if not told_something:
        return False
    spare = [w for w in said if w not in own and not any(_same_word(w, o) for o in own)]
    return len(spare) < 6 or len(spare) <= len(said) - len(spare)


# ── who a message is about, and what it asks ─────────────────────────────────
#
# A report on ELI (its runtime, failures, code, tests, models, agenda) answers a question about ELI.
# Rules that fired on a word matched their words in questions about the world: "what is heart
# failure?" got the executor's failure log, "my wifi router is broken" a codebase audit, "why did the
# plane crash?" a debugging plan for ELI's own code. A rule for such a report that matches a common
# English word ("router", "failure", "fail", "pipeline", "suggestions") asks `names_eli` as well.

# Second person, ELI by name, and ELI's work on itself ("self-update", "the self-improvement log").
_ADDRESSED = re.compile(r"\b(?:you|your|yours|yourself|eli|eli's|eli’s)\b"
                        r"|\bself[- ]?(?:updat|improv|test|patch|repair|upgrad|fix|heal|maintenan|analy[sz]|report)\w*", re.I)
# Who is asked, not what it is about: in "can you fix the errors in this paragraph" and "do you know
# why startups fail" the "you" is the one asked. The frame is passed over before looking for ELI.
_ASKING_FRAME = re.compile(
    r"\b(?:can|could|would|will|won'?t)\s+you\b(?:\s+(?:please|kindly|just|maybe|possibly|now|also|quickly))*"
    r"|\b(?:do|did|does|would|could|can)\s+you\s+(?:(?:please|just|actually|really|even|ever|happen\s+to)\s+)*"
    r"(?:think|know|reckon|believe|suppose|guess|feel|mind)\b"
    r"|\bi(?:'d|\s+would)\s+(?:like|love)\s+(?:you|for\s+you)\s+to\b|\bi\s+(?:want|need)\s+you\s+to\b"
    r"|\b(?:thank|thanks)\s+you\b|\bare\s+you\s+able\s+to\b",
    re.I)
_SOURCE_PATH = re.compile(r"(?:^|[\s\"'(\[])(?:\./)?eli/[\w./-]+|\b([\w-]+)\.py\b", re.I)
_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", re.I)
_ELI_SOURCE_NAMES: Optional[frozenset] = None


def _eli_source_names() -> frozenset:
    """The names of ELI's own source files, without ".py"."""
    global _ELI_SOURCE_NAMES
    if _ELI_SOURCE_NAMES is None:
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[1]
            _ELI_SOURCE_NAMES = frozenset(p.stem.lower() for p in root.rglob("*.py") if p.stem != "__init__")
        except Exception:
            _ELI_SOURCE_NAMES = frozenset()
    return _ELI_SOURCE_NAMES


def names_eli(text: Any) -> bool:
    """Whether a message names ELI as what it is about: "your", "you" (past the frame that asks
    something of ELI: "can you", "do you know"), "ELI", or one of ELI's own source files
    ("router_enhanced", "eli/core/paths.py", "executor_enhanced.py")."""
    said = _ASKING_FRAME.sub(" ", str(text or ""))
    if _ADDRESSED.search(said):
        return True
    names = _eli_source_names()
    for m in _SOURCE_PATH.finditer(said):
        if m.group(1) is None or m.group(1).lower() in names:
            return True
    return any(m.group(0).lower() in names for m in _IDENTIFIER.finditer(said))


def asks_about_eli(text: Any) -> bool:
    """Whether a question is about ELI itself, for the rules of ELI's diagnostic reports: ELI is named
    in it (`names_eli`), or it is about ELI's own doing (self_claims.about_eli, which also reads what
    ELI just did). "how many memories can a human brain hold" and "which model has the biggest
    context window" are about the world, and got ELI's memory count and runtime report."""
    if names_eli(text):
        return True
    try:
        from eli.cognition import self_claims
        reader = getattr(self_claims, "about_eli", None)
        return bool(reader(text)) if callable(reader) else False
    except (ImportError, OSError, ValueError, TypeError, AttributeError):
        return False


# A yes/no question about what was done: "did you inspect any of those files?", "have you run the
# tests?". The record answers it; it does not ask for the thing to be done. A rule that matched
# "inspect ... files" in it started a lint of ELI's own core. With "you" the inverted auxiliary is a
# question as said aloud too; with any other subject ("were the tests run") it needs its "?".
_DONE_YOU = re.compile(
    r"^\s*(?:(?:so|and|but|wait|well|ok|okay|hey|eli|hold on|sorry)[,\s]+)*"
    r"(?:did|didn'?t|have|haven'?t|has|hasn'?t|had|hadn'?t|were|weren'?t|was|wasn'?t)\s+(?:you|eli)\b(?!\s+(?:to|got\s+to)\b)",
    re.I)
_DONE_OTHER = re.compile(
    r"^\s*(?:(?:so|and|but|wait|well|ok|okay|hey|eli|hold on|sorry)[,\s]+)*"
    r"(?:did|didn'?t|has|hasn'?t|had|hadn'?t|were|weren'?t|was|wasn'?t)\s+(?:it|that|this|they|those|these|the\b)",
    re.I)


def asks_what_was_done(text: Any) -> bool:
    """Whether a message only asks whether something was done, every sentence of it."""
    from eli.runtime.command_splitter import sentences
    parts = sentences(str(text or ""))
    return bool(parts) and all(
        _DONE_YOU.match(part) or (_DONE_OTHER.match(part) and part.rstrip(" )\"'’”").endswith("?"))
        for part in parts)


# The user's own name as what is asked about ("how do you know my name", "where is my name stored",
# "what you call me"), in words: "named", "username" and "filename" are not it.
_USERS_NAME = re.compile(
    r"\bmy\s+(?:(?:first|full|last|real|preferred|user)\s+)?name\b|\bwhat\s+(?:you\s+)?call\s+me\b"
    r"|\bwho\s+i\s+am\b|\bthe\s+user'?s\s+name\b", re.I)
_NAME_SOURCE = re.compile(
    r"\bhow\s+(?:do|did|does)\s+you\s+know\b|\bwhere\b[^.?!]*\b(?:files?|located|stored|saved|kept|comes?\s+from|"
    r"get|got|learn\w*|find|found|read)\b|\bwhich\s+(?:files?|database|db|table|store)\b|\bwhere\s+(?:does|did)\b",
    re.I)


def asks_where_the_name_comes_from(text: Any) -> bool:
    """Whether a message asks where ELI's knowledge of the user's name comes from."""
    said = str(text or "")
    return bool(_USERS_NAME.search(said) and _NAME_SOURCE.search(said))


# A question about who the user is, asked of ELI, read in one place: the router's identity stage
# answers it with this (router_enhanced._eli_phase38_identity_contract), and the scope its answer
# keeps to comes from here too. It was read by eight copies of these words in the router, the
# intent stages and the executor, with four different actions, and which one answered depended on
# their order. What ELI holds about the user ("what do you know about me", "my preferences") is the
# personal memory summary's question, read by the personal memory contract, not this one.
_ASKS_MY_NAME = re.compile(r"\b(?:what is my name|what'?s my name|do you know my name)\b")
_ASKS_MY_NAME_ALONE = re.compile(r"\s*(?:and|but|so|ok|okay|well|hmm?)?[,\s]*(?:what(?:'s| is| about)?\s+)?my name\s*\??\s*")
_ASKS_WHO_I_AM = re.compile(
    r"\b(?:who am i|do you know who i am|you do not know who i am|you don'?t know who i am|don'?t you know who i am"
    r"|you have no idea who i am|what do you remember of me|my persona|my ethos)\b")
_ASKS_IF_KNOWN = re.compile(r"\b(?:do you know me|do you remember me|don'?t you know me|you don'?t know me)\b")
_ASKS_FOR_THE_REPORT = re.compile(
    r"\b(?:show user info|user info report|profile report|dump (?:my )?(?:user )?(?:profile|info|memory)"
    r"|show (?:my|the) stored memor(?:y|ies)(?: about me)?|what do you know from memory)\b")
# a request to make the report again is the refresh's (REFRESH_USER_INFO), not a request to read it
_REMAKE = re.compile(r"\b(?:refresh|rebuild|update|regenerate|redo)\b")


def identity_scope(text: Any) -> str:
    """Which part of who the user is a question asks for: "name_only", "memory_presence_only" (whether
    ELI knows them at all) or "identity_only"."""
    low = re.sub(r"\s+", " ", _low(text)).strip(" .,!?:;")
    if _ASKS_MY_NAME.search(low) or _ASKS_MY_NAME_ALONE.fullmatch(low):
        return "name_only"
    if re.search(r"\b(?:who am i|who i am)\b", low):
        return "identity_only"
    if _ASKS_IF_KNOWN.search(low):
        return "memory_presence_only"
    return "identity_only"


def identity_question(text: Any) -> Optional[Dict[str, Any]]:
    """{"action", "args", "matched_by"} for a question about who the user is, else None: the report
    of what ELI keeps on them ("show user info", "dump my profile") is USER_INFO_REPORT; who they are,
    their name, or whether ELI knows them is USER_IDENTITY_SUMMARY with its identity_scope."""
    low = re.sub(r"\s+", " ", _low(text)).strip(" .,!?:;")     # as identity_scope reads it: "And my name."
    if _ASKS_FOR_THE_REPORT.search(low):
        if _REMAKE.search(low):
            return None
        return {"action": "USER_INFO_REPORT", "args": {"reason": "identity_query"},
                "matched_by": "identity.user_info.report"}
    if (_ASKS_MY_NAME.search(low) or _ASKS_MY_NAME_ALONE.fullmatch(low) or _ASKS_WHO_I_AM.search(low)
            or _ASKS_IF_KNOWN.search(low)):
        return {"action": "USER_IDENTITY_SUMMARY", "args": {"question": str(text or ""),
                                                            "identity_scope": identity_scope(text)},
                "matched_by": "identity.final_user_summary"}
    return None


__all__ += ["TARGET_ARGS", "is_command", "changes_something", "has_what_it_needs", "fits",
            "does_work", "names_eli", "asks_about_eli", "asks_what_was_done", "asks_where_the_name_comes_from",
            "identity_question", "identity_scope"]
