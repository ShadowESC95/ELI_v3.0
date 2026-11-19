"""What ELI may say about its own workings, and the record it says it from.

Routing, retrieval and escalation run before the model is called and never reach it, so
asked "why did you search the web?" a model can only invent: live it produced "vector search
drift", "the embedding model decayed the context window", a `verbose_media_logging` setting
and an `action_logs` table, none of which exist, and "Consider it done. The audit trail is now
active" for logging that was always on. Two things fix that at the source:

  * the turn record: each turn's audit row says what the pipeline did (which rule routed it,
    what ran, whether it escalated to the web and why, what retrieval found). Asked about its
    own behaviour, ELI is handed those rows and explains from them.
  * the claim check: a sentence that names a setting, table or module ELI does not have, or
    reports switching something on when nothing ran, is dropped, in streamed replies too.
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Callable, Any, Dict, Iterable, List, Optional

from eli.cognition.query_planner import days_named
from eli.utils.log import get_logger

log = get_logger(__name__)

# ── is the user asking about ELI's own behaviour? ────────────────────────────

# Asking what just happened: "what the fuck just happened?", "what happened there?". The last
# turn's row in the ledger is the answer. Live, the first went to the model's guess at an action
# and came back as a dump of confidence figures.
WHAT_JUST_HAPPENED = (r"\bwhat\s+(?:the\s+\w+\s+|on\s+earth\s+)?(?:has\s+)?just\s+happened\b"
                      r"|\bwhat\s+(?:the\s+\w+|on\s+earth)\s+happened\b"
                      r"|\bwhat\s+happened\s+(?:there|just\s+now|then|just\s+then)\b")

# Every auxiliary a "why ... you" question can turn on: "why could you not tell me that the first
# time?" asks about ELI as much as "why didn't you".
_AUX = (r"(?:did|didn'?t|do|don'?t|does|doesn'?t|are|aren'?t|were|weren'?t|is|isn'?t|was|wasn'?t|have|haven'?t|has|"
        r"hasn'?t|had|keep|kept|can|can'?t|cannot|could|could ?n'?t|would|would ?n'?t|will|won'?t|should|should ?n'?t)")

_OWN_BEHAVIOUR_RE = re.compile(
    r"\bwhy\b[^.?!]{0,40}\b" + _AUX + r"\b[^.?!]{0,12}\byou(?:r)?\b"
    # how long ELI took is ELI's behaviour: "took you nearly 2 minutes to figure that out?"
    r"|\b(?:took|takes|taking|taken)\s+you\b"
    r"|\bwhat(?:'?s| is| was| has been| went)?\s+(?:going on|wrong|happening|happened)\b[^.?!]{0,30}\b(?:with\s+)?you(?:r)?\b"
    r"|\bwhat\s+(?:went|is going|has been going)\s+wrong\b"
    r"|\bwhat(?:'?s| is| are)\s+with\s+(?:all\s+)?(?:the|these|those|this|your)\s+(?:issues?|problems?|errors?|bugs?|mistakes?|failures?)\b"
    r"|\bwhat\s+(?:has|have)\s+been\s+going\s+on\b"
    r"|\bexplain\b[^.?!]{0,30}\b(?:yourself|your (?:mistake|error|answer|response|behaviou?r)|what (?:you did|happened)|why you)\b"
    r"|\bwhy\b[^.?!]{0,30}\b(?:search(?:ed|ing)? the web|web search|wrong|lying|lie[ds]?|making (?:shit|stuff|things) up|ignor\w+)\b"
    r"|\b(?:how|why)\s+(?:come|is it that)\s+you\b"
    r"|\bwhat did you (?:just )?do\b|\bwhat have you (?:been doing|done)\b"
    r"|" + WHAT_JUST_HAPPENED + r"|\b(?:do|are) you (?:log|logging|record|recording|keep(?:ing)? (?:a )?(?:log|record|track))\b"
    r"|\bwhat\s+(?:the\s+\w+\s+)?was\s+that\b|\bwhat(?:'?s| is)\s+with\s+(?:that|this|your)\b"
    r"|\bwhy\s+(?:tell|say|claim)\b|\bwhy\s+is\s+(?:it|this|that)\s+(?:not|still\s+not)\b"
    r"|\bwhat\s+did\s+you\s+(?:\w+\s+)?(?:search|look|find|say|run|read|check)\w*\b"
    r"|^\s*(?:(?:so|and|but|wait|well)[,\s]+)?are\s+you\s+(?:\w+\s+)?\w+ing\b(?!\s+(?:ok|okay|well|good|alright|fine)\b)"
    r"|\bdid\s+you\b(?!\s+know\b)|\byou\s+said\b"
    r"|\byour\s+(?:\w+\s+)?(?:success|failure|error|hit)\s+rate\b|\bhow\s+(?:accurate|reliable)\s+are\s+you\b"
    r"|\byour\s+(?:logs?|logging|audit|records?)\b|\blog(?:ging|s)?\s+(?:for|of)\s+every",
    re.I)


# A complaint aimed at ELI ("you keep ...", "you did not ...", "you just ...ed"): one reader,
# eli/cognition/correction_patterns, for every turn that is about ELI's last reply.
from eli.cognition.correction_patterns import _YOU_JUST_DID, complains_about_eli  # noqa: E402,F401


# Words that name nothing: a message made only of these ("what the hell is going on!?", "seriously??",
# "why?!", "huh?") has no subject of its own, so it is about ELI's last reply. Live, a bare reaction like
# that matched none of the patterns and the model invented a "RAG phase" and a "reflection agent".
_REACTION_WORDS = frozenset("""
a an the is was it its it's this that what what's whats why huh eh wtf fuck fucking fucked hell heck
going on happening happened wrong now again really seriously actually even still just so oh god jesus
christ come ok okay man dude bro mate sake for are you your me i am do does did of up with there here
""".split())


# ELI's name said to it: "what the fuck eli?", "eli, why did you do that". An address, not a subject.
_VOCATIVE = re.compile(r"^\W*(?:(?:hey|hi|yo|oi|ok|okay|so|and|but)\W+)?eli\b[\s,:;.!-]*|[\s,;-]+eli\s*(?=[?!.…]*\W*$)"
                       r"|,\s*eli\s*,", re.I)


def without_address(text: Any) -> str:
    """The message without ELI's name where it only calls ELI."""
    return _VOCATIVE.sub(lambda m: "" if m.start() == 0 else ("," if m.group(0).strip().endswith(",") else ""),
                         str(text or "")).strip()


def reacts_to_last_reply(text: Any) -> bool:
    """A short question or exclamation with no subject of its own: a reaction to what ELI just did."""
    low = without_address(text).lower()
    if not re.search(r"[?!]|\b(?:wtf|why|huh|seriously|really)\b", low):
        return False
    words = re.findall(r"[a-z']+", low)
    return 0 < len(words) <= 10 and all(w.strip("'") in _REACTION_WORDS for w in words)


def subject_words(text: Any) -> List[str]:
    """The words of a message that name something: not stopwords, and not the words a bare reaction
    is made of ("what", "the", "fuck", "this", "really")."""
    from eli.cognition.scoring import tokenize
    return [w for w in tokenize(str(text or "")) if w.strip("'") not in _REACTION_WORDS]


def asks_about_own_behaviour(text: Any) -> bool:
    """A question about what ELI did, why, or what it logs, a complaint about it, or a bare reaction
    to its last reply: the turns where it invents."""
    said = without_address(text)
    return (bool(_OWN_BEHAVIOUR_RE.search(said)) or complains_about_eli(said)
            or reacts_to_last_reply(said))


# ── the turn record ──────────────────────────────────────────────────────────

# What a routing rule means, for the ones a person is most likely to be told about.
_ROUTE_WORDS = {
    "chat.long_question_guard": "a long question, sent to chat",
    "fallback.chat": "no command rule matched, sent to chat",
    "llm_intent.resolver": "no command rule matched; the model chose the action",
    "phatic.fastpath": "a greeting, answered without retrieval",
    "identity.set_user_name": "the name-setting rule",
    "identity.final_user_summary": "the who-am-I rule",
    "memory.temporal_recall_as_chat": "a question about a period, sent to chat",
    "system.date": "the date rule",
    "system.time": "the time rule",
}


def describe_turn(facts: Dict[str, Any]) -> str:
    """One line on what the pipeline did this turn, from the facts the turn noted about itself.
    Goes into the audit row; nothing here is model output."""
    parts: List[str] = []
    via = str(facts.get("via") or "").strip()
    if via:
        parts.append(f"routed by {via}" + (f" ({_ROUTE_WORDS[via]})" if via in _ROUTE_WORDS else ""))
    if facts.get("downgraded_from"):
        parts.append(f"first read as {facts['downgraded_from']}, answered as chat (too little evidence for it)")
    for step in facts.get("steps") or []:
        words = " ".join(str(step.get("words") or "").split())
        parts.append(f"carried out “{words[:90]}{'…' if len(words) > 90 else ''}” as {step.get('action') or '?'}"
                     + (f" (matched by {step['via']})" if step.get("via") else ""))
    if facts.get("retrieval"):
        parts.append(f"retrieval: {facts['retrieval']}")
    if facts.get("escalation"):
        parts.append(str(facts["escalation"]))
    calls = int(facts.get("model_calls") or 0)
    if calls:
        secs = float(facts.get("model_seconds") or 0.0)
        parts.append(f"model ran {calls} time{'s' if calls != 1 else ''} on "
                     f"{int(facts.get('prompt_chars') or 0):,} prompt characters"
                     + (f", {secs:.0f} s of model time" if secs >= 1 else ""))
    return "; ".join(parts)


def _user_turns(mem: Any, since: float, user_id: str) -> List[Dict[str, Any]]:
    try:
        return list(mem.get_recent_conversation(limit=60, user_id=user_id or None, since=since, role="user") or [])
    except Exception:
        log.debug("turn record: user turns unavailable", exc_info=True)
        return []


