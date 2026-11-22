"""Shared patterns for user correction / dispute turns: the one reader of what a message says of
ELI's last reply.

Used by the kernel query classifier and the deterministic router so both agree on when a message is
challenging ELI's prior answer, and by everything that learns from the next turn (the engine's
settlement, self-improvement's record of rejected replies, the claim check, the tone profile), which
asks ``reply_verdict``. The complaint (self_claims), redo (action_commitment) and challenge
(diagnostic_patterns) vocabularies live here and those modules import them.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Tuple

# Words that say the last answer was wrong: it answered something not asked, or said what is not so.
_DISPUTES = (
    r"i did not ask|i didn't ask|not what i asked|that's not what i asked|"
    r"that is not what i asked|what are you talking about|what are you talking bout|"
    r"answer properly|"
    r"data dump|stop giving me data|why did you send|did not ask for|"
    r"that's not true|that is not true|i didn't say|"
    r"i did not say|where did you get|why do you say|never said|"
    r"never told you|didn't mention|did not mention|"
    r"why did you lie|you lied|that was a lie|why did you say|"
    r"that was a question|that is a question|that's a question|"
    r"what is your problem|what's your problem|"
    r"you misunderstood|you misunderstand|you missed the point|missed the point|not what i meant|"
    r"that's not it|that is not it"
)
# Words that open a challenge as often as a plain question or the user's own news ("what do you mean
# by entropy here?", "I never knew that", "I didn't have time to try it"). They mark a turn for the
# kernel to look at what was said (routing, is_correction_query), never a verdict that the last
# answer was wrong (is_answer_correction): every one of those settled a reply wrong and noted a
# surprise in what ELI learns from.
_ALSO_QUESTIONS = r"what do you mean|what wild night|what night|i never|didn't have|did not have"

CORRECTION_QUERY_RE = re.compile(rf"\b({_DISPUTES}|{_ALSO_QUESTIONS})\b", re.IGNORECASE)

# Meta-conversation: user is challenging ELI's prior reply or asking ELI to
# elaborate on something ELI just said (not a new factual lookup).
META_CONVERSATION_RE = re.compile(
    r"(?:^|\b)("
    r"like what\??|"
    r"you asked me\b|"
    r"i responded with|"
    r"not paying attention|paying attention to|previous answers?|"
    r"we just established|rolling summary|labs tab|"
    r"not answering my question|answering my question properly|"
    r"personal response|more of a personal|"
    r"why (?:is|are) my gui|gui not showing|grounding and agent|agent scores?|"
    r"what the fuck are you talking about|where are you getting|"
    r"no you (?:were not|wasn't|didn't|did not|fucking were not)"
    r")(?:\b|$)",
    re.IGNORECASE,
)

CONTINUITY_COMPLAINT_RE = re.compile(
    r"\b(?:"
    r"not paying attention|paying attention to|previous answers?|"
    r"we just established|rolling summary|labs tab|lost the thread"
    r")\b",
    re.I,
)


def is_continuity_complaint(text: str) -> bool:
    return bool(CONTINUITY_COMPLAINT_RE.search(str(text or "")))


# The user pointing back at something already said ("your long response above", "that
# conversation", "the response from you"), not asking about the outside world. Live: "can you
# tell me when that was?" about that conversation was web-searched, and "what day is that from"
# about ELI's long reply was answered from the last exchange only.
EARLIER_REPLY_RE = re.compile(
    r"\b(?:that|our|last|previous|earlier)\s+(?:\w+\s+)?(?:conversation|chat|exchange)\b"
    r"|\b(?:your|my|that|the|this)\s+(?:(?:long|last|previous|earlier|first|whole)\s+)?"
    r"(?:reply|response|answer|message|list)\s+(?:above|earlier|before|from\s+(?:above|earlier|before|you))\b"
    r"|\b(?:your|my)\s+(?:long|last|previous|earlier|first)\s+(?:reply|response|answer|message|list)\b"
    r"|\b(?:reply|response|answer|message)\s+from\s+you\b"
    # by count: "your message from 2 messages ago", "a few replies back" (live, ELI was shown that reply
    # cut short, missed what it had said at the end, and told the user they were hallucinating)
    r"|\b(?:\d+|one|two|three|four|five|a\s+few|a\s+couple(?:\s+of)?)\s+(?:messages?|replies|repl(?:y|ies)|responses?|answers?|turns?)\s+(?:ago|back|up|before)\b"
    # this conversation's last few: "check the last, say 7-8 messages"
    r"|\b(?:last|previous|past|recent)\b,?\s*(?:(?:say|about|around|like|the)\b\s*,?\s*)?(?:\d+(?:\s*[-–]\s*\d+)?|few|couple(?:\s+of)?|several)\s+(?:messages?|replies|responses|exchanges|turns)\b",
    re.I,
)


def refers_to_earlier_reply(text: str) -> bool:
    return bool(EARLIER_REPLY_RE.search(str(text or "")))


# User correcting ELI's recency ("that was today", "not last week").
TEMPORAL_CORRECTION_RE = re.compile(
    r"\b(?:was|is)\s+(?:today|yesterday|this morning|this evening|just now|earlier today)\b"
    r"|\bnot\s+last\s+week\b"
    r"|\bthat\s+was\s+(?:today|yesterday)\b",
    re.I,
)

# Complaints that ELI failed to read time/timestamps in the conversation.
META_CAPABILITY_COMPLAINT_RE = re.compile(
    r"\b(?:can(?:'t| not)|cannot|won't|will not)\s+(?:you\s+)?(?:read|parse|see|get)\b"
    r".{0,50}\b(?:timestamp|time|date|clock)\b"
    r"|\b(?:read|parse)\s+(?:a\s+)?(?:fucking\s+)?timestamp\b"
    r"|\bcan you not read\b",
    re.I,
)

# User correcting ELI's claimed model identity ("you are GLM", "no you are not Ornith").
# A dispute about which model ELI runs names the model, or is the whole reply ("no you are not").
# "You are not" anywhere matched before: "...but you are not properly searching for songs" got
# the runtime-status dump. The names are every model family ELI knows how to run (the architecture
# catalogue, eli/cognition/model_identity), the loaded model's own names (its GGUF general.name,
# basename and architecture) and the names users give models it has had; a fixed list missed every
# newer family and every fine-tune's name.
_SEEN_NAMES = ("ornith", "claude", "gpt", "nemotron")


def _model_names() -> Tuple[str, ...]:
    names = set(_SEEN_NAMES)
    from eli.cognition.model_identity import ARCHITECTURE_TO_FAMILY
    names |= {re.sub(r"\d+.*$", "", a) or a for a in ARCHITECTURE_TO_FAMILY} | set(ARCHITECTURE_TO_FAMILY.values())
    names |= _loaded_model_names()
    return tuple(sorted(n for n in names if n and n.isascii()))


_LOADED_NAMES: Dict[str, frozenset] = {}


def _loaded_model_names() -> frozenset:
    """The words of the loaded model's own names in its GGUF header, read once per model file."""
    try:
        from eli.cognition import gguf_inference
        path = gguf_inference.loaded_model_path()
        if not path:
            return frozenset()
        key = f"{path}:{os.path.getmtime(path)}"
        if key not in _LOADED_NAMES:
            from eli.cognition.model_load_diagnostics import gguf_metadata
            meta = gguf_metadata(path) or {}
            words = set()
            for field in ("general.name", "general.basename", "general.architecture"):
                words |= {w for w in re.findall(r"[a-z][a-z]+", str(meta.get(field) or "").lower()) if len(w) > 2}
            _LOADED_NAMES.clear()
            _LOADED_NAMES[key] = frozenset(words)
        return _LOADED_NAMES[key]
    except Exception:  # noqa: BLE001 - no model to read: the families ELI knows stand
        return frozenset()


