"""Router memory-runtime intents: when a question about what ELI has been doing is about development work, when one is about memory's machinery rather than what is remembered, and when the memory-runtime route lock triggers. The router keeps the rules that use them."""
from __future__ import annotations

import re


# "What have you been doing?" is two different questions wearing the same words.
# Asked after "what updates have you made to the code", it is the maintenance
# question this grounded contract exists for. Asked in the middle of a chat, it is
# small talk — and answering it with a git/runtime evidence dump is a worse failure
# than the invented maintenance activity the contract was built to prevent.
_BARE_ACTIVITY_PHRASES = (
    "what have you been doing",
    "what have you been processing",
    "what have you been working on",
    "what have you been up to",
    "what you been doing",
    "what you been up to",
)

_DEV_UPDATE_SUBJECT_RE = re.compile(
    r"\b(code|codebase|repo|repositor\w*|git|commit\w*|version|build|patch\w*|"
    r"capabilit\w*|module|tests?\b|upgrade\w*|"
    # "checks"/"checked"/"updates" as maintenance nouns — NOT "checking in/up"
    # (Jess Iris Xe live: "just checking in on you. what have you been doing
    # the past week?" matched check(?:ing)? and forced a packaged SELF_REPORT dump).
    r"checks?\b|checked\b|updates?\b|updated\b|"
    r"maintenance|deploy\w*|repair\w*|refactor\w*|"
    r"fix(?:es|ed|ing)?|"
    r"runtime|pipeline|system)\b",
    re.I,
)


def _eli_activity_question_is_about_dev_work(low: str) -> bool:
    """True when an activity question names development work as its subject.

    Shared by both routes that can reach the recent-updates report, so the two
    cannot drift apart and answer the same sentence differently.
    """
    text = str(low or "")
    if not any(p in text for p in _BARE_ACTIVITY_PHRASES):
        return True          # not a bare activity question; caller's own match governs
    # Rapport "checking in/up on you" must not count as a maintenance "check".
    scrubbed = re.sub(
        r"\bcheck(?:ing|ed)?\s+(?:in|up)\b(?:\s+on\b)?(?:\s+you\b)?",
        " ",
        text,
        flags=re.I,
    )
    return bool(_DEV_UPDATE_SUBJECT_RE.search(scrubbed))


def _not_about_the_machinery(text) -> bool:
    """"how does it feel to have a working memory, haha?" is said to someone, and a block of pasted
    text that mentions memory is not asking how it works. Both got the database dump."""
    said = str(text or "")
    return (len([ln for ln in said.splitlines() if ln.strip()]) >= 4
            or bool(re.search(r"\bhow\s+(?:does|do|did)\s+(?:it|that|this|you)\s+feel\b", said, re.I)))


def _eli_memory_runtime_route_lock_should_trigger(text):
    import re as _re

    raw = str(text or "").strip()
    low = raw.lower()

    if not low:
        return False

    if _asks_for_remembered_content(low) or _not_about_the_machinery(text):
        return False
    # "GPU memory", "VRAM temperature": hardware, not the memory subsystem, unless it also names the machinery.
    if _re.search(r"\b(?:gpu|vram|cuda|nvidia|graphics|thermal|temperature|temp)\b", low) and not _re.search(
            r"\b(?:sqlite|databases?|db|tables?|faiss|fts5?|schema|explain_memory_runtime|memory (?:system|internals|architecture))\b", low):
        return False

    # Literal control/action invocation.
    if _re.search(r"\b(?:run|execute|call|invoke)?\s*`?explain_memory_runtime`?\b", low):
        return True

    # Direct architecture/internal-memory requests.
    if _re.search(
        r"\b("
        r"explain exactly how your memory system works internally|"
        r"memory system works internally|"
        r"how (?:does|do) your memory system work|"
        r"how does your memory work internally|"
        r"which files.*which db tables.*which functions|"
        r"memory runtime(?: surface)?|"
        r"memory architecture|"
        r"memory internals"
        r")\b",
        low,
    ):
        return True

    # Broader DB/schema/function phrasing.
    if _re.search(r"\bmemor(?:y|ies)\b", low) and _re.search(
        r"\b("
        r"database files?|db files?|databases?|sqlite|tables?|schema|"
        r"functions?|internally|architecture|runtime|"
        r"faiss|fts5|vectors?|vectoring|recall_log|conversation_turns|"
        r"user\.sqlite3|agent\.sqlite3|memory\.sqlite3"
        r")\b",
        low,
    ):
        # Personal profile questions should stay personal-memory unless the
        # user explicitly asks architecture/schema/files/functions.
        asks_profile = _re.search(
            r"\b(what do you know about me|what do you remember about me|"
            r"my preferences|my profile|who am i|what is my name)\b",
            low,
        )
        asks_arch = _re.search(
            r"\b(files?|db|database|sqlite|tables?|functions?|schema|"
            r"internally|runtime|architecture)\b",
            low,
        )
        if asks_profile and not asks_arch:
            return False
        return True

    return False


_RECALL_CONTENT_RE = re.compile(
    r"\bwhat\s+(?:was|were|is|are)\s+(?:the\s+)?[^.?!]{0,90}?\b(?:i|we)\s+(?:gave|told|said|mentioned|stored|saved|asked\s+you\s+to\s+remember)\b"
    r"|\bwithout\s+me\s+restating\b|\bwhat\s+did\s+i\s+(?:tell|give|ask)\s+you\b", re.I)


_MEMORY_MACHINERY_RE = re.compile(
    r"\b(?:files?|db|databases?|sqlite|tables?|schema|functions?|internally|architecture|pipeline|faiss|fts5?|vectors?|runtime)\b", re.I)


def _asks_for_remembered_content(low: str) -> bool:
    """Asks what was said or stored (the content), not how memory works. Recall it before explaining anything."""
    return bool(_RECALL_CONTENT_RE.search(low) and not _MEMORY_MACHINERY_RE.search(low))
