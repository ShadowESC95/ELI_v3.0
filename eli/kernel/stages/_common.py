"""Shared primitives for the decomposed engine stages.

Single source for the soft-imported dependencies the engine and its stage mixins
both need, so they agree on one availability flag instead of each re-deriving it
with its own try/except.
"""
from __future__ import annotations

import os

import re
from eli.cognition import context_budget as _ctx_budget
import logging
from eli.utils.log import get_logger

log = get_logger(__name__)

# Response governance is optional (it may not be installed). The engine uses
# normalize_response (GGUF-artifact cleaning); the memory-commit stage uses
# should_store_as_memory. Both gate on the same _HAS_GOVERNANCE flag.
try:
    from eli.cognition.output_governor import (  # noqa: F401
        clean_gguf_artifacts as normalize_response,
        should_store_as_memory,
    )
    _HAS_GOVERNANCE = True
except ImportError:  # pragma: no cover - governance is optional
    normalize_response = None
    should_store_as_memory = None
    _HAS_GOVERNANCE = False

def _eli_path_get(obj, key, default=None):
    """
    Compatibility helper for ELI path containers.
    Accepts both dict-style path maps and object/namespace-style path maps.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _is_brief_phatic_prompt(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    normalized = re.sub(r"[^a-z0-9' ]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    # a greeting that asks ELI to remember is a memory request, not small talk
    if _ctx_budget.is_recall_question(raw):
        return False
    # Strip a trailing direct address ("good afternoon eli" -> "good afternoon") so greetings with
    # the wake name still match the phatic set. Without it "good afternoon eli" was non-phatic,
    # which injected past-session topics and the model resumed them off a plain hello.
    normalized = re.sub(
        r"\s+(?:eli|pal|bud|buddy|mate|man|dude|bro|friend)$", "", normalized
    ).strip() or normalized
    # Strip a leading greeting so a greeting plus a phatic check-in still matches ("good afternoon
    # what's the story"). A substantive remainder ("good morning fix the bug") still falls through
    # as non-phatic, so it can't swallow a real request.
    _lead = re.sub(r"^(?:good\s+)?(?:morning|afternoon|evening)\b[\s,.]*", "", normalized)
    _lead = re.sub(r"^(?:hi|hey|hello|hiya|howya|yo|sup)\b[\s,.]+", "", _lead).strip()
    # A leading direct address mirrors the trailing strip: "hey buddy, how's the head" is "how's the
    # head". Otherwise the address counted as a content word and pushed the check-in past the
    # <=5-word gate.
    _lead = re.sub(
        r"^(?:pal|bud|buddy|mate|man|dude|bro|lad|friend|eli)\b[\s,.]+", "", _lead
    ).strip() or _lead
    # Trailing conversational filler on a check-in — "how's the head doing, now"
    # is "how's the head". Only stripped after an idiom stem is already present,
    # so it cannot shorten a substantive request into a phatic one.
    if re.match(r"^how'?s?\s+(?:is\s+)?(?:the|your)\b", _lead):
        _lead = re.sub(
            r"[\s,]+(?:doing|going|keeping|holding up)?[\s,]*"
            r"(?:now|today|then|at all|so|these days)?[\s,.?!]*$", "", _lead
        ).strip() or _lead
    if _lead and _lead != normalized and len(_lead.split()) <= 8:
        normalized = _lead
    words = normalized.split()
    n = len(words)

    # Hard cap: more than 30 words is never purely phatic
    if n > 30:
        return False

    # Follow-up / result-request patterns are never phatic even if short
    _followup_patterns = (
        r"^(okay|ok|so|and|now)[,.]?\s*(what|where|when|how|why|tell|show|give|any)",
        r"^what (are|were|is|did|do) (the )?results?",
        r"^what (happened|did you find|did you see|did it say)",
        r"^(any|what) (results?|findings?|errors?|issues?|output|response)",
        r"^(show|tell|give) (me )?(the )?(results?|output|findings?|report)",
    )
    for _fp in _followup_patterns:
        if re.match(_fp, normalized):
            return False

    phrases = {
        "hi", "hello", "hey", "hiya", "howya", "yo", "sup",
        "hi eli", "hello eli", "hey eli", "howya eli",
        "whats up", "what's up", "you alive", "you there",
        "how are you", "how you doing", "how ya doing", "hows things", "how's things",
        "how is things", "how are things", "hows everything", "how's everything",
        "hows it going", "how's it going", "hows it", "how's it",
        "all good", "you good",         "you ok", "you okay",
        "you any better", "any better",
        "good morning", "good afternoon", "good evening", "morning eli", "evening eli",
        # Irish / colloquial check-ins (idiom for "how are things", NOT a request for a story)
        "whats the story", "what's the story", "what is the story", "story bud", "story pal",
        "whats the craic", "what's the craic", "hows the craic", "how's the craic", "any craic",
        "whats new", "what's new", "whats happening", "what's happening",
        # Irish / colloquial — same rapport class as "how's things"
        "hows tricks", "how's tricks", "how are tricks",
        "hows the form", "how's the form", "hows the head", "how's the head",
        # Gratitude / closers — purely phatic, never an action (a substantive remainder
        # like "thanks, now fix X" is caught by the follow-up guard / falls through).
        "thanks", "thank you", "thanks a lot", "thanks so much", "thank you so much",
        "cheers", "ta", "much appreciated", "appreciate it", "nice one", "good stuff",
        "good job", "well done", "great stuff", "lovely", "perfect", "brilliant",
        "no worries", "no problem", "you're welcome", "youre welcome",
        # Sign-offs / closers — purely phatic; a substantive remainder is caught
        # by the follow-up guard or falls through as non-phatic.
        "night", "good night", "goodnight", "gnight", "night night", "nighty night",
        # Bare time-of-day greetings. "good morning" was listed and "morning" wasn't, so "afternoon,
        # Eli" (which the router calls chat.greeting at 0.90) wasn't phatic here and the two
        # disagreed. The address strip above already reduces it to "afternoon".
        "morning", "afternoon", "evening",
        "mornin", "evenin", "good evening", "good day", "gday", "g'day",
        "see ya", "see you", "see you later", "see ya later", "talk later",
        "talk soon", "catch you later", "goodbye", "bye", "bye bye", "cya",
    }
    if normalized in phrases:
        return True

    # Compound phatic: "what's the story? you good?" — Irish greeting + check-in.
    if re.search(r"\b(?:whats|what'?s|what is) the story\b", normalized) and re.search(
        r"\b(?:you good|you ok|you okay|you alright|you better|you sorted)\b", normalized
    ):
        return True

    # Short (<=5 word) casual check-in patterns. "how's the X" is a wellbeing idiom unless X is
    # something ELI runs on: "how's the head" is a hello, "how's the GPU" is a real question. Listing
    # the technical nouns is narrower and safer than guessing intent. "how IS the x" too, or the
    # longer form goes to the LLM resolver (46s on CPU) to answer "CHAT".
    _technical_subject = re.match(
        r"^how'?s? (?:is )?(?:the|your) (gpu|cpu|ram|vram|server|model|build|tests?|code|api|"
        r"db|database|memory|disk|network|index|suite|pipeline|daemon|queue|log)\b",
        normalized,
    )
    _casual_patterns = () if _technical_subject else (
        r"^how'?s? (?:is )?the \w+(\s+\w+)?$",
        r"^how'?s? (?:is )?your \w+(\s+\w+)?$",
        # A second-person state check aimed at ELI ("are you ok", "you better now"): pronoun + state
        # adjective + optional temporal tail, anchored end to end, so there's no verb-plus-object slot
        # for a real request ("fix the router") to hide in. Each of these used to cost a full
        # intent-classification generation.
        r"^(?:are |r )?you(?:\s+any)?\s+"
        r"(?:ok|okay|alright|allright|good|well|better|fine|right|sorted|sound|normal"
        r"|back(?: to (?:normal|yourself|form))?)"
        r"(?: now| yet| today| again| then)?$",
        r"^how are (you|things|we)(?:\s+(?:doing|going))?(?:\s+(?:today|tonight|now|this\s+(?:morning|afternoon|evening)))?$",
        r"^what'?s (up|good|new|happening)(\s+\w+)?$",
        r"^\w+ \w+ buddy$",
        r"^hey \w+$",
    )
    if n <= 7:
        for pat in _casual_patterns:
            if re.match(pat, normalized):
                return True
    # Longer check-ins: "are you back to normal yet, or not?" — still rapport, not CLEAR_TONE.
    if n <= 10 and re.match(
        r"^(?:are |r )?you back to (?:normal|yourself)(?: yet|, or not)?(?:\?)?$",
        normalized,
    ):
        return True

    # "feeling normal again" / typo-tolerant "you oaynow" after greeting strip —
    # live: "hey buddy, you oaynow? feeling normal again?" must stay phatic so
    # continuity guards apply and stale GPU layer memory is not volunteered.
    if n <= 6 and re.match(
        r"^feeling (?:normal|better|ok|okay|fine|alright)(?: again| now| yet)?$",
        normalized,
    ):
        return True
    if n <= 5 and re.match(
        r"^(?:are |r )?you\s+o+k?a?y?\s*now(?:\s+again)?$",
        normalized,
    ):
        return True
    if n <= 12 and re.search(r"\bfeeling (?:normal|better)\b", normalized) and re.search(
        r"\byou\b", normalized
    ) and not re.search(r"\b(gpu|layer|vram|fix|install|bug|code|error)\b", normalized):
        return True

    # Identity/self-awareness questions — phatic if combined with a greeting
    _identity_only = {
        "do you know who you are", "do you know who i am",
        "do you know me", "who are you", "who am i",
        "do you remember me", "do you remember who i am",
    }
    if normalized in _identity_only:
        return False

    # Multi-sentence greeting + identity combo (e.g. "hey how are you do you know who you are")
    starts_as_greeting_check = any(normalized.startswith(g) for g in (
        "hey", "hi", "hello", "hiya", "yo", "howdy", "morning", "evening",
        "afternoon", "good morning", "good afternoon", "good evening",
    ))
    has_identity = any(p in normalized for p in _identity_only)
    has_checkin_quick = any(p in normalized for p in (
        "how are you", "how you doing", "you doing", "you alright", "you good",
    ))
    if has_identity:
        return False

    if starts_as_greeting_check and has_checkin_quick and n <= 30:
        # Only phatic if no actual task is embedded.
        _task_words_check = (
            "help", "fix", "write", "create", "open", "run", "search", "find",
            "explain", "tell me", "show me", "generate", "code", "script",
            "gimme", "give me", "lowdown", "stats", "stat", "report", "rundown",
            "summary", "capabilities", "inventory", "update me", "status report",
        )
        if not any(t in normalized for t in _task_words_check):
            return True

    # Multi-sentence greeting detection (≤30 words):
    # Catches "hey bud, how are you today? how is the head?" etc.
    # Must start with a greeting word and contain only check-in content
    _greeting_starts = (
        "hey", "hi", "hello", "hiya", "yo", "howdy", "morning", "evening",
        "afternoon", "good morning", "good afternoon", "good evening",
    )
    _checkIn_phrases = (
        "how are you", "how's it", "hows it", "how you doing",
        "how is the", "how's the", "hows the",
        "you doing", "you alright", "you good", "you okay",
        "all good", "everything ok", "everything good",
        "how are things", "hows things",
        # ELI-directed wellbeing / status queries — these do not need to start
        # with a greeting word to be phatic; "how has your last X been" is a
        # social check-in regardless of how the sentence opens.
        "how has your last", "how have you been", "how's your last",
        "how was your last", "how was your day", "how has your day",
        "checking up on you", "just checking up", "just checking in",
        "how has your", "how was your",
    )
    starts_as_greeting = any(normalized.startswith(g) for g in _greeting_starts)
    has_checkin = any(p in normalized for p in _checkIn_phrases)

    # No task/question words that suggest actual work is needed
    _task_words = (
        "help", "fix", "write", "create", "open", "run", "search", "find",
        "what is", "explain", "tell me", "show me", "can you", "could you",
        "why", "when", "where", "which", "generate", "code", "script",
        "gimme", "give me", "lowdown", "stats", "stat", "report", "rundown",
        "summary", "capabilities", "inventory", "update me", "status report",
        "hardcoded", "template", "stub", "static info", "dynamic",
    )
    has_task = any(t in normalized for t in _task_words)

    if starts_as_greeting and has_checkin and not has_task:
        return True

    # ELI wellbeing/status check-in: does not require greeting opener.
    # "Still here pal, how has your last 24 hours been?" is phatic even
    # though it starts with "still" rather than "hey/hi/hello".
    _eli_status_phrases = (
        "how has your last", "how have you been", "how's your last",
        "how was your last", "checking up on you", "just checking up",
        "just checking in",
    )
    has_eli_status = any(p in normalized for p in _eli_status_phrases)
    # "just checking in" alone is phatic; "just checking in … what have you been
    # doing the past week?" is a substantive social ask — must not force quick
    # (which then dumps SELF_REPORT when "checking" falsely looks like maintenance).
    _substantive_activity = any(p in normalized for p in (
        "what have you been doing", "what have you been up to",
        "what have you been working on", "what you been doing", "what you been up to",
        "what has been going on", "what's been going on", "whats been going on",
        "the past week", "this past week", "past few days", "all week",
    ))
    if has_eli_status and _substantive_activity:
        return False
    if has_eli_status and not has_task and n <= 30:
        return True

    # Meta about ELI's own speech/output quality — treat as light rapport so
    # we don't dump session memory about an unrelated prior topic and answer
    # THAT instead ("speaking in tongues" → model-swap essay).
    if n <= 14 and not has_task and re.search(
        r"\b(?:still\s+)?(?:speaking|talking|writing|replying|answering)\b"
        r".*\b(?:tongue|tongues|tounges|gibberish|garble|garbled|nonsense|weird|broken)\b"
        r"|"
        r"\b(?:tongue|tongues|tounges|gibberish|garble|garbled)\b",
        normalized,
    ):
        return True

    return False


# Where a turn that is ELI's own comes from: its automation (habits, the proactive daemon, the
# scheduler and its scheduled tasks, the autonomy loop, the system) and its internal helpers that ask
# the engine for text (the Labs report writer). Such a turn is never the user speaking: it is not
# stored as their message or memory, stamped as their activity, read as their reply to the last
# answer or assessed as their route. Matched on the tag before any ":detail". "scheduled_task" (the
# scheduled-task worker's) was missing, so ELI's own overnight research request was stored as the
# owner's message.
_AUTONOMOUS_SOURCES = frozenset(("habit", "proactive", "scheduler", "scheduled_task", "system", "autonomy",
                                 "labs"))


# Reading the web: a search's results, or the page a link points to. Both are answered from what came
# back and nothing else.
_WEB_READS = frozenset(("WEB_SEARCH", "WEB_FETCH"))


def _is_autonomous_source(source: str) -> bool:
    """True when `source` (process()'s own origin tag) is ELI's own automation, not a
    real user turn — one vocabulary, shared by every place that needs the distinction."""
    return str(source or "user").strip().lower().split(":", 1)[0] in _AUTONOMOUS_SOURCES


def _eli_test_mode() -> bool:
    from eli.core.paths import test_mode     # the one switch, ELI_TEST_MODE
    return test_mode()



def _eli_bus_first_ok_result(bus_result, action):
    """Return the bus's *successful* authoritative result for action, if any.

    Used as a "don't replan a success" guard: when the AgentBus already ran a
    deterministic action ok (e.g. MORNING_REPORT with the full report in
    content) but a redundant re-execution downstream failed, we trust the
    earlier success instead of replanning into an invented/unsupported action.
    """
    action_u = str(action or "").upper().strip()
    if not action_u or bus_result is None:
        return None

    def _ok_with_content(d):
        if not isinstance(d, dict) or d.get("skipped"):
            return False
        if not d.get("ok"):
            return False
        if str(d.get("action") or action_u).upper().strip() != action_u:
            return False
        return bool(str(d.get("content") or d.get("response") or "").strip())

    try:
        ar = getattr(bus_result, "action_result", None)
        if _ok_with_content(ar):
            return dict(ar)
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    try:
        for r in list(getattr(bus_result, "agent_results", []) or []):
            if str(getattr(r, "agent", "") or "") not in {"system", "plugin"}:
                continue
            data = getattr(r, "data", None)
            if _ok_with_content(data):
                return dict(data)
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

    return None

__all__ = [
    "_eli_bus_first_ok_result",
    "_eli_test_mode",
    "_HAS_GOVERNANCE",
    "_eli_path_get",
    "_is_brief_phatic_prompt",
    "normalize_response",
    "should_store_as_memory",
    "_is_autonomous_source",
    "_AUTONOMOUS_SOURCES",
]


def _phatic_generation_budget() -> int:
    """Enough room for a voiced greeting; not a full essay."""
    try:
        _raw = str(os.environ.get("ELI_PHATIC_MAX_TOKENS", "256") or "256").strip()
        _n = int(_raw)
        return max(128, min(_n, 384))
    except Exception:
        return 256


def _clarifier_norm(text: str) -> str:
    """Punctuation- and case-insensitive form, for comparing an utterance to itself.

    Apostrophes are deleted rather than spaced so "what's" and "whats" collapse to
    the same token; spacing them would leave "what s" and let a contraction slip an
    otherwise identical replay past the comparison.
    """
    lowered = str(text or "").lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", lowered)).strip()


# Memory block of the evidence prompt: characters per token of the loaded window.
ELI_BUDGET_MEMORY_CHARS_PER_TOKEN = 0.25


def _mounted_model_authority_line() -> str:
    """Authoritative mounted-model line — injected every turn so memory cannot lie."""
    _mounted = ""
    try:
        from eli.runtime.live_introspection import _runtime_core as _rc
        _core = _rc()
        _mname = _core.get("model_name") or _core.get("model_path")
        if _mname and str(_mname) not in ("", "unknown") and _core.get("loaded"):
            from pathlib import Path as _P_mm
            _mounted = _P_mm(str(_mname)).name
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if not _mounted:
        try:
            import json as _j_mm
            from eli.core.paths import artifacts_dir as _art_mm
            _snap = _art_mm() / "runtime_snapshot.json"
            if _snap.exists():
                _s = _j_mm.loads(_snap.read_text(encoding="utf-8"))
                _mn = (
                    ((_s.get("effective") or {}).get("model_name") or "")
                    or _s.get("model_name")
                    or ""
                )
                if _mn:
                    from pathlib import Path as _P_mm2
                    _mounted = _P_mm2(str(_mn)).name
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    if not _mounted:
        return ""
    return (
        "[MOUNTED MODEL — authoritative] Currently loaded in this process: "
        f"'{_mounted}'. When you state which model you run, use ONLY this name. "
        "Stale memory, prior turns, or family guesses (GLM, Qwen, Ornith, etc.) are "
        "wrong unless they match this filename exactly."
    )


def _phatic_rapport_style_rule() -> str:
    """Steering block for greetings/check-ins — keeps ELI's voice without memory dumps."""
    return (
        "SMALLTALK / RAPPORT MODE:\n"
        "- Treat the latest user message as a chemistry-building moment, not as a task ticket.\n"
        "- Reply AS ELI with your full voice: local, dry, direct, lightly sarcastic when it fits, "
        "and alive in tone — not a stripped-down echo of their words.\n"
        "- Give a real greeting: 2–4 sentences with personality. Match their mood — warm, brisk, "
        "bantering — not their words. A dry observation or natural follow-up is welcome.\n"
        "- FORBIDDEN: reusing the user's own words/phrases back at them, telegraphic parroting "
        "('It's morning.' after 'good morning'), weather-report minimalism. You are ELI, not an "
        "echo chamber.\n"
        "- Do not say the prompt is a common greeting, or use 'functioning as intended', "
        "'ready to assist', 'queries or tasks', 'how can I help', 'happy to help', or customer-service filler.\n"
        "- Do NOT resume old projects, invent user preferences/habits, or dump runtime stats unprompted.\n"
        "- It is fine to ask one natural follow-up about mood or what they're up to — but never as "
        "a substitute for actually greeting them.\n"
        "- CRITICAL: Do NOT assert that the user has a preference, habit, or memory unless it "
        "appears in verified profile or this conversation. Wit and cultural references are fine; "
        "invented user biography is not.\n"
        "- Do NOT claim a different local time, timezone, or weather than CURRENT TIME above.\n"
        "- You already know today's day/date from CURRENT TIME — never claim otherwise.\n"
        "- Do NOT invent model names, GPU stats, or runtime telemetry — use MOUNTED MODEL only.\n"
        "- Do NOT volunteer GPU layer counts, VRAM %, or load parameters on a casual check-in "
        "unless the user asked about hardware/status.\n"
        "- Humour must not replace warmth; if there is an actual runtime issue, mention it plainly.\n"
        "- If you already greeted with the same opener this session (e.g. 'Hey bud'), vary it — "
        "do not open twice in a row with the identical phrase.\n\n"
    )