def _identity_dispute_re(names: Tuple[str, ...]) -> "re.Pattern":
    # a name as itself or with its version ("qwen2.5", "phi-4", "gpt-4o"), never inside a longer word
    alts = "|".join(re.escape(n) + r"(?:[-\d.][\w.-]*)?" for n in names)
    words = rf"(?:{alts}|(?:the|that|a|this)\s+model|running\s+\w+|loaded|using\s+\w+)"
    return re.compile(
        rf"\byou(?:'re| are)\s+not\s+(?:\w+\s+){{0,2}}?{words}\b"
        rf"|\byou(?:'re| are)\s+(?:{alts})\b"
        rf"|\b(?:wrong model|not (?:{alts}))\b"
        r"|\bcheck(?:\s+the|\s+your)?\s+model\s+again\b"
        r"|\blook at (?:the|your) model\b"
        r"|^\W*(?:no\W+)?you(?:'re| are)\s+not\W*$",
        re.I)


_DISPUTE_RES: Dict[Tuple[str, ...], "re.Pattern"] = {}


def _identity_dispute() -> "re.Pattern":
    names = _model_names()
    if names not in _DISPUTE_RES:
        _DISPUTE_RES.clear()
        _DISPUTE_RES[names] = _identity_dispute_re(names)
    return _DISPUTE_RES[names]