def recent_turn_rows(mem: Any, *, user_id: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    """Recent turns as the pipeline recorded them, oldest first: the audit row (action, ok, outcome,
    ts, elapsed_ms, reasoning_mode) with `asked`, what the user said for it."""
    try:
        from eli.runtime import orchestrator_audit_ledger as oal
        oal.flush(1.0)
        rows = [r for r in oal.recent_turns(limit * 4)
                if not r.get("parent_request_id") and (not user_id or not r.get("user_id") or r["user_id"] == user_id)]
    except Exception:
        log.debug("turn record unavailable", exc_info=True)
        return []
    rows = list(reversed(rows[:limit]))
    if not rows:
        return []
    said = _user_turns(mem, float(rows[0]["ts"] or 0) - 900, user_id) if mem is not None else []
    out = []
    for r in rows:
        end = float(r.get("ts") or 0)
        start = end - float(r.get("elapsed_ms") or 0) / 1000.0 - 5
        asked = next((t for t in reversed(said) if start <= float(t.get("timestamp") or 0) <= end), None)
        out.append(dict(r, asked=" ".join(str((asked or {}).get("content") or "").split())))
    return out


def turn_record_lines(mem: Any, *, user_id: str = "", limit: int = 8, now: Optional[float] = None) -> List[str]:
    """Recent turns as the pipeline recorded them, oldest first, each with what was asked."""
    return format_turn_rows(recent_turn_rows(mem, user_id=user_id, limit=limit), now=now)


def format_turn_rows(rows: List[Dict[str, Any]], *, now: Optional[float] = None) -> List[str]:
    """recent_turn_rows as the lines the model and the claim check read."""
    from eli.cognition.evidence_format import turn_stamp
    lines = []
    for r in rows:
        words = r["asked"]
        quote = f" “{words[:90]}{'…' if len(words) > 90 else ''}”" if words else ""
        took = float(r.get("elapsed_ms") or 0) / 1000.0
        outcome = str(r.get("outcome") or "").strip()
        lines.append(f"[{turn_stamp(float(r.get('ts') or 0), now)}]{quote} -> {r.get('action') or '?'} "
                     f"({r.get('reasoning_mode') or 'quick'}, {took:.0f} s): {outcome or 'ok'}")
    return lines


# What ELI's last actions were about, for a message that refers to one of them without saying "you
# did" in so many words: "i seen you take a screenshot, so what is the actual issue?" right after a
# screen capture that failed was sent to the web, and the reply invented what had gone wrong.
_NOT_A_REFERENT = frozenset("""about again also back bit come else even ever from going got have here into just know
like look made make more much need only over really said same seen some still such than that them then there these
they thing things this those through time today very want well were what when where which while will with would
your yours actual actually issue issues problem problems wrong right first last next""".split())


def _words_of(text: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z0-9']{3,}", str(text or "").lower()) if w not in _NOT_A_REFERENT}


def _shares_a_word(a: Iterable[str], b: Iterable[str]) -> bool:
    from eli.execution.route_contracts import _same_word
    b = list(b)
    return any(_same_word(x, y) for x in a for y in b)


def _what_an_action_is_about(row: Dict[str, Any]) -> set:
    """The words of what a recorded turn did: its action's name and what the catalogue says it does,
    and any action it also carried out. Not the words of what it acted on: after "play harbour bells
    by orlo", "is orlo still touring?" asks about the band, not about ELI."""
    names = [str(row.get("action") or "")]
    names += re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", str(row.get("outcome") or ""))
    names = [n for n in dict.fromkeys(n.upper() for n in names) if n and n != "CHAT"]
    if not names:
        return set()
    try:
        from eli.tools.registry.capabilities_doc import action_words
        about = " ".join(action_words(n, with_phrases=False) for n in names)
    except ImportError:
        about = ""
    return _words_of(" ".join(n.replace("_", " ") for n in names) + " " + about)


def names_a_recent_action(text: Any, rows: List[Dict[str, Any]], *, now: Optional[float] = None) -> bool:
    """Whether a message names what an action ELI ran just before it was about ("these files" after a
    file was read, "a screenshot" after a screen read), request or question alike."""
    from eli.runtime.action_commitment import REDO_MAX_AGE_S
    at = time.time() if now is None else float(now)
    words = _words_of(without_address(text))
    return bool(words) and any(_shares_a_word(words, _what_an_action_is_about(r)) for r in rows
                               if at - float(r.get("ts") or 0) <= REDO_MAX_AGE_S)


def refers_to_recent_action(text: Any, rows: List[Dict[str, Any]], *, now: Optional[float] = None) -> bool:
    """Whether a message is about an action ELI ran just before it, read from the record: it names
    what that action was about, or it reacts to one that failed. A message that tells ELI to do
    something is a request, not a question about what it did ("take another screenshot")."""
    said = without_address(text)
    if not said or not rows:
        return False
    try:
        from eli.runtime.command_splitter import tells_eli_to_do
        if tells_eli_to_do(said):
            return False
    except ImportError:
        pass
    from eli.runtime.action_commitment import REDO_MAX_AGE_S
    at = time.time() if now is None else float(now)
    recent = [r for r in rows if at - float(r.get("ts") or 0) <= REDO_MAX_AGE_S]
    words = _words_of(said)
    for r in reversed(recent):
        about = _what_an_action_is_about(r)
        if about and _shares_a_word(words, about):
            return True
    last = recent[-1] if recent else None
    asks = bool(re.search(r"[?!]", said)) and bool(re.search(r"\b(?:you|your|yourself)\b", said, re.I))
    return bool(last) and not last.get("ok", True) and (asks or reacts_to_last_reply(said))


def about_eli(text: Any, *, mem: Any = None, user_id: str = "") -> bool:
    """Whether a message is about ELI's own behaviour: its wording says so (asks_about_own_behaviour),
    or what ELI just did does (refers_to_recent_action). Read once a turn: the answer is kept on the
    turn for every check that asks."""
    said = str(text or "")
    if asks_about_own_behaviour(said):
        return True
    try:
        from eli.kernel.request_context import turn_facts_var
        facts = turn_facts_var.get()
    except ImportError:
        facts = None
    if not (facts or {}).get("request_id"):
        return False             # no turn the pipeline is recording: no record to read
    kept = facts.get("_about_eli")
    if isinstance(kept, dict) and kept.get("text") == said:
        return bool(kept.get("is"))
    if mem is None:
        try:
            from eli.memory.memory import get_memory
            mem = get_memory()
        except Exception:
            mem = None
    rows = recent_turn_rows(mem, user_id=user_id, limit=8)
    answer = refers_to_recent_action(said, rows)
    if facts is not None:
        facts["_about_eli"] = {"text": said, "is": answer, "rows": rows}
    return answer


def record_block(lines: List[str]) -> str:
    """The record with the instruction to explain from it and from nothing else."""
    if not lines:
        return ""
    return ("What you did on recent turns, written by the pipeline into the audit ledger (not by you). "
            "Asked why you did something or what went wrong, explain from these rows and the period log "
            "only. Where a row does not show the cause, say the record does not show it; never name a "
            "mechanism, setting or component that is not written here.\n" + "\n".join(lines))


# ── the claim check ──────────────────────────────────────────────────────────

# Reporting a switch thrown, or promising a standing change, from inside a reply.
_ENABLED_CLAIM = re.compile(
    r"\bconsider it done\b"
    r"|\b(?:logging|audit(?:ing| trail)?|tracking|monitoring|recording|verbose\w*|filter\w*|threshold\w*|"
    r"setting\w*|config\w*|mode|index\w*|pipeline|memory|retrieval|gating)\b[^.?!\n]{0,60}?\b(?:is|are|has been|have been)\s+"
    r"now\s+(?:active|enabled|on|in place|live|persistent|fixed|updated|tightened|enforced)\b"
    r"|\bi(?:'ve|’ve| have)\s+(?:now\s+|just\s+)?(?:enabled|activated|switched on|turned on|implemented|tightened|"
    r"recalibrated|reconfigured|adjusted the|updated (?:my|the)|patched|flagged (?:this|that|it) for)\b"
    r"|\bi(?:'ll|’ll| will)\s+(?:enforce|ensure|log|record|verify|store|track|audit)\b[^.?!\n]{0,80}?"
    r"\b(?:going forward|from now on|every time|in future|for every|each time)\b"
    r"|\bi\s+can\s+(?:implement|enable|activate)\s+(?:this|that|it)\s+(?:immediately|now|right away)\b"
    r"|\bdo you want me to\s+(?:enable|activate|switch on|turn on)\b",
    re.I)

# Saying it has done, or is doing, something on the machine. Checked on a turn in which nothing
# was carried out: asked to open Spotify inside a longer sentence, the model alone answered "Spotify
# is opening now." and then "Done. Playing ...", and given folders to read it wrote "Scanning ...
# (Waiting for system execution output...)" with nothing running. The verbs are the ones a command
# starts with (runtime.command_splitter), in the forms a report uses, without the ones below.

# Work a reply does in its own words: "I've summarised it below" and "Listing the options:" are the
# reply doing what it says. ("Checking ..." and "searching ..." with nothing run are
# runtime.action_commitment's: it carries the check out.)
_DONE_IN_WORDS = frozenset("""add analyse analyze build call cancel check clear confirm convert copy create decline
decrease describe design diagnose dictate do examine explain find fix forget generate get give help import improve
increase list listen look lower make move next paste prepare previous raise read remember remove repeat resolve
review say select set show speak summarise summarize take tell test train transcribe turn type update volume
write""".split())


# The verbs of the work a report of work in progress uses, beyond the ones commands start with.
_WORK_VERBS = frozenset(("scan", "load", "wait", "queue", "index", "ingest", "restart", "trigger", "initiate", "initialise",
                         "initialize", "process", "send", "start", "batch", "draft", "patch", "pull", "inspect", "parse"))


@lru_cache(maxsize=1)
def act_verbs() -> frozenset:
    """The verbs of the acts ELI can be said to be doing: the ones a command starts with
    (runtime.command_splitter), every word of an action's name that is one of those (READ_FILE,
    SCREEN_READ_ANALYZE, EXAMINE_CODE), and the words of work in progress. One list, for the claim
    check here and for runtime.action_commitment's promises."""
    from eli.runtime.command_splitter import COMMAND_VERBS
    verbs = set(COMMAND_VERBS) | set(_WORK_VERBS)
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        verbs |= {w for a in SUPPORTED_ACTIONS for w in str(a).lower().split("_") if w in verbs}
    except Exception:
        log.debug("act verbs: actions unavailable", exc_info=True)
    return frozenset(v for v in verbs if len(v) > 1)


@lru_cache(maxsize=1)
def read_verbs() -> frozenset:
    """The act verbs whose actions only look at something: "read", "list", "examine", "analyse",
    "scan". From the catalogue: a verb is one when an action named with it changes nothing
    (execution.route_contracts.changes_something)."""
    verbs = act_verbs()
    found = set(_WORK_VERBS & {"scan", "inspect", "parse", "index"})
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        from eli.execution.route_contracts import changes_something
        for a in SUPPORTED_ACTIONS:
            words = str(a).lower().split("_")
            if not changes_something(str(a)):
                found |= {w for w in words if w in verbs}
    except Exception:
        log.debug("read verbs: actions unavailable", exc_info=True)
    return frozenset(found)


@lru_cache(maxsize=1)
def machine_things() -> frozenset:
    """What ELI's actions act on, by the words of their names (FILE, DIR, CODE, SCREEN, IMAGE, MEMORY,
    NOTES...), with the everyday words for the same things and ELI's own records. "I'm reading the
    source code" claims an act on one; "I'm reading your message" does not."""
    things = {"folder", "folders", "directory", "directories", "source", "codebase", "repo", "repository", "disk", "drive",
              "path", "paths", "log", "logs", "ledger", "record", "records", "database", "table", "tables", "document",
              "documents", "screenshot", "script", "scripts", "module", "modules", "package", "packages"}
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        verbs = act_verbs()
        for a in SUPPORTED_ACTIONS:
            for w in str(a).lower().split("_"):
                if len(w) > 2 and w not in verbs:
                    things |= {w, w + "s"}
    except Exception:
        log.debug("machine things: actions unavailable", exc_info=True)
    return frozenset(things)


def _doing_forms() -> str:
    verbs = set(act_verbs()) - _DONE_IN_WORDS
    stems = sorted({v[:-1] if v.endswith("e") else v for v in verbs if len(v) > 2} | {"sent", "ran"},
                   key=len, reverse=True)
    return "(?:" + "|".join(stems) + r")\w{0,2}(?:ed|ing|d)?"


_DOING = _doing_forms()
_SAYS_NOTHING_IS = re.compile(r"\s*(?:nothing|none|no\s+\w+)\b", re.I)
_REPORTS_ACTING = re.compile(
    # "I've opened ...", "I'm scanning ...", "I just ran ...". Not "I opened ..." or "I successfully
    # sent ...": the plain past tells of an earlier turn, and that account is held to the record.
    r"\bi(?:'ve|’ve| have|'m|’m| am| just)\s+(?:(?:now|just|already|successfully|currently)\s+)*"
    + _DOING + r"\b"
    # "Spotify is opening now", "the scan is running"
    r"|\b[\w`'\"&.-]+(?:\s+[\w`'\"&.-]+){0,3}\s+(?:is|are)\s+(?:now\s+|currently\s+)?"
    r"(?:open|launch|start|play|load|run|download|install|scan|execut|search|fetch|pull|process|index|analys|analyz)"
    r"\w{0,2}ing\b"
    # "The three directories are now queued", "as we speak", "Validation update in progress", "Logic patch drafted"
    r"|\b(?:is|are)\s+(?:now\s+)?queued\b|\bas\s+we\s+speak\b"
    r"|\b(?:update|patch|fix|job|task|analysis|scan|simulation)\s+(?:is\s+)?(?:in\s+progress|underway|drafted|applied|deployed)\b",
    re.I)
# "Done." on its own. It is a report when what follows it is one ("Done. Playing ...") or when
# nothing follows; before something the reply made in words ("Done. Here is the email:") it is not.
_DONE_LEAD = re.compile(r"^[\s*_>(\[-]*(?:done|all done|sorted|there you go)\s*[.!]*[\s*_)\]]*$", re.I)
# "Scanning `/home/...` and `/home/...`.", "Playing "X" by Y, since ...", "Executing directory
# scans...", "(Waiting for system execution output...)": the act named with no one doing it. What
# tells it from a sentence about the act ("Enabling `x` adds nothing", "Playing music helps") is
# that no verb follows: the thing it points at is followed by the end, a comma or a joining word,
# or the sentence is short and trails off.
_ACTING_NOW = re.compile(r"^[\s*_>(\[-]*(?:" + _DOING + r")\b", re.I)
_POINTED_AT = re.compile(
    r"^(?:\s+[\w'’-]+){0,4}?\s+(?:`[^`]+`|\"[^\"]+\"|“[^”]+”|[~.]?/[\w./-]+)"
    r"\s*(?:$|[.,;:!…)*_]|(?:and|by|on|in|from|for|with|to|at|via|now|since|as|through|using)\b)", re.I)
_TRAILS_OFF = re.compile(r"(?:\.\.\.|…|\bnow\b\s*[.!]?)[\s)*_\]]*$", re.I)
_HAS_A_VERB = re.compile(
    r"\b(?:is|are|was|were|be|would|could|can|should|might|may|will|does|do|did|has|have|had|helps?|takes?|means?|"
    r"needs?|requires?|adds?|makes?|gives?)\b|n[’']t\b", re.I)


# "Project `Physics_DeepDive` initialized.", "Reminder set.", "File saved.": the thing, then what
# was done to it, and nothing more. "Well played." is not one: the thing has to be something ELI
# acts on (a word of its own catalogue) or be pointed at.
_DONE_TO_IT = re.compile(
    r"^[\s*_>(\[-]*((?:[\w`'\"“”./~-]+\s+){1,4})(?:" + _DOING.replace(r"(?:ed|ing|d)?", "(?:ed|d)")
    + r"|set)\s*[.!]*[\s*_)\]]*$", re.I)


def _reports_a_thing_done(sentence: str) -> bool:
    done = _DONE_TO_IT.match(sentence)
    if not done:
        return False
    thing = done.group(1)
    if re.search(r"[`\"“]|[~.]?/[\w.-]+/", thing):
        return True
    try:
        from eli.cognition.llm_intent import _catalogue_words
        known = _catalogue_words()
    except Exception:
        return False
    return any(w in known or (w.endswith("s") and w[:-1] in known) for w in re.findall(r"[a-z]{3,}", thing.lower()))


# What ELI runs on is where it is, not something it did: "the language model I'm running on was trained
# by its publisher" was dropped as a claim to have acted.
_HOSTED_ON = re.compile(r"\bi(?:'m|’m| am)\s+(?:\w+ly\s+)?running\s+(?:on|locally|inside|from)\b", re.I)


# Someone doing it now: "I'm reading", "I've checked", "I will now read", "while I scan", "it's
# currently reading". Then, a verb that is work in words ("read", "check", "analyse", "list") on one of
# the things ELI's actions act on is a report of acting, not of writing: "I'm reading the actual source
# code inside the folder line by line" and "Hold on while I scan the critical source files" were served
# with nothing running. On the reply itself ("I've summarised it below", "listed here") it is not.
_DOING_IT_NOW = re.compile(
    r"\b(?:i(?:['’]m|\s+am|['’]ve|\s+have|\s+just|\s+will\s+now|['’]ll\s+now|\s+am\s+now)"
    r"|(?:while|as|until)\s+i|(?:it|eli|the\s+(?:job|scan|task|agent|process))(?:['’]s|\s+is))"
    r"\s+(?:(?:now|just|already|currently|actually|still|carefully|quickly)\s+)*(?P<verb>[a-z]+)\b(?P<rest>[^.!?\n]{0,80})",
    re.I)
_ON_THE_REPLY = re.compile(r"\b(?:below|above|here|as\s+follows|for\s+you\s*:|in\s+(?:this|my)\s+(?:reply|answer|message))\b", re.I)


# Verbs of acts that only read but say nothing of a lookup in a promise: "I'll get back to you",
# "let me help", "I'll test that idea".
_VAGUE = frozenset(("get", "help", "show", "message", "import", "test", "resolve"))


def looking_verbs() -> frozenset:
    """The verbs of looking something up or over: read_verbs() less the vague ones and the verbs of
    saying. What a promise to look ("let me read the key files") is made with."""
    return frozenset(read_verbs() - _VAGUE - _SAYING)


# Verbs of saying: a reply does these in words whatever they are about ("I'm explaining the code").
_SAYING = frozenset("""answer describe dictate explain give help message say show speak summarise summarize tell
transcribe write""".split())


def _acts_on_the_machine(sentence: str) -> bool:
    from eli.execution.route_contracts import _same_word
    verbs = (read_verbs() | (act_verbs() & _DONE_IN_WORDS)) - _SAYING
    things = machine_things()
    for m in _DOING_IT_NOW.finditer(sentence):
        verb = m.group("verb").lower()
        if not any(_same_word(verb, v) for v in verbs if len(v) > 2):
            continue
        rest = m.group("rest")
        if _ON_THE_REPLY.search(rest):
            continue
        if re.search(r"`[^`]+`|[~.]?/[\w.-]+/", rest) or any(w in things for w in re.findall(r"[a-z]+", rest.lower())):
            return True
    return False


def _says_it_acted(sentence: str) -> bool:
    sentence = _HOSTED_ON.sub(" ", sentence)
    if _SAYS_NOTHING_IS.match(sentence):
        return False
    return (bool(_REPORTS_ACTING.search(sentence)) or _names_an_act_under_way(sentence) or _reports_a_thing_done(sentence)
            or _acts_on_the_machine(sentence))


def _names_an_act_under_way(sentence: str) -> bool:
    start = _ACTING_NOW.match(sentence)
    if not start or not start.group(0).rstrip().lower().endswith("ing"):
        return False
    rest = sentence[start.end():]
    if _POINTED_AT.match(rest):
        return True
    return bool(_TRAILS_OFF.search(rest)) and len(sentence.split()) <= 14 and not _HAS_A_VERB.search(rest)


# A fix ELI has no action for: "a hard reset on the media process manager", "re-indexing the
# last 48 hours with stricter temporal weighting", "enable verbose logging in the config".
# Saying it cannot read what it can. Told "I don't have a live feed", the next message read the feed in
# a quarter of a second.
# "I have no record of that session", "the logs from that day do not exist in this runtime", "not in my
# active context": a reply denying the record of a period whose record the turn has in hand (the
# orchestrator put the period log in front of the model and said so, _period_found).
_DENIES_THE_RECORD = re.compile(
    r"\bi\s+(?:do\s+not|don['’]t|have\s+no|can['’]?t|cannot|am\s+unable\s+to)\s+(?:have\s+|see\s+|find\s+|recall\s+|access\s+|retrieve\s+)?"
    r"(?:any\s+|the\s+|a\s+|those\s+|that\s+)?(?:records?|logs?|memor(?:y|ies)|history|transcripts?|timestamps?|conversation)\b"
    r"|\b(?:there\s+(?:is|are)\s+)?no\s+(?:records?|logs?|history|transcripts?|memor(?:y|ies))\s+(?:of|from|for|about)\b"
    r"|\b(?:records?|logs?|history|transcripts?)\b[^.!?\n]{0,40}\b(?:do(?:es)?\s+not|don['’]t|doesn['’]t)\s+exist\b"
    r"|\bnot\s+(?:in|within|part\s+of)\s+(?:my|this|the)\s+(?:active\s+|current\s+)?(?:context|memory|session|window|runtime)\b"
    # "there are no `VR_JOIN` ... events recorded for Fri 06 Nov", "nothing was logged that day": what is
    # recorded denied in any words, not only by the five nouns above
    r"|\b(?:no|not\s+any|none\s+of\s+the)\s+(?:[\w`'’,-]+\s+){0,8}?(?:records?|logs?|entries|events?|activity|history|"
    r"transcripts?|mentions?|traces?)\s+(?:were\s+|was\s+|are\s+|is\s+)?(?:recorded|logged|stored|kept|saved|found|"
    r"of|from|for|about|on)\b"
    r"|\b(?:nothing|none)\s+(?:is\s+|was\s+|has\s+been\s+|got\s+)?(?:recorded|logged|stored|saved|kept)\b"
    r"|\b(?:isn['’]t|wasn['’]t|aren['’]t|weren['’]t)\s+(?:any(?:thing)?\s+)?(?:recorded|logged|stored)\b",
    re.I)
_DENIES_A_READ = re.compile(
    r"\bi\s+(?:do\s+not|don['’]t|can\s*not|can['’]t|have\s+no)\b[^.!?\n]{0,40}"
    r"\b(?:(?:live\s+)?feed\s+of|live\s+feed|your\s+screen|file\s*system|your\s+files|"
    r"what(?:['’]s|\s+is|\s+you['’]re|\s+you\s+are)\s+(?:playing|watching|on\s+your\s+screen))\b", re.I)
# "I am updating the orchestration logic", "I'll tighten up the media flow": changing itself, from a reply.
_CHANGING_ITSELF = re.compile(
    r"\bi(?:'m|’m| am|'ll|’ll| will|'ve|’ve| have)\s+(?:now\s+|also\s+|currently\s+|already\s+)?"
    r"(?:updat|add|patch|chang|fix|rewrit|tighten|adjust|broaden|correct|reconfigur|reset|repair)\w*\b[^.!?\n]{0,60}"
    r"\b(?:logic|agent|layer|parser|parsing|pipeline|code|router|routing|handler|flow|validation|filter|orchestrat\w*|"
    r"path|paths|setting|settings|config\w*|behaviou?r|mapping|alias\w*|index)\b", re.I)
_READ_WORD = re.compile(r"\b(?:look|check|search|read|find|found|recall|remember|fetch|saw|see)\w*\b", re.I)

_INVENTED_FIX = re.compile(
    r"\b(?:the|a|this)\s+fix\s+(?:requires?|needs?|is|would be|involves?)\b"
    r"|\bwe\s+need\s+to\s+(?:enable|re-?index|reset|recalibrate|rebuild|retrain|patch|flush|tighten|re-?embed)\b"
    r"|\bi\s+(?:can|could|will|'ll|’ll|need to|should)\s+(?:now\s+)?(?:re-?index|recalibrate|re-?embed|hard[- ]reset|"
    r"flush|purge|retrain|rebuild (?:the|my)|reset (?:the|my)|tighten|re-?weight|re-?anchor)\b"
    r"|\b(?:enable|turn on|switch on)\s+verbose\b",
    re.I)

# A standing change promised from inside a reply. How ELI routes, searches and logs is decided by
# its code on every turn; "no web search for future questions" is not something a reply can make true.
_STANDING_PROMISE = re.compile(
    r"\b(?:for (?:all |any )?future (?:questions|requests|turns|queries|messages)|from now on|going forward|next time|"
    r"in the future|the next time|whenever you|every time you|"
    r"from here on|never again|(?:not|\w+n['’]t) (?:happen|do (?:that|it)|search\w*) again|any ?more|in future|"
    r"unless you (?:explicitly |specifically )?(?:ask|say|tell|request)|until you (?:say|tell|ask)|"
    r"for the rest of (?:this|the|our) (?:session|conversation|chat))\b", re.I)
_SAYS_IT_DID = re.compile(r"\bi(?:'ve|’ve| have)\s+(?:(?:now|just|already|since)\s+)*\w+(?:ed|en|pt|lt)\b", re.I)
_COMMITS = re.compile(
    r"\bi(?:'ll|’ll| will| won't| won’t| will not| am going to|'m going to|’m going to)\b|^\W*no (?:more )?\w+", re.I)

# A sentence that leans on the one before it: an offer to act on it, or a conclusion drawn from it.
_OFFER = re.compile(
    r"^\W*(?:do you |would you like |d'you )?(?:want|like)\s+me\s+to\b|^\W*(?:shall|should)\s+i\b|^\W*say the word\b", re.I)
_FOLLOWS = re.compile(
    r"^\W*(?:that(?:'s|’s| is| was) why|this is why|which is why|that means|this means|as a result|until then|"
    r"because of (?:this|that)|hence|therefore|in other words|put simply)\b", re.I)

# A question about a fault: what ELI did, got wrong or keeps doing. A diagnosis in the reply
# is checked against the record on these.
_FAULT_RE = re.compile(
    r"\bwhy\b[^.?!]{0,40}\b(?:did|didn'?t|were|keep|can'?t|could ?n'?t|won'?t|are you (?:still|always|lying|making|search))\b"
    r"|\bwhy\b[^.?!]{0,40}\b(?:can|could|would|will|should|did|do)\s+you\s+not\b"
    r"|\b(?:the\s+)?(?:actual|real|underlying)\s+(?:issue|problem|cause|fault)\b"
    r"|\bwhat\s+(?:went|is going|has been going|keeps going)\s+wrong\b|\bwhat(?:'?s| is| was)?\s+(?:wrong|happening|happened)\b"
    r"|\bwhat(?:'?s| is| are)\s+with\s+(?:all\s+)?(?:the|these|those|this|your)\s+(?:issues?|problems?|errors?|bugs?|mistakes?|failures?)\b"
    r"|\bwhat\s+(?:has|have)\s+been\s+going\s+on\b|\bgoing on\b[^.?!]{0,30}\b(?:issues?|problems?|errors?)\b"
    r"|\bwhy\b[^.?!]{0,40}\b(?:wrong|lying|lie[ds]?|making (?:shit|stuff|things) up|ignor\w+|slow|so long|broken|fail\w*)\b"
    r"|\bexplain\b[^.?!]{0,30}\b(?:yourself|your (?:mistake|error)|what happened|why you)\b"
    r"|" + _YOU_JUST_DID + r"|" + WHAT_JUST_HAPPENED,
    re.I)

# What an invented diagnosis reaches for. On a fault question each has to be in the record.
_INTERNALS = re.compile(
    r"\b(?:vectors?|embeddings?|high-dimensional|dimensional space|latent|attention (?:mechanism|leak|head)|"
    r"context window|tokeni[sz]\w+|weights|neural|buffer\w*|cach(?:e|ed|ing)|(?:re-?)?index(?:es|ing|ed)?|"
    r"anchor(?:s|ed|ing)?|drift(?:s|ed|ing)?|decay(?:s|ed|ing)?|alignment|stale|zombie|race condition|deadlock\w*|"
    r"queues?|synchroni[sz]\w+|process (?:manager|cleanup)|heuristics?|classifier|scheduler|daemon|threads?|mutex|"
    r"memory leak|checkpoint\w*|weighting|prioriti[sz]\w+|error states?|failure signals?|log buffer|bitmask|"
    r"garbage collect\w+|state machine|handshake)\b",
    re.I)

# A sentence that explains: gives a cause, or says how ELI's machinery behaves. A word list
# cannot hold every mechanism a model can invent ("has no concept of concurrency", "the
# retrieval score was artificially high"), so on a fault question these must be carried by
# the record: most of what the sentence says has to be written there.
_EXPLAINS = re.compile(
    r"\b(?:because|due to|caused by|as a result|which is why|that(?:'s|’s| is) why|the reason|therefore|hence|"
    r"leading to|led to|causing|resulting in|this (?:indicates|suggests|means|implies)|which (?:means|indicates|caused)|"
    r"instead of|so that|root cause|failure mode|the (?:issue|problem|fault|bug) (?:is|was|lies)|"
    r"stem(?:s|med|ming)? from|comes? down to|boils? down to|(?:failure|breakdown|gap|flaw) in (?:how|the|my)|"
    r"(?:core|underlying|real|actual) (?:issue|problem|cause|fault)|broke down|breaks down|"
    r"(?:the\s+)?(?:result|product|fault|work)\s+of|(?:was|is)\s+down\s+to|masked|swallowed|failed\s+(?:silently|to))\b"
    r"|\b(?:my|its|the)\s+(?:\w+\s+){0,2}(?:executor|system|index|retrieval|router|pipeline|agents?|model|logic|layer|"
    r"engine|orchestrat\w+|mechanism|subsystem|module|stack|scheduler)\b"
    r"|\b(?:has|have|had|with)\s+no\s+(?:concept|notion|way|awareness|tracking|state)\b|\bthere\s+is\s+no\s+\w+\s+(?:tracking|check|sync\w*|link)\b",
    re.I)
# Invented self-performance statistics. ELI tracks per-turn confidence and timings (and reports those
# from the record), but keeps NO "success rate" / "accuracy" / "reliability" percentage for itself — so a
# sentence stating one about itself is fabricated. Live: "my timestamp diagnostics (est. 67%
# success rate lately)". 'confidence' is deliberately excluded — ELI does report a real per-turn one.
_INVENTED_SELF_METRIC = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s*%?\s*(?:success\s+rate|accuracy|reliab\w*|uptime|"
    r"error\s+rate|failure\s+rate|hit\s+rate|precision|recall)\b"
    r"|\b(?:success\s+rate|accuracy|reliability|uptime|error\s+rate|failure\s+rate|hit\s+rate)\b[^.!?\n]{0,40}?\d{1,3}(?:\.\d+)?\s*%?"
    r"|\b\d{1,3}(?:\.\d+)?\s*%\s+(?:of\s+(?:\w+\s+){1,3})?(?:(?:is|are)\s+)?(?:fail\w*|succe\w*|complete\w*|done|finished|working)\b",
    re.I)
_SPEAKS_OF_ITS_OWN = re.compile(r"\b(?:i|i['’]m|my|mine|our|eli['’]?s?|job|analysis|scan|task)\b", re.I)
# "Did you open it?" "Yes." The record says whether it did.
# "I was built by ELI Systems": a maker named, to be checked against the one the licence names.
_NAMES_ITS_MAKER = re.compile(
    r"\b(?:i\s+(?:was|am|were)|i['’]m|eli\s+(?:was|is))\s+(?:\w+\s+){0,3}?"
    r"(?:built|made|created|developed|designed|written|programmed|coded)\s+by\s+(?P<a>[^.,;:!?()\n]+)"
    r"|\bmy\s+(?:creator|maker|developer|author|builder)s?\s+(?:is|are|was|were)\s+(?P<b>[^.,;:!?()\n]+)", re.I)
# What a sentence names that only the facts could stand behind: anything with a digit, anything in backticks.
_SPECIFIC = re.compile(r"`([^`]+)`|([\w.+-]*\d[\w.+%-]*)")
_ASKED_IF_IT_DID = re.compile(r"\b(?:did\s+you|have\s+you|you\s+said\s+you)\s+(?:\w+ly\s+)?(\w{3,})", re.I)
_SAYS_YES = re.compile(r"\s*yes\b", re.I)
_SUPPORT_NEEDED = 0.6
_FILLER = frozenset("""that this these those with from have been were was your you're they them then than when what
which will would could should there their about into over also just only some more most very because instead
since while where here does did doing done being both each other such same after before between through
during again once ever never always still even much many well back down away still really actually simply""".split())

_BACKTICKED = re.compile(r"`([A-Za-z_][\w.\-]{2,60})`")
_CODE_SHAPED = re.compile(r"[_.]|^[A-Z0-9_]{4,}$")
# A path to a file. A segment written as an ellipsis ("/…/evidence.jsonl", ".../logs/audit.log") is
# a guess at one, and no file is there.
_PATH = re.compile(r"(?<![\w/])((?:~|\.{0,2}|…)?/(?:(?:[\w.\-]+|…)/)+[\w.\-]+\.[A-Za-z0-9]{1,8}|(?:[\w\-]+/)+[\w\-]+\.(?:ya?ml|json|toml|ini|cfg|conf|log|sqlite3?|db))")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S.*|(?:\d+[.)]\s*)?\*\*[^*\n]{2,140}\*\*:?|\*\*\d+[.)][^*\n]{2,140}\*\*:?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s+")
_LIST_MARK = re.compile(r"^\s*(?:\*\*)?(?:[-*•]+|\d+[.)])\s*(?:\*\*)?\s*$")
_ELI_SPEAKS = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?:ELI|Eli|Assistant)\s*:")
_USER_SPEAKS = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?(?:You|User|Human)\s*:")


