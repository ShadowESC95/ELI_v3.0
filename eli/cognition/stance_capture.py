"""Notice when ELI has taken a position, so it survives the conversation.

`stance_store` can hold ELI's own positions and `belief.assess_claim` can weigh a
challenge to one, but nothing ever recorded that a position had been taken — the
storage had no author. This is the trigger.

The problem it solves is visible in a live transcript: ELI argued one line on
machine consciousness for four hours, was cornered, conceded precisely, and named
its own earlier move as a defensive manoeuvre. A genuinely good exchange — and
all of it inside one context window. Reopen the subject the next morning and
there is nothing to reopen. ELI could take the opposite line with equal
confidence and never know it had contradicted itself, because the only record of
the argument was the scrollback.

Detection is deliberately narrow. Most of what ELI says is not a stance: reports,
acknowledgements, answers to factual questions and data dumps are all things it
would be absurd to "defend" later. What counts is a COMMITTED FIRST-PERSON CLAIM
about something contestable — the kind of sentence that would be embarrassing to
reverse without noticing.

No inference call. This runs on every assistant turn, so it is regex and set
operations only; the cost of getting it wrong is a junk row, and the cost of
being slow is every reply.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

MIN_REPLY_CHARS = 80
MIN_TOPIC_WORDS = 2
MAX_POSITION_CHARS = 400

# A committed first-person claim. Hedges are excluded on purpose: "I think maybe"
# is not a position anyone needs to be held to.
_ASSERTION = re.compile(
    r"(?:^|(?<=[.!?]\s))\s*(?:no[.,]|yes[.,])?\s*"
    r"(?:i (?:am|'m) not\b|i (?:am|'m)\b|i do not\b|i don'?t\b|i cannot\b|i can'?t\b"
    r"|i have no\b|i disagree\b|i reject\b|i hold\b|i maintain\b"
    r"|that (?:is|'s) (?:not|wrong|false|a category|incorrect)\b"
    r"|you (?:are|'re) (?:wrong|mistaken|conflating|confusing)\b"
    r"|there is no\b|there'?s no\b|it (?:is|'s) not\b)",
    re.I,
)

# Things that look assertive but are reports, not positions.
_NOT_A_STANCE = re.compile(
    r"\bi have \d+\b|\bgrounded supporting counts\b|\blong-term memory rows\b"
    r"|\bjob #\d+\b|\bqueued for\b|\bfailed:\b|\berror:\b|\btraceback\b"
    r"|^\s*(?:understood|noted|done|ok(?:ay)?|sure|got it|fair enough)\b",
    re.I,
)

MAX_QUESTION_WORDS = 45
_RUNTIME_TALK = re.compile(
    r"\b(?:gpu|vram|cuda|nvidia|ctx|context (?:size|window)|batch|threads?|runtime|model file|sqlite|database|"
    r"faiss|inference backend|backend|layers?)\b", re.I)
_NO_EVIDENCE_REPLY = re.compile(
    r"\bi (?:cannot|can'?t|could not|do not|don'?t) (?:provide|tell you|report|confirm|find|see)\b|"
    r"\b(?:evidence|records?|data) (?:does not|doesn'?t|do not|don'?t) (?:list|contain|include|show)\b|\bno record of\b", re.I)

_HEDGE = re.compile(r"\b(?:i think|i guess|maybe|perhaps|possibly|might be|not sure)\b", re.I)

_WORD = re.compile(r"[a-z][a-z0-9'-]{2,}")


def topic_of(user_text: str) -> str:
    """A stable key for what is being discussed.

    Derived from the USER's turn rather than ELI's reply: the user frames the
    subject, and framing is more stable across sessions than whatever wording ELI
    reached for. Sorted so "consciousness and machines" and "machines and
    consciousness" land on the same topic.
    """
    try:
        from eli.runtime.reflection import topic_words
        words = {w for w in topic_words(str(user_text or "")) if len(w) > 3}
    except Exception:
        from eli.cognition.scoring import STOPWORDS
        words = {w for w in _WORD.findall(str(user_text or "").lower())
                 if w not in STOPWORDS and len(w) > 3}
    if len(words) < MIN_TOPIC_WORDS:
        return ""
    # The full content-word set, not a truncation. Taking the first few alphabetically made the key
    # brittle (one extra word gave a different topic for the same argument). Matching is by overlap
    # at lookup (stance_store.get_stance), so a long key is fine.
    return " ".join(sorted(words)[:12])


def _position_sentence(reply: str) -> str:
    """The first committed claim in the reply."""
    text = re.sub(r"\s+", " ", str(reply or "")).strip()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        s = sentence.strip()
        # asking is not asserting, whichever sentence of the reply asks ("I don't think I caught your
        # name — what should I call you?" was kept as a position)
        if len(s) < 12 or s.endswith("?") or _HEDGE.search(s) or _NOT_A_STANCE.search(s):
            continue
        if _ASSERTION.search(s):
            return s[:MAX_POSITION_CHARS]
    return ""


def detect_stance(user_text: str, reply: str) -> Optional[Tuple[str, str]]:
    """(topic, position) when ELI has committed to something, else None."""
    body = str(reply or "").strip()
    if len(body) < MIN_REPLY_CHARS:
        return None            # acknowledgements are not positions
    if len(str(user_text or "").split()) > MAX_QUESTION_WORDS or _RUNTIME_TALK.search(str(user_text or "")):
        return None            # a question about ELI's own runtime is not a subject ELI takes a side on
    if _NO_EVIDENCE_REPLY.search(body[:220]):
        return None            # "the evidence does not list it" reports a gap, not a belief
    if _NOT_A_STANCE.search(body[:200]):
        return None            # a report that happens to start with "I have"
    if body.rstrip().endswith("?"):
        return None            # asking is not asserting
    position = _position_sentence(body)
    if not position:
        return None
    topic = topic_of(user_text)
    if not topic:
        return None
    return topic, position


def capture(cur: sqlite3.Cursor, user_text: str, reply: str) -> Optional[str]:
    """Record the position ELI just took, or reinforce the one it already held.

    Returns the topic when something was recorded. A position that CONTRADICTS a
    held one is not swapped in silently: `record_stance` declines it, and it is
    weighed against the held one (`belief.assess_claim`). Outweighing it, it is a
    revision, kept with what it replaced and why (`revise_stance`); otherwise the
    held stance stands and the contradiction is raised as a conflict thought
    (`belief.voice`), which the next reply on the topic is shown beside the stance.
    """
    found = detect_stance(user_text, reply)
    if not found:
        return None
    topic, position = found
    try:
        from eli.cognition.stance_store import ensure_tables, get_stance, record_stance, revise_stance
        ensure_tables(cur)
        if record_stance(cur, topic, position, provenance="observed"):
            return topic
        held = get_stance(cur, topic)
        if held is None:
            return None
        from eli.cognition.belief import CONCEDE, Belief, assess_claim, voice
        verdict = assess_claim(held, Belief(statement=position, provenance="observed", corroboration=1))
        if verdict.action == CONCEDE:
            revise_stance(cur, topic, position, reason="; ".join(verdict.reasons), provenance="observed")
            return topic
        voice(verdict.action, about=topic, standing=held.statement, challenger=position,
              reasons=verdict.reasons, subject="eli", ref=f"conflict:eli:{topic}", conn=cur)
    except Exception:
        log.debug("stance capture failed", exc_info=True)
    return None


def held_line(cur: sqlite3.Cursor, user_text: str) -> str:
    """What ELI has held on the subject of this message in earlier conversations, as a line for the
    reply's prompt (eli/kernel/stages/prompt_assembly): the stance, how often it was argued and since
    when. "" when ELI holds nothing on it. Only the record: the reply decides whether to keep the
    position or say why it has changed."""
    topic = topic_of(user_text)
    if not topic:
        return ""
    try:
        from eli.cognition.stance_store import ensure_tables, get_stance
        ensure_tables(cur)
        held = get_stance(cur, topic)
    except Exception:
        log.debug("held stance unreadable", exc_info=True)
        return ""
    if held is None:
        return ""
    import time as _time
    since = _time.strftime("%Y-%m-%d", _time.localtime(float(held.first_seen)))
    times = {1: "once"}.get(int(held.corroboration), f"{int(held.corroboration)} times")
    return (f'\nWHAT YOU HAVE HELD ON THIS IN EARLIER CONVERSATIONS: "{held.statement[:MAX_POSITION_CHARS]}" '
            f"(argued {times}, since {since}). Stay consistent with it, or say plainly that and why you now "
            "think otherwise.")


__all__ = ["capture", "detect_stance", "held_line", "topic_of", "MIN_REPLY_CHARS"]