MODEL_IDENTITY_DISPUTE_RE = _identity_dispute_re(_model_names())

# The user saying what ELI said of them is not so.
_BIOGRAPHICAL_DISPUTES = (
    r"that's not true|that is not true|i didn't say|"
    r"i did not say|where did you get|why do you say|what are you talking about|"
    r"never said|never told you|didn't mention|did not mention|you said i"
)
BIOGRAPHICAL_DISPUTE_RE = re.compile(
    rf"\b({_BIOGRAPHICAL_DISPUTES}|{_ALSO_QUESTIONS}|been through|waking up late|wild night)\b",
    re.IGNORECASE,
)
# What says the last answer was wrong, and nothing that is as often a question or the user's news.
_ANSWER_DISPUTE_RE = re.compile(rf"\b(?:{_DISPUTES}|{_BIOGRAPHICAL_DISPUTES})\b", re.IGNORECASE)


NEWS_FETCH_COMPLAINT_RE = re.compile(
    r"\b(?:"
    r"guessing|guess(?:ing)?|made\s+up|hallucin(?:at(?:e|ing|ed))?|"
    r"didn't\s+fetch|did\s+not\s+fetch|not\s+fetch(?:ing)?\s+(?:the\s+)?(?:actual|real)|"
    r"fake\s+story|invented|you\s+made\s+that\s+up|getting\s+ahead\s+of\s+yourself|"
    r"come\s+with\s+(?:some\s+)?(?:facts?|info)|at\s+least\s+come\s+with"
    r")\b",
    re.I,
)


def is_news_fetch_complaint(text: str) -> bool:
    s = str(text or "")
    if not NEWS_FETCH_COMPLAINT_RE.search(s):
        return False
    return bool(re.search(
        r"\b(?:news|story|stories|article|headline|fetch|deeper|briefing)\b", s, re.I))


# "No, the ...", "nope, that ...": a correction of what was said. Not "no, I ...", which opens agreement
# as often ("No I think you're right").
FACT_CORRECTION_RE = re.compile(r"(?:^|[.!?]\s+)(?:no|nope|nah)[,.!]?\s+(?:the|that|it|those|this)\b", re.I)

# "that's not right", "thats wrong", "you got that wrong", "that is incorrect": the last answer disputed.
# Without the apostrophe ("thats not right, there must be an error with your memory database") none of
# the patterns above saw it, and the memory-runtime lock answered with the subsystem's internals.
DISPUTE_RE = re.compile(
    r"\b(?:that|it|this)(?:['’]?s|\s+is|\s+was)\s+(?:(?:just|simply|totally|completely|all)\s+)?"
    r"(?:not\s+(?:right|correct|true|accurate)|wrong|incorrect|false|inaccurate|made\s+up)\b"
    r"|\byou(?:['’]?re|\s+are|\s+were)\s+(?:wrong|mistaken|incorrect)\b"
    r"|\byou\s+got\s+(?:it|that|this|them)\s+(?:all\s+)?wrong\b|\bwrong\s+answer\b",
    re.I)


def is_answer_correction(text: str) -> bool:
    """The user is telling ELI its last answer was wrong (not a complaint about the app itself).
    This settles what ELI learns from (the reply's chance, the memories shown, the claims about the
    user), so it reads only what says so: a clarifying question or the user's own news is not it."""
    s = str(text or "")
    return bool(_ANSWER_DISPUTE_RE.search(s) or RUNTIME_RECHECK_CORRECTION_RE.search(s)
                or FACT_CORRECTION_RE.search(s) or DISPUTE_RE.search(s))