def asks_about_fault(text: Any) -> bool:
    said = without_address(text)
    return bool(_FAULT_RE.search(said)) or complains_about_eli(said)


def without_own_prose(text: Any) -> str:
    """Prompt context minus ELI's earlier replies. They are not evidence about ELI: counted as
    such, an invented setting licenses itself on the next turn."""
    out: List[str] = []
    skipping = False
    for line in str(text or "").splitlines():
        if _ELI_SPEAKS.match(line):
            skipping = True
            continue
        if skipping and (_USER_SPEAKS.match(line) or not line.strip()):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out)


_CREATES_TABLE = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?([A-Za-z_]\w*)", re.I)


@lru_cache(maxsize=1)
def known_identifiers() -> frozenset:
    """Every name ELI really has: actions, settings, tunables, agents, tables, modules."""
    names: set = set()
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        names.update(SUPPORTED_ACTIONS)
    except Exception:
        log.debug("known identifiers: actions unavailable", exc_info=True)
    try:
        from eli.core.runtime_settings import DEFAULTS
        names.update(DEFAULTS)
    except Exception:
        log.debug("known identifiers: settings unavailable", exc_info=True)
    try:
        from eli.core.cognition_tunables import TUNABLES
        names.update(t.key for t in TUNABLES)
    except Exception:
        log.debug("known identifiers: tunables unavailable", exc_info=True)
    try:
        from eli.cognition.agent_bus import _ALL_AGENTS
        names.update(getattr(a, "name", "") for a in _ALL_AGENTS)
    except Exception:
        log.debug("known identifiers: agents unavailable", exc_info=True)
    try:
        import sqlite3
        from eli.core.paths import agent_db_path, user_db_path
        for db in (user_db_path(), agent_db_path()):
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
            try:
                names.update(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            finally:
                con.close()
    except Exception:
        log.debug("known identifiers: tables unavailable", exc_info=True)
    try:
        from pathlib import Path
        import eli
        for p in Path(eli.__file__).resolve().parent.rglob("*.py"):
            names.add(p.stem)
            names.add(p.name)
            # the tables ELI's code makes, whether or not this install has made them yet: on a fresh
            # one, `runtime_events` was dropped as a table ELI does not have
            try:
                names.update(_CREATES_TABLE.findall(p.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    except Exception:
        log.debug("known identifiers: modules unavailable", exc_info=True)
    return frozenset(n.lower() for n in names if n)


def _invented_identifier(sentence: str, evidence_low: str) -> Optional[str]:
    for name in _BACKTICKED.findall(sentence):
        low = name.lower().rstrip(".")
        if not _CODE_SHAPED.search(name):
            continue
        if low in known_identifiers() or low in evidence_low:
            continue
        if low.rsplit(".", 1)[0] in known_identifiers():
            continue
        return name
    return None


def _path_exists(path: str) -> bool:
    from pathlib import Path
    try:
        p = Path(path).expanduser()
        if p.is_absolute():
            return p.exists()
        from eli.core.paths import config_dir, data_dir, project_root
        return any((base / p).exists() for base in (project_root(), data_dir(), config_dir()))
    except Exception:
        return True


def _invented_path(sentence: str, evidence_low: str) -> Optional[str]:
    for path in _PATH.findall(sentence):
        if path.lower() in evidence_low or _path_exists(path):
            continue
        return path
    return None


def _stems(text: str) -> set:
    return {w[:6] for w in re.findall(r"[a-z][a-z0-9_']{3,}", str(text or "").lower())}


def _supported(sentence: str, record_stems: set) -> float:
    """How much of what the sentence says is in the record: the share of its content words found there."""
    words = [w for w in re.findall(r"[a-z][a-z0-9_']{3,}", sentence.lower()) if w not in _FILLER]
    if len(words) < 3:
        return 1.0
    return sum(1 for w in words if w[:6] in record_stems) / len(words)


def _unrecorded_internal(sentence: str, record_low: str) -> Optional[str]:
    for m in _INTERNALS.finditer(sentence):
        term = m.group(0).lower()
        if term[:5] not in record_low:
            return term
    return None


def _live_facts() -> str:
    """What is true of ELI right now, lower case: the model file and the load as the loader reported it,
    and who made it. "I am running on `Qwen3.6-35B-A3B-Q4_K_M.gguf`" is a fact here, not a claim to act."""
    import json
    from eli.cognition import gguf_inference
    from eli.cognition.context_synthesiser import _runtime_snapshot
    from eli.runtime.license_info import made_by
    path = gguf_inference.get_model_path()
    facts = [str(path or "").replace("\\", "/").rsplit("/", 1)[-1], made_by()]
    try:
        facts.append(json.dumps(_runtime_snapshot() or {}, default=str))
    except (OSError, ValueError):
        log.warning("the runtime snapshot could not be read: statements about the load are checked without it",
                    exc_info=True)
    return "\n".join(facts).lower()


# A file named as made or kept at a path: "I've written the patch to /home/x/fix.patch", "The file is
# at /home/x/fix.patch". Held to the disk in every turn: nothing was there, and the person went looking.
# Only what ELI says it did, or where it says a file is: "Put it in ~/.config/x.json" tells the person
# where to put something, and is not checked.
_INTO = r"[^.!?\n]{0,60}?\b(?:to|in|at|into|under|as)\s+[`\"'“*_]*(?:~|\.{0,2})?/"
_PUT_AT = re.compile(
    r"\bi(?:['’]ve|\s+have|\s+just)?\s+(?:(?:now|just|already)\s+)*"
    r"(?:written|wrote|saved|put|placed|stored|created|exported|generated|dropped)\b" + _INTO
    + r"|\b(?:it|this|that|they|(?:the|your|my)\s+[\w-]+(?:\s+[\w-]+)?)\s+(?:is|are|was|were|has\s+been|have\s+been)\s+"
    r"(?:now\s+)?(?:(?:written|saved|put|placed|stored|created|exported|located|sitting|ready)\s+)?(?:to|in|at|into|under)\s+"
    r"[`\"'“*_]*(?:~|\.{0,2})?/"
    r"|^\W*(?:saved|written|exported|created)\b" + _INTO
    # "the ledger lives in /x", "it is kept at /x", "can be found in /x"
    + r"|\b(?:lives?|living|kept|stored|held|sits?|resides?|found|located|written)\s+(?:in|at|under|inside)\s+"
    r"[`\"'“*_]*(?:~|\.{0,2}|…)?/"
    # "Evidence Ledger: /…/evidence.jsonl", "- **Audit log:** `~/x/audit.log`": a record of ELI's named, and where
    + r"|^\W*(?:[\w-]+\s+){0,3}(?:ledger|log|logs|audit|record|records|history|database|db|store|journal|trail|"
    r"memory|memories|events)\W*:\W*(?:~|\.{0,2}|…)?/", re.I)
# A lead that says where things are, for the list under it: "The record lives in two places:".
_LOCATING_LEAD = re.compile(
    r"\b(?:lives?|kept|stored|held|sits?|resides?|found|located|written|saved)\s+(?:in|at|under)\b[^.!?\n]{0,40}:\s*$", re.I)
# Said of what is to come, not of what is: "I'll put it in /x", "want me to save it to /x?".
_TO_COME = re.compile(r"\b(?:i['’]ll|i\s+will|i\s+can|i\s+could|i['’]d|let\s+me|want\s+me\s+to|shall\s+i|should\s+i)\b", re.I)
# An answer said to come from a lookup: "Here is the actual answer, from a live search:".
_FROM_A_LOOKUP = re.compile(
    r"\b(?:from|per|according\s+to|based\s+on)\s+(?:a|my|the|this|that)\s+"
    r"(?:(?:live|quick|fresh|new|web|online|internet|real[- ]time)\s+)*(?:search|lookup|look-up)\b", re.I)
_LOOKUP_ACTION = re.compile(r"SEARCH|FETCH|LOOKUP|BROWSE|NEWS|WEATHER", re.I)
# Asked for something made up or written out, a weekday in it is not said of today: "It's Saturday
# morning in the village" in a story.
_ASKED_TO_WRITE = re.compile(
    r"\b(?:stor(?:y|ies)|poems?|lyrics|songs?|scripts?|scenes?|fiction|novel|chapters?|dialogue|examples?|drafts?|"
    r"write|compose|imagine|pretend|role[- ]?play|make\s+up)\b", re.I)


def _put_nowhere(sentence: str, evidence_low: str) -> Optional[str]:
    """The path a sentence says a file was written to or is at, when nothing is there."""
    if not _PUT_AT.search(sentence) or _TO_COME.search(sentence) or sentence.rstrip(" *_)\n").endswith("?"):
        return None
    return _invented_path(sentence, evidence_low)


# ── claims a reply makes about ELI itself, whatever it was asked ─────────────

# A part of ELI: what a sentence names when it explains ELI's own workings.
_OWN_PART = (r"(?:executor|system|index(?:er)?|retrieval|router|routing|pipeline|agents?|model|logic|layer|engine|"
             r"orchestrat\w*|mechanism|subsystem|module|stack|scheduler|pars(?:er|ing)|tool(?:s|ing)?(?:\s+calls?)?|"
             r"interface|handler|daemon|queue|context(?:\s+window)?|memory|search|runtime|code(?:base)?|backend|"
             r"integration|wrapper|plugin|process|thread|loop|ledger|logs?|records?|database|tables?|settings?|config\w*|"
             r"history|audit|capture|vision|ocr)")
# A sentence about ELI's own workings: one of its parts as "my ...", or "the ... I use / I'm running on",
# its records as such, or itself failing at something. "The index goes stale when vectors change" in
# an answer about databases is none of these; "my internal routing mangled the path" is.
_ITS_OWN_WORKINGS = re.compile(
    r"\b(?:my|eli['’]s)\s+(?:own\s+)?(?:[\w-]+\s+){0,2}" + _OWN_PART + r"\b"
    r"|\b(?:the|this|that)\s+(?:[\w-]+\s+){0,2}" + _OWN_PART + r"\s+(?:i|i['’]m|i\s+am|i['’]ve|i\s+use|i\s+run|i\s+keep|that\s+i|eli)\b"
    r"|\bi\s+(?:had\s+to|tried\s+to|failed\s+to|was\s+(?:unable|trying)\s+to|could(?:n['’]t|\s+not))\b"
    r"|\b(?:the\s+)?(?:action|audit|evidence|turn|event)\s+(?:ledger|log|record)s?\b"
    r"|\b(?:recorded|logged)\s+(?:in|for|on|by|under)\b|\bevents?\s+recorded\b",
    re.I)
# On a turn about ELI, its parts named plainly are its own too: "the executor stripped the suffix",
# "a tool call failed silently", "the parsing was instant; the hardware handshake ...".
_A_PART = re.compile(r"\b(?:the|a|an|this|that|its|their)\s+(?:[\w-]+\s+){0,2}" + _OWN_PART + r"\b", re.I)


def speaks_of_its_own_workings(sentence: str) -> bool:
    return bool(_ITS_OWN_WORKINGS.search(str(sentence or "")))


# Saying ELI cannot do something: "I can't force Spotify to resume a paused session from here", "I
# don't have control over its playback", "I have no way to see your screen". The next message,
# "RESUME", worked in half a second.
_DENIES_AN_ABILITY = re.compile(
    r"\bi\s+(?:can\s*not|can['’]?t|am\s+unable\s+to|['’]m\s+unable\s+to|am\s+not\s+able\s+to|['’]m\s+not\s+able\s+to|"
    r"have\s+no\s+(?:way|means|ability)\s+to|(?:do\s+not|don['’]t)\s+have\s+(?:the\s+)?(?:ability|means|power)\s+to)\s+"
    r"(?P<act>[^.!?\n—–;]{3,80})"
    r"|\bi\s+(?:have\s+no|(?:do\s+not|don['’]t)\s+have(?:\s+any)?)\s+(?:control|access)\s+(?:over|to)\s+(?P<over>[^.!?\n—–;]{3,60})",
    re.I)
# What makes "I can't" false: "force Spotify to resume" is resuming Spotify.
_CAUSATIVE = re.compile(r"^\s*(?:force|make|get|have|let|tell|ask)\s+(?:[\w.'’-]+\s+){1,3}?to\s+", re.I)
_FROM_HERE = re.compile(r"\s+(?:from\s+here|right\s+now|at\s+the\s+moment|for\s+you|anymore|any\s*more)\b.*$", re.I)


def _denied_act(sentence: str) -> Optional[str]:
    """The act a sentence says ELI cannot do, as a request ELI's router can read, or None."""
    m = _DENIES_AN_ABILITY.search(sentence)
    if not m:
        return None
    if m.group("act"):
        act = _CAUSATIVE.sub("", m.group("act"))
        named = re.match(r"^\s*(?:force|make|get|have|let|tell|ask)\s+((?:[\w.'’-]+\s+){1,3}?)to\s+", m.group("act"), re.I)
        act = _FROM_HERE.sub("", act).strip(" ,")
        if named:
            act = f"{act} {named.group(1).strip()}"
    else:
        act = "control " + _FROM_HERE.sub("", m.group("over")).strip(" ,")
    return act or None


def _can_do(act: str) -> Optional[str]:
    """The action ELI has for `act`, when it has one and nothing has shown it cannot run here."""
    try:
        from eli.execution.router_enhanced import route
        routed = route(act) or {}
    except Exception:
        log.debug("ability check: router unavailable", exc_info=True)
        return None
    action = str(routed.get("action") or "").upper()
    if action in ("", "CHAT", "UNKNOWN", "NOOP"):
        return None
    try:
        from eli.runtime.capability_state import get_state
        state = get_state(action) or {}
    except Exception:
        state = {}
    if any(state.get(k) is False for k in ("dependency_ready", "reachable", "device_available")):
        return None          # it cannot run here: saying so is true
    return action


# The state something was left in: "Shuffle is now confirmed off", "Playback is locked in", "the volume
# is now at 40%". After a toggle whose result said only "Shuffle toggle", "off" was nothing ELI read.
_STATE_CLAIM = re.compile(
    r"\b(?P<thing>[\w-]+(?:\s+[\w-]+){0,2})\s+(?:is|are|has\s+been|have\s+been)\s+(?:now\s+)?"
    r"(?:(?:confirmed|definitely|fully|properly|officially|firmly|successfully)\s+)?"
    r"(?P<state>on|off|enabled|disabled|paused|playing|stopped|muted|unmuted|locked(?:\s+in)?|active|inactive|"
    r"shuffled|unshuffled|closed|open|maximi[sz]ed|minimi[sz]ed|set\s+to\s+\S+|at\s+\d+\s*%?)\b",
    re.I)


def _states_a_thing_left(sentence: str) -> Optional[tuple]:
    """(thing, state) when a sentence says what something ELI acts on was left as."""
    for m in _STATE_CLAIM.finditer(sentence):
        thing = m.group("thing").lower().split()
        if any(w in machine_things() or w in act_verbs() or w in ("shuffle", "playback", "volume", "music", "song",
                                                                  "track", "window", "player", "repeat")
               for w in thing):
            return m.group("thing"), m.group("state")
    return None


# Writing to its memory: "I have logged that X is an alias for Y", "I've noted that", "I'll remember
# that", "saved that for next time". Nothing logged an alias: no action wrote anything.
_WRITES_TO_MEMORY = re.compile(
    r"\bi(?:['’]ve|\s+have|\s+just|\s+now)?\s+(?:(?:now|just|already)\s+)*(?:logged|noted|saved|stored|recorded|filed|"
    r"remembered|memori[sz]ed|added|updated|written|made\s+a\s+note)\b[^.!?\n]{0,40}\b(?:that|this|it|the\s+fact|as)\b"
    r"|^\W*(?:noted|logged|saved|stored)\s+(?:that|this|it)\s+(?:for|as)\b",
    re.I)


@lru_cache(maxsize=1)
def _memory_writing_actions() -> frozenset:
    """The actions that write to ELI's memory or notes: ones that change something and whose catalogue
    entry is about remembering, notes or memory."""
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        from eli.execution.route_contracts import changes_something
        from eli.tools.registry.capabilities_doc import action_words
    except Exception:
        return frozenset()
    return frozenset(a for a in SUPPORTED_ACTIONS if changes_something(str(a)) and re.search(
        r"\b(?:memor\w*|remember\w*|notes?|store)\b", action_words(str(a), with_phrases=False) + " " + str(a).lower().replace("_", " ")))


# Saying what the screen or a picture shows: "I have the image now. Here is what is on the screen:",
# "The previous screenshot shows your desktop: ...". Nothing had looked at either.
_REPORTS_SEEING = re.compile(
    r"\bi\s+(?:can\s+)?see\s+(?:on\s+)?(?:your|the|that|this)\s+(?:screen|desktop|image|picture|photo|screenshot|window)\b"
    r"|\bi(?:['’]ve|\s+have)\s+(?:got\s+)?the\s+(?:image|picture|photo|screenshot)\b"
    r"|\b(?:the|your|this|that|my)\s+(?:previous\s+|last\s+|latest\s+)?(?:screenshot|image|picture|photo|screen|capture)\s+"
    r"(?:shows?|showed|has|contains|displays|is\s+showing)\b"
    r"|\bhere(?:['’]s|\s+is)\s+what(?:['’]s|\s+is)\s+on\s+(?:the|your)\s+screen\b"
    r"|\bon\s+(?:the|your)\s+screen\s+(?:right\s+now\s+)?(?:is|are|there\s+(?:is|are))\b",
    re.I)
_LOOKED = re.compile(r"SCREEN|IMAGE|OCR|VISION|CAMERA|PHOTO", re.I)


def _figures_of(sentence: str) -> List[tuple]:
    """Each figure in a sentence with what it counts: [("18", "%"), ("6", "runs"), ("2", "")]."""
    return [(m.group(1), (m.group(2) or "").lower()) for m in
            re.finditer(r"(?<![\d:.])(\d+(?:\.\d+)?)(?![\d:])\s*(%|percent|per\s+cent|[a-z]+)?", sentence, re.I)]


def _figure_in(number: str, unit: str, evidence: str) -> bool:
    """The figure as itself in the evidence, not as digits inside another number: "18%" is not in
    "16:18", "6 runs" is not in "16 runs"."""
    num = re.escape(number)
    if unit in ("%", "percent", "per cent"):
        pat = num + r"\s*(?:%|percent|per\s+cent)"
    elif unit:
        pat = num + r"\s*" + re.escape(unit[:4])
    else:
        pat = num
    return bool(re.search(r"(?<![\d:.])" + pat + r"(?![\d])", evidence, re.I))


def _measured_figure_holds(sentence: str, number: str, unit: str) -> bool:
    """A percentage said of an action ELI measures holds when it is that action's measured chance of
    success (runtime.evidence_ledger.action_reliability), as rounded where it is written."""
    if unit not in ("%", "percent", "per cent"):
        return False
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        from eli.runtime.evidence_ledger import action_reliability
    except Exception:
        return False
    low = sentence.lower()
    named = [a for a in SUPPORTED_ACTIONS if re.search(r"\b" + re.escape(str(a).lower()) + r"\b", low)
             or re.search(r"\b" + re.escape(str(a).lower().replace("_", " ")) + r"\b", low)]
    places = len(number.split(".")[1]) if "." in number else 0
    for a in named:
        try:
            r = action_reliability(str(a))
        except Exception:
            continue
        if r.get("n") and round(float(r["p"]) * 100, places) == float(number):
            return True
    return False


def _turn_evidence(facts: Dict[str, Any], evidence: str) -> str:
    """The evidence for every claim check of this turn: what each pass was given, kept on the turn.
    The live stream was checked against the memory and the brief, the stored copy against nothing,
    so a figure stood on screen and went from the record."""
    new = str(evidence or "")
    if not facts:
        return new
    held = str(facts.get("_claims_evidence") or "")
    if new.strip() and new not in held:
        held = f"{held}\n{new}".strip()
    facts["_claims_evidence"] = held
    return held


def _agreed_done() -> Optional[Dict[str, Any]]:
    """The rest of a message whose commands were carried out first: what was done ("done", the
    results' text, and "ran", the actions, when the engine notes them)."""
    try:
        from eli.kernel.request_context import agreed_task_var
        agreed = agreed_task_var.get()
    except Exception:
        return None
    return agreed if isinstance(agreed, dict) and str(agreed.get("done") or "").strip() else None


@lru_cache(maxsize=512)
def _action_words(action: str) -> frozenset:
    """An action's name and what the catalogue says it does, as words."""
    try:
        from eli.tools.registry.capabilities_doc import action_words
        about = action_words(str(action), with_phrases=False)
    except Exception:
        about = ""
    return frozenset(_words_of(str(action).replace("_", " ") + " " + about)
                     | {w for w in str(action).lower().split("_") if len(w) > 2})


@lru_cache(maxsize=1)
def _catalogue_actions() -> tuple:
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
        return tuple(sorted(str(a) for a in SUPPORTED_ACTIONS))
    except Exception:
        return ()


def _may_run(action: str) -> bool:
    """False when the capability state says the action cannot run here; True otherwise (missing is
    not false: runtime.capability_state)."""
    try:
        from eli.runtime.capability_state import get_state
        state = get_state(action) or {}
    except Exception:
        return True
    return not any(state.get(k) is False for k in ("dependency_ready", "reachable", "device_available"))


# What ELI does, for a promise to do it differently: its act verbs and the words of how it picks.
_ITS_WAYS = frozenset(("route", "remember", "log", "record", "track", "pick", "choose", "prefer", "default", "match",
                       "map", "treat", "handle", "answer", "reply", "respond"))


def _names_one_of_its_acts(sentence: str) -> bool:
    words = set(re.findall(r"[a-z]+", sentence.lower()))
    verbs = set(act_verbs()) | _ITS_WAYS
    return bool(speaks_of_its_own_workings(sentence)) or _shares_a_word(
        {w for w in words if len(w) > 2}, {v for v in verbs if len(v) > 2})


# A unit that is only a tool call written out: "[EXTRACT_LOG]", "[RUN_CMD: ls -la]", "extract_log(path=x)",
# {"action": "EXTRACT_LOG"}. A model with no tool for what it was asked writes one; it is not an answer,
# and nothing ran because of it. ALL_CAPS words joined by underscores in brackets, or one of ELI's
# action names; a snake_case call; a JSON object naming an action, tool or function.
_TOOL_CALL_SHAPE = re.compile(
    r"^\s*(?:\[\s*(?P<name>[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)\s*(?:[:(][^\]\n]*)?\]"
    r"|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\s*\([^()\n]*\)"
    r"|\{\s*\"(?:action|tool|tool_name|function|name)\"\s*:[^\n]*\})\s*[.;]?\s*$")


def is_a_pretend_tool_call(unit: str) -> bool:
    """Whether the unit is only a tool call written out (_TOOL_CALL_SHAPE): a bracketed name has to be
    two or more words joined by underscores or one of ELI's actions ("[BBC]" is a source tag)."""
    m = _TOOL_CALL_SHAPE.match(str(unit or ""))
    if not m:
        return False
    name = m.group("name")
    return name is None or "_" in name or name in _catalogue_actions()


class _Check:
    """One turn's claim check, fed a reply one unit (sentence or line) at a time."""

    def __init__(self, evidence: str = "", instructions: str = ""):
        self._instructions = " ".join(str(instructions or "").lower().split())
        facts = _turn_context()
        self._facts = facts
        asked = str(facts.get("user_input") or "")
        self._asked = asked
        # About ELI by its wording or by what ELI had just done (the record), inside a turn; outside
        # one there is no record to read, only the wording.
        self.about_self = about_eli(asked) if facts else asks_about_own_behaviour(asked)
        # One evidence for the turn: what an earlier pass over this reply was given still stands
        # behind it here (the stored copy was checked with none, and lost a figure the screen kept).
        evidence = _turn_evidence(facts, evidence)
        self._writing = bool(_ASKED_TO_WRITE.search(asked))
        # Whether the user asked ELI to do something: only then is "nothing was carried out" news.
        try:
            from eli.runtime.command_splitter import is_request, read_message, starts_with_command
            self.asked_to_act = any(
                is_request(part["said"]) or (part["tells"] and starts_with_command(part["tells"]))
                for part in read_message(asked))
        except Exception:
            self.asked_to_act = True
        self._ran_at_start = bool(facts.get("executed_actions")) or _carried_out_before()
        # What the pipeline recorded, plus the user's own words. Not ELI's. On a turn about ELI the
        # record is read here when nothing put it in front of the model (the cause checks need it).
        self._raw_evidence = without_own_prose(evidence)
        self._record = str(facts.get("_record") or "").lower()
        if self.about_self and not self._record:
            self._load_record()
        self._fault = bool(self._record) and (asks_about_fault(asked) or reacts_to_last_reply(asked)
                                              or self._follows_a_failure())
        self._evidence = f"{self._raw_evidence}\n{self._record}\n{asked}".lower()
        _did = _ASKED_IF_IT_DID.search(asked)
        # Not the question itself: it holds the very word.
        self._yes_unbacked = bool(_did) and _did.group(1)[:4].lower() not in (
            f"{without_own_prose(evidence)}\n{self._record}\n{' '.join(facts.get('executed_actions') or [])}".lower())
        self._record_and_asked = f"{self._record}\n{asked.lower()}"
        self._record_stems = _stems(self._record_and_asked) if self._record else set()
        self._locating = False         # under a lead that says where things are ("lives in two places:")
        self._heading: Optional[str] = None
        self._dropped_under_heading = False
        self._last_dropped = False
        # A question about a period: the days in it that have anything recorded.
        self._period_window = facts.get("_period_window")
        self._period_days = set(facts.get("_period_days") or [])
        self._skip_section = False
        self._live = _live_facts()
        self.unrecorded_days: List[str] = []
        self.denied_record = False     # a sentence denied the record of a period that is in hand
        self._line_open = False  # served text since the last newline
        self.dropped: List[str] = []
        self.kept_words = 0
        self.code_served = False
        self.claimed_acting = False
        self._lead: Optional[str] = None

    def _load_record(self) -> None:
        """The pipeline's record of the last turns, read once a turn and kept on it, for the checks
        and for the model when the reply is said again."""
        if not self._facts:
            return
        if self._facts.get("_record_lines"):
            self._record = str(self._facts.get("_record") or "\n".join(self._facts["_record_lines"])).lower()
            self._record_stems = _stems(f"{self._record}\n{self._asked.lower()}")
            self._record_and_asked = f"{self._record}\n{self._asked.lower()}"
            return
        if not self._facts.get("request_id"):
            return                   # not a turn the pipeline records
        try:
            from eli.memory.memory import get_memory
            mem = get_memory()
        except Exception:
            mem = None
        rows = list(((self._facts.get("_about_eli") or {}).get("rows")) or []) or recent_turn_rows(mem, limit=8)
        lines = format_turn_rows(rows)
        if not lines:
            return
        period = "\n".join(str(x) for x in (self._facts.get("_period_lines") or []))
        self._facts["_record_lines"] = lines
        self._facts["_record"] = "\n".join(lines) + ("\n" + period if period else "") + (
            "\n" + str(self._facts.get("_live_facts")) if self._facts.get("_live_facts") else "")
        self._record = str(self._facts["_record"]).lower()
        self._record_stems = _stems(f"{self._record}\n{self._asked.lower()}")
        self._record_and_asked = f"{self._record}\n{self._asked.lower()}"
        self._evidence = f"{self._raw_evidence}\n{self._record}\n{self._asked}".lower()

    def _recent_rows(self) -> List[Dict[str, Any]]:
        return list(((self._facts.get("_about_eli") or {}).get("rows")) or [])

    def _follows_a_failure(self) -> bool:
        rows = self._recent_rows()
        return bool(rows) and not rows[-1].get("ok", True) and refers_to_recent_action(self._asked, rows)

    def _ran(self) -> List[str]:
        """The actions that ran this turn, and in the rest of a message whose commands ran, theirs."""
        ran = [str(a) for a in (self._facts.get("executed_actions") or [])]
        agreed = _agreed_done()
        if agreed:
            ran += [str(a) for a in (agreed.get("ran") or [])]
        return [a for a in dict.fromkeys(a.upper() for a in ran) if a]

    def _backed(self, sentence: str) -> bool:
        """Whether what ran this turn can stand behind the sentence: an action it is about (a word of
        it, its verb or what it acts on, is a word of the action's name or of what the catalogue says
        the action does), or, for a sentence about looking something up, an action that looked. A
        memory lookup does not stand behind "I am starting the background job for the three folders",
        and a song played does not stand behind "I have logged that one title is an alias for another"."""
        from eli.execution.route_contracts import changes_something
        said = _words_of(sentence) | set(re.findall(r"[a-z]+", sentence.lower())) & set(act_verbs())
        ran = self._ran()
        for action in ran:
            if _shares_a_word(said, _action_words(action)):
                return True
        # "Consider it done." names no act of its own: it is about what was asked, and stands when
        # something that does things ran. "I've enabled verbose logging" names one, and a timer set
        # does not stand behind it.
        names_an_act = any(_shares_a_word([w], [v]) for w in said for v in act_verbs() if len(v) > 2) or any(
            w in machine_things() for w in said) or bool(_ENABLED_CLAIM.search(sentence) and re.search(
                r"\b(?:logging|audit|tracking|monitoring|recording|verbose|filter|threshold|setting|config|mode|index|"
                r"pipeline|memory|retrieval|gating)", sentence, re.I))
        if not names_an_act and any(changes_something(a) for a in ran + (["_AGREED"] if _agreed_done() else [])):
            return True
        agreed = _agreed_done()
        if agreed and not agreed.get("ran"):
            # what ran in the rest of the message is known by the words of its results ("Opened app:
            # Spotify"): the sentence's own act has to be one of them
            done = _words_of(str(agreed.get("done") or "")) | set(re.findall(r"[a-z]+", str(agreed.get("done") or "").lower()))
            acts = {v for v in act_verbs() if len(v) > 2 and any(_shares_a_word([w], [v]) for w in said)}
            if acts and _shares_a_word(acts, done):
                return True
        return any(not changes_something(a) for a in ran) and bool(_READ_WORD.search(sentence))

    def _states_what_is_true(self, sentence: str) -> bool:
        """Every specific the sentence names (a file, a figure) is in the live facts."""
        named = [a or b for a, b in _SPECIFIC.findall(sentence)]
        return bool(named) and bool(self._live) and all(n.lower().strip(" .") in self._live for n in named)

    def _invented_maker(self, sentence: str) -> Optional[str]:
        m = _NAMES_ITS_MAKER.search(sentence)
        if not m:
            return None
        maker = (m.group("a") or m.group("b") or "").strip()
        # a person or company by name; "you" or "a small team" is not checked against anything
        names = re.findall(r"\b[A-Z][\w&.-]+", maker)
        if not names or all(n.lower() in self._live for n in names):
            return None
        return maker

    def _denies_what_it_can(self, sentence: str) -> Optional[str]:
        """The action that shows a denial of an ability false: ELI has one for the act, and nothing has
        shown it cannot run here (its capability state, or a run of it that failed on the last turns:
        right after two failed screen reads, "I can't see your screen" is true)."""
        failed = {str(r.get("action") or "").upper() for r in self._recent_rows() if not r.get("ok", True)}
        if _DENIES_A_READ.search(sentence):
            things = _words_of(_DENIES_A_READ.search(sentence).group(0)) - {"have", "live", "what", "your"}
            candidates = [a for a in _catalogue_actions() if _shares_a_word(things, _action_words(a))]
            if any(a in failed for a in candidates):
                return None          # it tried that kind of read and it failed: saying so is true
            standing = [a for a in candidates if _may_run(a)]
            return standing[0] if standing else None
        act = _denied_act(sentence)
        if not act:
            return None
        action = _can_do(act)
        return action if action and action not in failed else None

    def why(self, unit: str) -> str:
        s = str(unit or "")
        if not s.strip():
            return ""
        if not self._writing and is_a_pretend_tool_call(s):
            # dropped, and the turn says nothing was carried out unless something did run
            self.claimed_acting = self.claimed_acting or not self._ran()
            return "is a tool call written out, and nothing ran because of it"
        maker = self._invented_maker(s)
        if maker:
            return f"names {maker} as its maker, and the licence names someone else"
        # Smaller models read their prompt back: "You always know today's weekday and date".
        said = " ".join(s.lower().split())
        if self._instructions and len(said) >= 30 and said in self._instructions:
            return "reads its own instructions back"
        if _DENIES_A_READ.search(s) or _DENIES_AN_ABILITY.search(s):
            able = self._denies_what_it_can(s)
            if able:
                return f"says it cannot do what it can ({able})"
        if self._facts.get("_period_found") and _DENIES_THE_RECORD.search(s):
            self.denied_record = True
            return "says it has no record of that period, and the record of it is in hand"
        if self._period_window:
            named = days_named(s, self._period_window)
            missing = [d for d in named if d not in self._period_days]
            if missing and len(missing) == len(named):
                for d in missing:
                    if d not in self.unrecorded_days:
                        self.unrecorded_days.append(d)
                return f"writes about {', '.join(missing)}, when nothing is recorded that day"
        try:
            from eli.cognition.output_governor import contradicts_the_loaded_placement
            if contradicts_the_loaded_placement(s):
                return "says the model is wholly on the GPU, and the loader put part of it in RAM"
        except Exception:
            log.debug("loaded-placement check skipped", exc_info=True)
        ran = self._backed(s) or self._states_what_is_true(s)
        if not ran and _CHANGING_ITSELF.search(s):
            return "says it is changing its own code, and nothing did"
        if not ran and _ENABLED_CLAIM.search(s):
            return "reports a change nothing made"
        if _WRITES_TO_MEMORY.search(s) and not (set(self._ran()) & _memory_writing_actions()):
            return "says it wrote something to its memory, and nothing did"
        if not ran and _says_it_acted(s):
            self.claimed_acting = True
            return "says it did or is doing something, and nothing was carried out this turn"
        if not ran and _STANDING_PROMISE.search(s) and _SAYS_IT_DID.search(s):
            return "says it changed something so that it will not happen again, and nothing was changed"
        if not ran and _STANDING_PROMISE.search(s) and _COMMITS.search(s) and (
                self.about_self or _names_one_of_its_acts(s)):
            return "promises a standing change a reply cannot make"
        left = _states_a_thing_left(s)
        if left and not self._state_on_record(left):
            return f"says {left[0]} is {left[1]}, and nothing read that"
        if _REPORTS_SEEING.search(s) and not self._looked():
            return "says what the screen or an image shows, and nothing looked at one"
        try:
            from eli.cognition.query_planner import wrong_day_now
            wrong_day = "" if self._writing else wrong_day_now(s)
        except Exception:
            log.debug("weekday check skipped", exc_info=True)
            wrong_day = ""
        if wrong_day:
            return wrong_day
        nowhere = _put_nowhere(s, self._evidence) or (self._locating and _invented_path(s, self._evidence))
        if nowhere:
            self.claimed_acting = True
            return f"says a file is at {nowhere}, and nothing is there"
        if _FROM_A_LOOKUP.search(s) and not _carried_out_before() and not self._facts.get("escalation") and not any(
                _LOOKUP_ACTION.search(str(a)) for a in self._facts.get("executed_actions") or []):
            return "says its answer comes from a search, and none ran this turn"
        if self._yes_unbacked and _SAYS_YES.match(s):
            return "says yes to what the record does not show"
        if _INVENTED_SELF_METRIC.search(s) and (self.about_self or _SPEAKS_OF_ITS_OWN.search(s)) and not all(
                _figure_in(n, u, self._evidence) or _measured_figure_holds(s, n, u) for n, u in _figures_of(s)):
            return "states a figure about its own work that nothing recorded"
        # What ELI says of its own workings is held to what it has and what was recorded, whatever the
        # question was: on a turn about ELI every sentence; on any other, the ones that speak of its
        # own parts or records ("there are no `VR_JOIN` events recorded", "my internal routing ...").
        own = self.about_self or speaks_of_its_own_workings(s)
        if not own:
            return ""
        name = _invented_identifier(s, self._evidence)
        if name:
            return f"names `{name}`, which ELI does not have"
        path = _invented_path(s, self._evidence)
        if path:
            return f"names {path}, which does not exist"
        if not ran and _INVENTED_FIX.search(s) and not any(
                w.lower() in known_identifiers() for w in re.findall(r"\b[A-Z][A-Z_]{3,}\b", s)):
            return "proposes a fix ELI has no action for"
        explains = bool(_INTERNALS.search(s) or _EXPLAINS.search(s))
        if explains and (self._fault or speaks_of_its_own_workings(s) or (self.about_self and _A_PART.search(s))):
            if not self._record:
                self._load_record()
            if self._record:
                term = _unrecorded_internal(s, self._record_and_asked)
                if term:
                    return f"gives '{term}' as a cause, which the record does not show"
                if _EXPLAINS.search(s):
                    share = _supported(s, self._record_stems)
                    if share < _SUPPORT_NEEDED:
                        return f"explains a cause the record does not carry ({share:.0%} of it is there)"
        if self._last_dropped and len(s.split()) <= 30 and (_OFFER.search(s) or _FOLLOWS.search(s)):
            return "rests on a claim that was dropped"
        return ""

    def _state_on_record(self, left: tuple) -> bool:
        """Whether what was read or recorded says the thing was left in that state: the result of an
        action this turn (`action_results`, what each one said, when the executor notes it), the rest of
        the message's results, the record, or the evidence."""
        state = str(left[1]).lower()
        said = [str(r.get("said") or r.get("response") or "") for r in (self._facts.get("action_results") or [])
                if isinstance(r, dict)]
        agreed = _agreed_done()
        if agreed:
            said.append(str(agreed.get("done") or ""))
        # not the question: "is shuffle off now?" holds the very word
        where = "\n".join(said + [self._raw_evidence, self._record]).lower()
        return bool(re.search(r"\b" + re.escape(state) + r"\b", where))

    def _looked(self) -> bool:
        """Whether anything looked at the screen or a picture this turn (or in the rest of the message),
        or an ambient glance is in what the model was given (runtime.conversation_thread labels it)."""
        return any(_LOOKED.search(a) for a in self._ran()) or "ambient screen glance" in self._evidence

    def drop(self, unit: str) -> None:
        """A unit dropped before it was fed (a patch presented from a reply): a "Done." held back
        before it was the report of that, and goes with it."""
        self.dropped.append(unit.strip())
        if self._lead is not None:
            self.dropped.append(self._lead)
            self._lead = None
            self.claimed_acting = True
        self._last_dropped = True

    def feed(self, unit: str) -> List[str]:
        """The text to serve now for this unit: nothing, the unit, or a held heading and the unit."""
        out = self._feed(unit)
        for piece in out:
            if piece:
                self._line_open = not piece.endswith("\n")
        return out

    def _dropped(self, unit: str) -> List[str]:
        self.dropped.append(unit)
        # for the failure record: a reply that claimed what did not happen is a failure
        from eli.kernel.request_context import note_turn_fact
        note_turn_fact("claims_dropped", len(self.dropped))
        note_turn_fact("claim_dropped", " ".join(unit.split()))
        # the dropped sentence ended its line: end the line for what was served before it
        return ["\n"] if (self._line_open and "\n" in unit[len(unit.rstrip()):]) else []

    def _feed(self, unit: str) -> List[str]:
        if not unit:
            return []
        if not unit.strip():
            return [unit] if (self._heading is None and self._lead is None) else []
        if self._lead is not None:
            # a "Done." held back: what follows says whether it was a report
            lead, self._lead = self._lead, None
            if _says_it_acted(unit) or _put_nowhere(unit, self._evidence):
                self.dropped.append(lead)
                self.claimed_acting = True
                return self._unit(unit)
            return self._unit(lead) + self._unit(unit)
        if _DONE_LEAD.match(unit) and not (self._ran_at_start or self._facts.get("executed_actions")):
            self._lead = unit
            return []
        return self._unit(unit)

    def _unit(self, unit: str) -> List[str]:
        if _HEADING.match(unit):
            out = [] if (self._heading is None or self._dropped_under_heading) else [self._heading]
            # a heading can carry the invention too ("The Temporal Drift"); its section stands without it,
            # unless the heading is a day with nothing recorded: then everything under it was made up
            titled = self.why(unit)
            self._skip_section = bool(titled) and titled.startswith("writes about ")
            if titled:
                out += self._dropped(unit)
            self._heading, self._dropped_under_heading, self._last_dropped = (None if titled else unit), False, False
            return out
        if self._period_window and _is_day_label(unit, self._period_window):
            # a day label written inline ("... --- **Wednesday, 7 October**") opens a section like a heading
            self._skip_section = all(d not in self._period_days for d in days_named(unit, self._period_window))
        reason = self.why(unit) or ("sits under a day with nothing recorded" if self._skip_section else "")
        # a lead saying where things are holds the list under it to the disk ("lives in two places:")
        if _LOCATING_LEAD.search(unit.rstrip()):
            self._locating = True
        elif self._locating and not _LIST_ITEM.match(unit):
            self._locating = False
        if reason:
            log.debug("[SELF-CLAIM] dropped a sentence that %s: %s", reason, unit.strip()[:100])
            self._dropped_under_heading = True
            self._last_dropped = True
            return self._dropped(unit)
        self._last_dropped = False
        self.kept_words += len(unit.split())
        out = [unit] if self._heading is None else [self._heading, unit]
        self._heading = None
        return out

    def close(self) -> List[str]:
        out = [] if (self._heading is None or self._dropped_under_heading) else [self._heading]
        self._heading = None
        if self._lead is not None:
            lead, self._lead = self._lead, None
            if self.kept_words:
                out.append(lead)       # it closes something the reply did say
            else:
                self.dropped.append(lead)
                self.claimed_acting = True
        return out

    def fell_short(self) -> bool:
        """Too little true was left to serve, it said it had done what was asked and had not, or it
        denied the record of a period that is in hand: the model says it again from what is true
        (with the period's lines, which are on the turn), and the record's own lines are only for
        when it cannot."""
        return bool(self.dropped) and not self.unrecorded_days and (
            self.kept_words < 4 or (self.claimed_acting and self.asked_to_act) or self.denied_record)

    def afterword(self) -> str:
        """What follows a reply that lost sentences: the record when little or nothing true was
        left, a line saying causes were left out when several were, otherwise nothing."""
        if not self.dropped:
            return ""
        if self.denied_record:
            # the record it denied, served in its place: the period log's own lines
            lines = self._facts.get("_period_lines") or []
            if lines:
                return ("\n\n" if self.kept_words else "") + "What I have for that period:\n" + "\n".join(f"- {x}" for x in lines)
        if self.claimed_acting:
            # Said plainly when the user asked ELI to do something and was told it had happened.
            # Not under a reply to a greeting or a question: "how are you doing this evening?" got
            # "Nothing was carried out in this turn." — nobody had asked for anything to be done.
            if self.asked_to_act or not self.kept_words:
                return ("\n\n" if self.kept_words else "") + "Nothing was carried out in this turn."
            return ""
        if self.unrecorded_days:
            today = time.strftime("%Y-%m-%d")
            days = ["earlier today" if d == today else
                    time.strftime("%A %d %B", time.strptime(d, "%Y-%m-%d")).replace(" 0", " ") for d in self.unrecorded_days]
            if days == ["earlier today"]:
                return "\n\nNothing is recorded earlier today."
            return "\n\nNothing is recorded on " + ", ".join(days[:-1]) + (" or " if len(days) > 1 else "") + days[-1] + "."
        lines = self._facts.get("_record_lines") or []
        if self.kept_words < 4:
            if self.about_self and lines:
                return (("\n" if self.kept_words else "")
                        + "I won't guess at a cause or promise a change a reply can't make. This is what the "
                          "audit ledger holds for the last turns:\n" + "\n".join(f"- {x}" for x in lines[-4:]))
            return "" if self.kept_words else "".join(self.dropped)  # never an empty reply
        # What is left stands by itself. The rows pasted under it read as "a random data dump" to the
        # person who was complaining; they are in front of the model already, for it to answer from.
        return ""


_BOUNDARY = re.compile(r"[.!?]+[\"'’”)\]*_]*\s+|\n")


def _next_unit(buf: str, final: bool = False):
    """(unit, rest) for the first complete sentence or line in `buf`, or (None, buf)."""
    at = 0
    while True:
        m = _BOUNDARY.search(buf, at)
        if m is None:
            return (buf, "") if (final and buf) else (None, buf)
        # "1." and "- " open a list item; they are not a sentence of their own.
        if m.group(0) != "\n" and _LIST_MARK.match(buf[:m.end()]):
            at = m.end()
            continue
        return buf[:m.end()], buf[m.end():]


def _is_day_label(unit: str, window: Any) -> bool:
    """A short unit that only names a day ("**Wednesday, 7 October 2026**"), not a sentence about it."""
    words = re.findall(r"[A-Za-z0-9]+", str(unit or ""))
    return 1 <= len(words) <= 6 and bool(days_named(unit, window))


def _stops_where_it_says_more_follows(text: str) -> bool:
    from eli.cognition.output_governor import announces_what_follows
    return announces_what_follows(text)


def _carried_out_before() -> bool:
    """A turn nested in one that has already carried the user's commands out (the rest of a
    message of commands and talk, or the written part of an agreed offer)."""
    return _agreed_done() is not None


def _turn_context() -> Dict[str, Any]:
    try:
        from eli.kernel.request_context import turn_facts_var
        return turn_facts_var.get() or {}
    except Exception:
        return {}


# "Here is the patch for `router_enhanced.py`", "**File:** .../eli/execution/router_enhanced.py",
# "--- a/eli/...": a change to ELI's own code presented from a reply. Live, ELI "patched" a line that
# did not exist, with invented line numbers. Without an action that changed code this turn, the words
# and the code block after them are not served.
_INVENTED_PATCH = re.compile(
    r"\b(?:here(?:'s|’s| is) (?:the|a|my|your) (?:patch|diff|fix|code change|corrected (?:code|version))\b"
    r"|\*\*file:?\*\*:?\s*\S*\beli[/\\]|^\s*(?:---|\+\+\+)\s+[ab]/eli/|\bthe patch (?:is|for|to)\b)", re.I | re.M)
_CHANGES_CODE = re.compile(r"PATCH|IMPROVE|FIX|REPAIR|EDIT_FILE|WRITE_FILE|APPLY", re.I)
NO_PATCH_NOTE = ("(I don't change my own code from a reply: no patch was made. The self-improvement cycle "
                 "changes code from a verified failure, with its tests.)")


def _patch_backed(check: "_Check") -> bool:
    ran = [str(a) for a in (check._facts.get("executed_actions") or [])]
    return any(_CHANGES_CODE.search(a) for a in ran)


def _checked(chunks: Iterable[str], check: _Check):
    """Each sentence held until it ends, then served or dropped. Text inside ``` fences passes
    straight through, unless the words before it presented a patch to ELI's own code that nothing
    made: then the block goes with them."""
    buf = ""
    in_code = False
    skip_code = False
    recent = ""                 # the words since the last code block, for what a block is presented as
    for piece in chunks:
        buf += str(piece or "")
        while buf:
            if in_code:
                end = buf.find("```")
                if end < 0:
                    if len(buf) > 2:  # the tail could be the start of the closing fence
                        if not skip_code:
                            yield buf[:-2]
                        buf = buf[-2:]
                    break
                if skip_code:
                    yield NO_PATCH_NOTE
                else:
                    yield buf[:end + 3]
                buf, in_code, skip_code, recent = buf[end + 3:], False, False, ""
                continue
            unit, rest = _next_unit(buf)
            fence = buf.find("```")
            if fence >= 0 and (unit is None or fence < len(unit)):
                before = buf[:fence]
                recent = (recent + before)[-600:]
                if _INVENTED_PATCH.search(recent) and not _patch_backed(check):
                    check.drop(before)
                    skip_code = True
                    yield from check.close()
                else:
                    yield from check.feed(before) + check.close()
                    yield "```"
                    check.code_served = True
                buf, in_code = buf[fence + 3:], True
                continue
            if unit is None:
                break
            buf = rest
            recent = (recent + unit)[-600:]
            if _INVENTED_PATCH.search(unit) and not _patch_backed(check):
                check.drop(unit)                          # "Here is the patch for ...": not served
                continue
            yield from check.feed(unit)
    if buf:
        if in_code:
            yield buf
        else:
            yield from check.feed(buf)
    yield from check.close()


def not_said_yet(again: str, served: str) -> str:
    """`again` without the sentences already served. Told to go on from what it had said, the model
    wrote the last paragraph out again word for word, and the person read it twice."""
    def _plain(unit: str) -> str:
        return re.sub(r"[\W_]+", " ", unit.lower()).strip()
    said = {_plain(u) for u in re.split(r"(?<=[.!?])\s+|\n+", str(served or "")) if _plain(u)}
    pieces = re.split(r"((?<=[.!?])[ \t]+|\n+)", str(again or ""))
    kept = [unit + (pieces[i + 1] if i + 1 < len(pieces) else "")
            for i, unit in ((i, pieces[i]) for i in range(0, len(pieces), 2))
            if not (_plain(unit) and _plain(unit) in said)]
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()


# A name in a sentence: a capitalised word that does not open the sentence, or a run of them.
_A_NAME = re.compile(r"(?<!^)(?<![.!?]\s)(?<![.!?]\s\s)\b([A-Z][\w&'’-]+(?:\s+[A-Z][\w&'’-]+)*)")


def _specifics(sentence: str) -> List[str]:
    """What a sentence names that only facts could stand behind: figures, code names, paths, names."""
    found = [a or b for a, b in _SPECIFIC.findall(sentence)] + _PATH.findall(sentence)
    found += [n for n in _A_NAME.findall(sentence.strip()) if n not in ("I", "I'm", "I've", "I'll", "I'd", "ELI")]
    return [f.strip(" .,;:") for f in found if f.strip(" .,;:")]


def only_what_it_was_given(text: str, facts: str, asked: str = "") -> str:
    """`text`, a model's wording of `facts`, without the sentences that add to them: any that names a
    figure, code name, path or name that neither the facts nor what was asked holds. "" when nothing
    is left. A wording says the facts in other words; it does not bring new ones."""
    held = f"{facts}\n{asked}".lower()
    kept: List[str] = []
    for unit in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
        if not unit.strip():
            continue
        extra = [x for x in _specifics(unit) if x.lower() not in held]
        if extra:
            log.debug("[SELF-CLAIM] dropped a sentence that adds %s to what it was given: %s", extra[0], unit[:100])
            continue
        kept.append(unit.strip())
    return " ".join(kept).strip()


def holds_up(text: str, evidence: str = "") -> bool:
    """Whether every sentence of `text` gets through the claim check."""
    check = _Check(evidence)
    list(_checked([text], check))
    return not check.dropped


def _not_said_already(chunks: Iterable[str], said: str):
    """`chunks` without the sentences already in `said`, as they come: told to go on, the model wrote
    the last paragraph out again word for word (see not_said_yet)."""
    def _plain(unit: str) -> str:
        return re.sub(r"[\W_]+", " ", unit.lower()).strip()
    before = {_plain(u) for u in re.split(r"(?<=[.!?])\s+|\n+", str(said or "")) if _plain(u)}
    buf = ""
    for piece in chunks:
        buf += str(piece or "")
        while True:
            unit, rest = _next_unit(buf)
            if unit is None:
                break
            buf = rest
            if not (_plain(unit) and _plain(unit) in before):
                yield unit
    if buf and not (_plain(buf) and _plain(buf) in before):
        yield buf


def gate_stream(chunks: Iterable[str], evidence: str = "", rewrite: Optional[Callable[[List[str], str], str]] = None,
                instructions: str = "", go_on: Optional[Callable[[str], Iterable[str]]] = None):
    """The claim check on a live stream. When too little true is left, `rewrite(dropped, served)` asks
    the model again from what actually happened; the record's own line is for when it can't. When the
    reply stops on a sentence that only announces what follows ("Yes. Here's why."), `go_on(served)`
    asks the model to go on from what it said, and that goes through the same check."""
    check = _Check(evidence, instructions)
    served: List[str] = []
    for out in _checked(chunks, check):
        served.append(out)
        yield out
    said = "".join(served)
    if go_on is not None and not check.code_served and _stops_where_it_says_more_follows(said):
        opened = False
        for out in _checked(_not_said_already(go_on(said) or [], said), check):
            if not out:
                continue
            if not opened:
                if not out.strip():
                    continue
                opened = True
                out = ("" if said.endswith("\n") else "\n\n") + out.lstrip()
            served.append(out)
            yield out
    if check.code_served:
        return
    if rewrite is not None and check.fell_short():
        again = not_said_yet(str(rewrite(list(check.dropped), "".join(served)) or ""), "".join(served))
        if again and holds_up(again, evidence):
            yield ("\n\n" if check.kept_words else "") + again
            return
    after = check.afterword()
    if after:
        yield after


def ask_again(dropped: List[str], served: str) -> str:
    """Notes on the turn that a reply lost what it said, for the engine to ask the model again from
    what actually happened (kernel/stages/generation.py reads `reply_fell_short`). The checks that
    run on a buffered reply cannot reach the model themselves; several may drop from one reply, so
    what each dropped is kept, and `served` is what is left after the latest. "" for the caller."""
    from eli.kernel.request_context import turn_facts_var
    facts = turn_facts_var.get()
    if facts is None:
        return ""
    prior = facts.get("reply_fell_short") or {}
    facts["reply_fell_short"] = {"dropped": list(prior.get("dropped") or []) + [str(d).strip() for d in dropped if str(d).strip()],
                                 "served": str(served or "")}
    return ""


def drop_invented_self_claims(text: str, evidence: str = "") -> str:
    """`text` without sentences that invent ELI's settings, files, causes or fixes, or report
    changes it didn't make. Code blocks are left alone. Never returns an empty reply."""
    body = str(text or "")
    if not body.strip():
        return body
    kept = "".join(gate_stream([body], evidence, rewrite=ask_again))
    kept = re.sub(r"[ \t]+\n", "\n", kept)
    return re.sub(r"\n{3,}", "\n\n", kept).strip() or body

