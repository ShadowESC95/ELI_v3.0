"""Shared primitives for the decomposed engine stages.

Single source for the soft-imported dependencies the engine and its stage mixins
both need, so they agree on one availability flag instead of each re-deriving it
with its own try/except.
"""
from __future__ import annotations

import os
import sys

import re

# Response governance is optional (it may not be installed). The engine uses
# normalize_response (GGUF-artifact cleaning); the memory-commit stage uses
# should_store_as_memory. Both gate on the same _HAS_GOVERNANCE flag.
try:
    from eli.cognition.response_governance import (  # noqa: F401
        normalize_response,
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
    # Strip a trailing direct-address of the assistant's name ("good afternoon eli" ->
    # "good afternoon", "hello there eli" -> "hello there") so greetings that tack the wake
    # name on the end still match the phatic phrase set below. "good afternoon eli" used to
    # fall through as non-phatic, which let the past-session project topics get injected and
    # the model resumed them off a plain hello.
    normalized = re.sub(
        r"\s+(?:eli|pal|bud|buddy|mate|man|dude|bro|friend)$", "", normalized
    ).strip() or normalized
    # Strip a LEADING greeting so a greeting compounded with a phatic check-in still matches
    # ("good afternoon what's the story" -> "what's the story"). This only HELPS a phatic
    # remainder reach the phrase set; a substantive remainder ("good morning fix the bug")
    # still falls through as non-phatic, so it can't swallow a real request.
    _lead = re.sub(r"^(?:good\s+)?(?:morning|afternoon|evening)\b[\s,.]*", "", normalized)
    _lead = re.sub(r"^(?:hi|hey|hello|hiya|howya|yo|sup)\b[\s,.]+", "", _lead).strip()
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
        "all good", "you good", "you ok", "you okay",
        "good morning", "good afternoon", "good evening", "morning eli", "evening eli",
        # Irish / colloquial check-ins (idiom for "how are things", NOT a request for a story)
        "whats the story", "what's the story", "what is the story", "story bud", "story pal",
        "whats the craic", "what's the craic", "hows the craic", "how's the craic", "any craic",
        "whats new", "what's new", "whats happening", "what's happening",
        # Gratitude / closers — purely phatic, never an action (a substantive remainder
        # like "thanks, now fix X" is caught by the follow-up guard / falls through).
        "thanks", "thank you", "thanks a lot", "thanks so much", "thank you so much",
        "cheers", "ta", "much appreciated", "appreciate it", "nice one", "good stuff",
        "good job", "well done", "great stuff", "lovely", "perfect", "brilliant",
        "no worries", "no problem", "you're welcome", "youre welcome",
        # Sign-offs / closers — purely phatic; a substantive remainder is caught
        # by the follow-up guard or falls through as non-phatic.
        "night", "good night", "goodnight", "gnight", "night night", "nighty night",
        "see ya", "see you", "see you later", "see ya later", "talk later",
        "talk soon", "catch you later", "goodbye", "bye", "bye bye", "cya",
    }
    if normalized in phrases:
        return True

    # Short (≤5 word) casual check-in patterns
    _casual_patterns = (
        r"^how'?s the \w+(\s+\w+)?$",
        r"^how'?s your \w+(\s+\w+)?$",
        r"^how are (you|things|we)( doing| going)?$",
        r"^what'?s (up|good|new|happening)(\s+\w+)?$",
        r"^\w+ \w+ buddy$",
        r"^hey \w+$",
    )
    if n <= 5:
        for pat in _casual_patterns:
            if re.match(pat, normalized):
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
    if has_eli_status and not has_task and n <= 30:
        return True

    return False


def _eli_test_mode() -> bool:
    if os.environ.get("ELI_TEST_MODE", "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in sys.modules


__all__ = [
    "_eli_test_mode",
    "_HAS_GOVERNANCE",
    "_eli_path_get",
    "_is_brief_phatic_prompt",
    "normalize_response",
    "should_store_as_memory",
]