# The user saying the last answer was right: thanks, or plain agreement, at the start of their next
# message. Read at the start only, so "thanks, now search for X" counts and a long message that
# happens to contain "right" is read for what it is. What is NOT here on purpose: "ok"/"yes" alone,
# which answer a question as often as they endorse an answer (eli/runtime/grounded_remediation.py).
# The whole list is also what a closing remark is made of (eli/cognition/chat_grounding_gate.py).
ENDORSEMENT_WORDS = (
    r"thanks|thank you|thankyou|ta|cheers|great|perfect|brilliant|excellent|spot on|exactly|"
    r"that's right|that is right|correct|that's it|that's the one|nice one|lovely|good answer|well done|"
    r"got it|cool|nice|yes,?\s+(?:that's|that is)\s+(?:right|it|correct)"
)
ENDORSEMENT_RE = re.compile(rf"^\s*(?:ok(?:ay)?[,.!\s]+)?(?P<word>{ENDORSEMENT_WORDS})\b", re.IGNORECASE)
# Of those, the ones that open a new request, a sarcastic aside or a remark as often as a verdict
# ("cool, what's the weather tomorrow", "great, another wrong answer", "got it wrong again", "ta-da"):
# a closing remark, never a sign that the last answer was right.
_ACKNOWLEDGING_ONLY = frozenset({"ta", "great", "got it", "cool", "nice"})


def is_endorsement(text: str) -> bool:
    """True when the message opens by saying the previous answer was right."""
    s = str(text or "").strip()
    m = ENDORSEMENT_RE.match(s) if s else None
    if m is None or m.group("word").lower() in _ACKNOWLEDGING_ONLY:
        return False
    return not is_answer_correction(s)


# ── complaints, redos and challenges about ELI's last reply ──────────────────────────────────

# Saying what ELI just did: "then you just stopped spotify", "you just maximised your GUI, not the
# browser". Live, the first was answered with "a background housekeeping routine misfired ... I have
# isolated the media player handler", none of which was checked: no pattern took it for a complaint.
_YOU_JUST_DID = (r"\byou\s+(?:(?:just|then|only|also|somehow|suddenly|actually|even|literally|fucking|bloody|damn)\s+)+"
                 # a past form, not a present one that happens to end the same way
                 r"(?!(?:need|want|feed|speed|proceed|exceed|could|should|would|point|count|print)\b)\w+(?:ed|pt|ld|nt)\b"
                 r"|\bthen\s+you\s+(?!(?:can|could|should|will|would|may|might|need|want|have|just)\b)\w+")

# A complaint aimed at ELI. The worst invented diagnosis on record ("the embedding model decayed
# the context window's relevance") answered one of these, not a question.
_COMPLAINT_RE = re.compile(
    r"\byou\s+(?:do\s+not|don'?t|did\s+not|didn'?t)\s+(?:need|have)\s+to\b"
    r"|\byou\s+should(?:\s+not|n'?t)?\s+(?:have|be|know|check|remember|log|stop)\b"
    r"|\byou\s+(?:keep|kept|still|always|never)\s+(?:\w+ing|get|got|forget|ignore|search|answer|remember|listen|say|do|make)\b"
    r"|\b(?:stop|quit)\s+(?:search\w*|lying|making|guessing|inventing|hallucinat\w+|ignoring)\b"
    r"|\byou(?:'re|’re| are| were)\s+(?:wrong|lying|making|not listening|useless|broken|ignoring)\b"
    r"|\byou\s+(?:forgot|ignored|missed|lied|made\s+(?:that|it|this|shit|stuff)\s+up|got\s+(?:that|it|this)\s+wrong|hallucinat\w+)\b"
    r"|\bthat(?:'s|’s| is| was)\s+(?:wrong|incorrect|bullshit|not\s+(?:right|true|what\s+i\s+(?:asked|said)))\b"
    r"|\bi\s+asked\s+you\b|\bwhy\s+the\s+(?:fuck|hell)\b"
    r"|\bi\s+(?:\w+\s+)?(?:told|gave|sent)\s+you\b"
    # an insult aimed at ELI is a complaint about its last reply ("you fucking idiot, you are talking shit")
    r"|\byou(?:'re|’re| are)?\s+(?:\w+\s+){0,2}(?:idiot|moron|stupid|useless|dumb|incompetent|clueless|clown|slow)\b"
    r"|\b(?:talking|talk|spouting|chatting)\s+(?:shit|bollocks|nonsense|rubbish|crap|garbage)\b"
    # a correction of what ELI answered: "I did not ask about RAM", "I asked why it took so long"
    r"|\bi\s+(?:did\s+not|didn'?t|never|was\s+not|wasn'?t)\s+ask(?:ed|ing)?\b"
    r"|\bi\s+(?:only\s+|just\s+)?asked\s+(?:you\s+)?(?:why|what|how|about|for|if|whether|when|where)\b"
    r"|\b(?:are|were)\s+you\s+(?:just\s+)?making\s+(?:that|this|it|stuff|things|shit)\s+up\b"
    r"|\byou\s+(?:did\s+not|didn'?t|have\s+not|haven'?t)\s+(?:actually\s+|even\s+)?\w+"
    r"|\byou\s+never\s+(?!know\b)\w+"
    r"|" + _YOU_JUST_DID,
    re.I)


# "you didn't burn the house down while I was away, did you?" is banter ending on a tag question; on
# its own the generic "you didn't <verb>" is not a complaint then (it got the audit rows as a footer)
_GENERIC_DIDNT_RE = re.compile(r"\byou\s+(?:did\s+not|didn'?t|have\s+not|haven'?t)\s+(?:actually\s+|even\s+)?\w+", re.I)
_TAG_QUESTION_RE = re.compile(r",?\s*\b(?:did|have|were|would|could|do)\s+you\s*\?+\W*$", re.I)


def complains_about_eli(text: Any) -> bool:
    t = str(text or "")
    found = [m.group(0) for m in _COMPLAINT_RE.finditer(t)]
    if not found:
        return False
    if _TAG_QUESTION_RE.search(t) and all(_GENERIC_DIDNT_RE.fullmatch(f) for f in found):
        return False
    return True


# User directives/challenges that mean "actually (re-)do the task": the caller re-runs the last real
# action instead of letting ELI chat or defend itself. Requires "again / it / that" or a doubt ("are
# you actually ...ing") so a fresh request ("check the news") isn't treated as a redo.
REDO_RE = re.compile(
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
    m = REDO_RE.search(s)
    if not m:
        return False
    # Exclude "I'll check it myself" — the USER doing it, not a directive to ELI.
    pre = s[max(0, m.start() - 25):m.start()].lower()
    if "myself" in s.lower() or re.search(r"\b(?:i|i'?ll|i'?m|we|we'?ll)\b", pre):
        return False
    return True


# A message challenging ELI.
USER_CHALLENGE_RE = re.compile(
    r"(?is)\b(?:"
    r"i\s+did(?:n'?t| not)\s+ask"
    r"|not\s+what\s+i\s+asked"
    r"|stop\s+talking\s+about"
    r"|what\s+(?:the\s+)?(?:fuck|hell|heck)"
    r"|do\s+you\s+not\s+understand"
    r"|what\s+is\s+going\s+on\s+with\s+you"
    r"|what\s+is\s+happening\s+with\s+you"
    r")\b"
)


def is_user_challenge(text) -> bool:
    return bool(USER_CHALLENGE_RE.search(str(text or "")))


# A sentence that opens by saying no ("no, I live in Lisbon now", "nope, Tuesdays"). On its own it
# is no verdict on the whole answer ("No I think you're right" opens agreement), so it never settles
# a reply (is_answer_correction leaves it out); it denies what the rest of the message speaks of,
# which is how a claim about the user is corrected (eli/kernel/engine._settle_recall_outcome).
_DENIAL_RE = re.compile(r"(?:^|[.!?]\s+)(?:no|nope|nah)\b(?![-'’])", re.I)
_SAYS_RIGHT_RE = re.compile(r"\b(?<!not )(?:you(?:'re|’re| are)\s+right|(?:that's|that is)\s+(?:right|it)|correct|"
                            r"exactly|spot on|i\s+agree)\b", re.I)


def denies(text: str) -> bool:
    """The message opens a sentence with a no and nowhere says the answer was right: a denial of
    what it goes on to speak of."""
    s = str(text or "")
    return bool(_DENIAL_RE.search(s)) and not _SAYS_RIGHT_RE.search(s)


def is_correction_query(text: str) -> bool:
    s = str(text or "")
    if is_news_fetch_complaint(s):
        return True
    if CORRECTION_QUERY_RE.search(s):
        return True
    if META_CONVERSATION_RE.search(s):
        return True
    if is_continuity_complaint(s):
        return True
    if META_CAPABILITY_COMPLAINT_RE.search(s):
        return True
    if _identity_dispute().search(s):
        return True
    if TEMPORAL_CORRECTION_RE.search(s) and re.search(
        r"\b(?:you|eli|wrong|not|can(?:'t| not)|cannot)\b", s, re.I
    ):
        return True
    return False


RUNTIME_RECHECK_CORRECTION_RE = re.compile(
    r"\b(?:that(?:'s| is| was)?\s+not\s+true|not\s+true|that(?:'s| is)\s+wrong|you'?re\s+wrong)\b",
    re.I,
)

CODEBASE_HEALTH_RE = re.compile(
    r"\bhow(?:'?s|\s+is|\s+are)\s+(?:the\s+)?(?:codebase|code\s*base|repo|repository|project|code)\b",
    re.I,
)


def is_runtime_recheck_correction(text: str) -> bool:
    """User disputing ELI's prior factual/runtime claim and asking for a re-check."""
    s = str(text or "")
    if not RUNTIME_RECHECK_CORRECTION_RE.search(s):
        return False
    return bool(re.search(
        r"\b(check again|try again|look again|verify|recheck|re-check|check(?:\s+it)?\s+again)\b",
        s,
        re.I,
    ))


def is_codebase_health_query(text: str) -> bool:
    s = str(text or "")
    if CODEBASE_HEALTH_RE.search(s):
        return True
    return bool(re.search(
        r"\b(?:codebase|code\s*base|repo|repository)\b", s, re.I
    ) and re.search(r"\b(?:health|status|state|holding up|doing)\b", s, re.I))


def is_model_identity_dispute(text: str) -> bool:
    return bool(_identity_dispute().search(str(text or "")))


def is_biographical_dispute(text: str) -> bool:
    if is_runtime_recheck_correction(text):
        return False
    return bool(BIOGRAPHICAL_DISPUTE_RE.search(str(text or "")))


WEB_SEARCH_REQUEST_RE = re.compile(
    r"\b("
    r"search the web|search online|look it up|google it|check online|"
    r"go search|web search|search for|look online|find online"
    r")\b",
    re.I,
)

# Routed executor actions must not be overridden by the CORRECTION repair shortcut.
_CORRECTION_BYPASS_ACTIONS = frozenset({
    "WEB_SEARCH", "WEB_FETCH", "WEB_LEARN", "SEARCH_WEB",
    "NEWS_FETCH", "GET_WEATHER",
})


def explicit_web_search_request(text: str) -> bool:
    """The user tells ELI to search the web. The same words said about a search are not a
    request: live, 'you about a website with a search for "that website" and then killed the app'
    was web-searched and answered with advice on malicious websites."""
    from eli.runtime.command_splitter import read_message
    return any(part["tells"] and WEB_SEARCH_REQUEST_RE.search(part["tells"]) for part in read_message(text))


def is_meta_conversation(text: str) -> bool:
    """True when the user is asking about ELI's own prior reply, not a new topic."""
    s = str(text or "")
    if META_CONVERSATION_RE.search(s):
        return True
    return bool(re.search(
        r"\b(that(?:'s| is| was) a question|like what\??)\b", s, re.I
    ))


def correction_shortcut_allowed(text: str, action: str) -> bool:
    """True when the kernel may run _correction_repair before the main pipeline."""
    if str(action or "").upper() in _CORRECTION_BYPASS_ACTIONS:
        return False
    if explicit_web_search_request(text):
        return False
    return True


# ── what the user said versus what they pasted ───────────────────────────────

# The arrow people put between their own words and something pasted ("here's ChatGPT's reply -->")
_PASTE_ARROW_RE = re.compile(r"\s*(?:-{2,}>|={1,2}>|→|»)\s*")
# "here's ChatGPT's reply:", "look at what it said:" — a lead-in ending in a colon or a line break
_PASTE_LEAD_IN_RE = re.compile(
    r"^.{0,200}?\b(?:here(?:'s| is| are)|this is|look at|read|check)\b.{0,120}?"
    r"\b(?:reply|replies|response|message|said|says|wrote|critique|answer|review|feedback)\b[^:\n]{0,60}?[:\n]",
    re.IGNORECASE | re.DOTALL,
)

# A report (runtime, memory, personal memory, the last response, an import audit) answers a short
# request for it. Longer than this, the user is talking, and a keyword in what they wrote is not that
# request.
SHORTCUT_MAX_CHARS = 300


def split_pasted(text: str) -> tuple[str, str]:
    """(the user's own words, what they pasted). Pasted text follows an arrow ("-->", "=>", "→") or a
    lead-in such as "here's ChatGPT's reply:". With neither, the whole message is the user's.
    The words inside a paste belong to whoever wrote them: read as the user's, a pasted ChatGPT
    critique that mentioned "runtime", "memory" and "audit ledger" was answered with runtime dumps,
    and its "I am making it more precise" was stored as a fact about the user."""
    s = str(text or "")
    m = _PASTE_ARROW_RE.search(s)
    if m and m.start() <= 600 and s[m.end():].strip():
        return s[:m.start()].strip(), s[m.end():].strip()
    m = _PASTE_LEAD_IN_RE.match(s)
    if m and len(s) - m.end() >= 200:
        return s[:m.end()].rstrip(": \n").strip(), s[m.end():].strip()
    return s.strip(), ""


def shortcut_text(text: str) -> str:
    """The words a deterministic shortcut (a status or memory report, introspection, a control
    upgrade) may read: the user's own words, never pasted ones, and nothing from a long message."""
    own, _pasted = split_pasted(text)
    return own if len(own) <= SHORTCUT_MAX_CHARS else ""


# ── what the next message says of the last reply ─────────────────────────────────────────────────
CORRECTION, COMPLAINT, REDO, ENDORSEMENT, NONE = "correction", "complaint", "redo", "endorsement", "none"


def reply_verdict(text: str) -> str:
    """What this message says of ELI's last reply, from the words above (one reader for every
    consumer): ``correction`` (it was wrong: is_answer_correction, a dispute of the model ELI said
    it runs, or of when something was), ``redo`` (do it, or do it again: is_redo_directive),
    ``complaint`` (about how ELI did it: complains_about_eli, a lost thread, a news fetch it made
    up, a timestamp it could not read, a challenge), ``endorsement`` (it was right) or ``none``
    (anything else: a new request, the user's own news, a question)."""
    s = str(text or "")
    if not s.strip():
        return NONE
    if (is_answer_correction(s) or _identity_dispute().search(s)
            or (TEMPORAL_CORRECTION_RE.search(s) and re.search(r"\b(?:you|eli|wrong|not)\b", s, re.I))):
        return CORRECTION
    if is_redo_directive(s):
        return REDO
    if (complains_about_eli(s) or is_continuity_complaint(s) or is_news_fetch_complaint(s)
            or META_CAPABILITY_COMPLAINT_RE.search(s) or is_user_challenge(s)):
        return COMPLAINT
    if is_endorsement(s):
        return ENDORSEMENT
    return NONE


def rejects_reply(text: str) -> bool:
    """The message says the last reply was wrong, or not done, or not done well."""
    return reply_verdict(text) in (CORRECTION, REDO, COMPLAINT)


# ── the next message judged against the reply, by ELI's own model ────────────────────────────────
# What ELI learns from (the reply's chance, the route, the memories and claims it rested on) is settled
# by what the user's next message says of the last reply. The words above are cues, not a reading: the
# local model reads the message against the reply it answers, held by a grammar to one of three labels
# and a confidence, with the cues given as evidence. With no model loaded, the cues decide alone.
DISPUTES, ACCEPTS, NEITHER = "disputes", "accepts", "neither"
LABELS = (DISPUTES, ACCEPTS, NEITHER)
_FROM_WORDS = {CORRECTION: DISPUTES, ENDORSEMENT: ACCEPTS}

_JUDGE_SYSTEM = (
    "You judge what a user's message says of the assistant's previous reply. Answer with one label. "
    "disputes: the message says the reply was wrong, untrue, or answered something that was not asked. "
    "accepts: the message says the reply was right or did what was wanted. "
    "neither: anything else, such as a new request, a change of subject, the user's own news, a follow-up "
    "question, or a complaint about how something was done that does not say the reply was wrong. "
    "Give your confidence in the label from 0 to 1. Output one JSON object.")


class Judged(str):
    """A label (disputes, accepts, neither) that carries how it was reached: `confidence` (the
    model's, None from the word cues), `by` ("model" or "words"), and `words` (reply_verdict)."""

    def __new__(cls, judged: dict) -> "Judged":
        out = super().__new__(cls, str((judged or {}).get("label") or NEITHER))
        out.label = str(out)
        out.confidence = (judged or {}).get("confidence")
        out.by = str((judged or {}).get("by") or "words")
        out.words = str((judged or {}).get("words") or NONE)
        return out


def judge_reply(message: str, reply: str = "", asked: str = "", *, ask=None) -> dict:
    """What `message` says of the reply ELI gave to `asked`: {"label": disputes | accepts | neither,
    "confidence" (the model's, None from the cues alone), "by": "model" | "words", "words": the cue
    reading (reply_verdict)}. `ask(prompt, system, grammar)` is the model call (local_judge()); with
    none, or an answer outside the labels, the cues decide (a correction disputes, an endorsement
    accepts, anything else is neither)."""
    words = reply_verdict(message)
    fallback = {"label": _FROM_WORDS.get(words, NEITHER), "confidence": None, "by": "words", "words": words}
    if ask is None or not str(message or "").strip():
        return fallback
    def _prompt(said: str) -> str:
        return (f'THE USER HAD ASKED: "{asked or ""}"\nTHE ASSISTANT REPLIED: "{said}"\n'
                f'THE USER\'S NEXT MESSAGE: "{message}"\nWord cues (not a verdict): {words}.\nJSON:')
    said = str(reply or "")
    room = int(getattr(ask, "room_chars", 0) or 0)
    over = len(_prompt(said)) + len(_JUDGE_SYSTEM) - room if room else 0
    if over > 0:      # the window the model loaded cannot hold the whole reply: its start and its end
        gap = " … "
        keep = max(0, len(said) - over - len(gap))
        said = said[:keep // 2] + gap + said[len(said) - keep // 2:] if keep else ""
    prompt = _prompt(said)
    try:
        from eli.cognition.gguf_inference import label_grammar
        raw = ask(prompt, _JUDGE_SYSTEM, label_grammar("label", LABELS))
        found = re.search(r"\{.*\}", str(raw or ""), re.S)
        import json as _json
        parsed = _json.loads(found.group()) if found else {}
        label = str(parsed.get("label") or "").strip().lower()
        confidence = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except Exception:  # noqa: BLE001 - an unusable answer: the cues decide
        return fallback
    if label not in LABELS:
        return fallback
    return {"label": label, "confidence": confidence, "by": "model", "words": words}


def local_judge():
    """The model call judge_reply uses: ELI's loaded local model, held by the grammar, as background
    work (eli/cognition/gguf_inference); None when no model is loaded."""
    try:
        from eli.cognition.inference_broker import get_inference_broker
        broker = get_inference_broker()
        if broker is None or not broker.gguf_ready:
            return None
        from eli.cognition import gguf_inference
    except Exception:  # noqa: BLE001 - no model to be had: the cues decide
        return None

    budget = gguf_inference.label_budget("label", LABELS)

    def ask(prompt: str, system: str, grammar) -> str:
        kwargs = {"grammar": grammar} if grammar is not None else {}
        return gguf_inference.chat_completion(prompt, system=system, temperature=0.0, max_tokens=budget, **kwargs)
    try:      # what the loaded window holds beside the answer, in characters
        from eli.cognition.context_budget import chars_per_token
        window = gguf_inference._loaded_ctx(getattr(gguf_inference, "_llm", None))
        ask.room_chars = int(max(0, window - budget) * chars_per_token()) if window else 0
    except Exception:  # noqa: BLE001 - the window unknown: the backend fits the call itself
        ask.room_chars = 0
    return ask
