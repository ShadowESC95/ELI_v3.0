"""Persona + identity text preparation, shared by the engine and its stage
mixins (prompt_assembly, generation).

These load the persona text and sanitise identity/self-reference context before
it goes into a prompt. They lived as module-level helpers in engine.py; lifting
the prompt-assembly and generation stages into their own modules would have
orphaned them (a mixin file cannot import back from engine without a cycle), so
they moved here — one source both sides import.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from eli.core import config

from eli.utils.log import get_logger

log = get_logger(__name__)

_IDENTITY_OR_PHATIC_FASTPATH_RE = re.compile(
    r"\b(?:"
    r"who are you|what are you|who am i|do you know me|do you remember me|"
    r"what do you know about me|what do you remember about me|"
    r"what do you know about me from memory|"
    r"hi|hello|hey|hiya|yo|sup|"
    r"how are you|how are you doing|how you doing"
    r")\b",
    re.I,
)
_BAD_ASSISTANT_IDENTITY_DRIFT_SNIPPETS = (
    "ah, the age-old question",
    "helpful and somewhat sarcastic ai assistant",
    "technology and weather inquiries",
    "communication preferences or working style",
    "waste of both our time",
    "enterprise test",
    "recent_test: hello",
)

def _clean_persona(raw: str) -> str:
    clean_lines: List[str] = []
    in_template_block = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(("PARAMETER ", "FROM ")):
            continue
        if stripped.upper().startswith("TEMPLATE "):
            in_template_block = True
            continue
        if in_template_block:
            if '"""' in stripped:
                in_template_block = False
            continue
        if stripped.startswith("# Model parameters") or stripped.startswith(
            "# Template for conversation"):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines).rstrip()

def _load_persona_text() -> str:
    """
    Canonical persona loader.

    Source of truth:
      eli/cognition/persona.txt
      eli/cognition/persona.auto.txt

    Do not fall back to generic assistant identity unless both canonical
    files are missing. The old lookup paths caused ELI to ignore its real
    persona and drift into base-model AI-disclaimer behaviour.
    """
    # Single source of truth: delegate the base+overlay read to the canonical
    # eli.cognition.persona module (one `_clean_persona`, env-override aware). Compose
    # exactly as before (base + "\n\n" + overlay) so the persona text is unchanged.
    # The legacy candidate/config chain below stays only as a fallback for non-default
    # layouts or when the canonical files are absent.
    try:
        from eli.cognition.persona import read_base_persona as _canon_base, read_auto_persona as _canon_auto
        _cb = (_canon_base() or "").strip()
        _ca = (_canon_auto() or "").strip()
        if _cb and _ca:
            return _cb + "\n\n" + _ca
        if _cb:
            return _cb
        if _ca:
            return _ca
    except Exception:
        pass

    root = Path(__file__).resolve().parents[2]

    # Base identity candidates — stop at first found
    base_candidates = [
        root / "eli" / "cognition" / "persona.txt",
        root / "config" / "persona.txt",
        Path(__file__).resolve().parents[1] / "persona" / "persona.txt",
        Path(__file__).resolve().parent / "persona.txt",
    ]
    # Dynamic overlay — auto-updated every 120s by persona_updater
    overlay_candidates = [
        root / "eli" / "cognition" / "persona.auto.txt",
        root / "config" / "persona.auto.txt",
    ]

    base = ""
    for cand in base_candidates:
        try:
            if cand.exists() and cand.is_file():
                raw = cand.read_text(encoding="utf-8", errors="replace")
                cleaned = _clean_persona(raw).strip()
                if cleaned:
                    base = cleaned
                    break
        except Exception:
            pass

    overlay = ""
    for cand in overlay_candidates:
        try:
            if cand.exists() and cand.is_file():
                raw = cand.read_text(encoding="utf-8", errors="replace")
                cleaned = _clean_persona(raw).strip()
                if cleaned:
                    overlay = cleaned
                    break
        except Exception:
            pass

    if base and overlay:
        return base + "\n\n" + overlay
    if base:
        return base
    if overlay:
        return overlay

    try:
        forced = (config.get_eli_persona() or "").strip()
        if forced:
            return forced
    except Exception:
        pass

    try:
        configured = (config.get_persona() or "").strip()
        if configured:
            return configured
    except Exception:
        pass

    return (
        "You are ELI — Enhanced Learning Interface. You run locally on this "
        "machine. Be substantive, grounded, direct, and never claim to be a generic "
        "cloud AI assistant."
    )

def _eli_is_identity_or_phatic_fastpath(text: str) -> bool:
    norm = " ".join(str(text or "").strip().lower().split())
    return bool(_IDENTITY_OR_PHATIC_FASTPATH_RE.search(norm))

def _eli_scrub_recent_turns_for_identity(recent_turns, user_input: str = ""):
    if not recent_turns:
        return recent_turns
    if not _eli_is_identity_or_phatic_fastpath(user_input):
        return recent_turns

    cleaned = []
    for item in recent_turns:
        role = ""
        content = ""

        if isinstance(item, dict):
            role = str(item.get("role") or item.get("speaker") or item.get("author") or "")
            content = str(item.get("content") or item.get("text") or item.get("message") or "")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = str(item[0] or "")
            content = str(item[1] or "")
        else:
            content = str(item or "")

        low = content.lower()
        role_low = role.lower()
        assistantish = (
            role_low in {"assistant", "eli", "system", "bot"}
            or low.startswith("eli:")
        )

        if any(s in low for s in _BAD_ASSISTANT_IDENTITY_DRIFT_SNIPPETS):
            if assistantish or "enterprise test" in low or "recent_test: hello" in low:
                continue

        cleaned.append(item)

    # Never return empty if we started with content.
    return cleaned if cleaned else recent_turns

def _eli_sanitize_identity_context_block(text: str, user_input: str = "") -> str:
    s = str(text or "")
    if not _eli_is_identity_or_phatic_fastpath(user_input):
        return s.strip()

    bad_markers = (
        "[username]",
        "<local_user>",
        "<username>",
        "recent_test: hello",
        "this is an enterprise test",
        "enterprise test",
        "helpful and somewhat sarcastic ai assistant",
        "technology and weather inquiries",
        "age-old question",
        "how about you?",
        "i'm doing just fine",
    )

    cleaned = []
    for raw in s.splitlines():
        line = str(raw or "")
        low = line.strip().lower()

        if not low:
            cleaned.append("")
            continue

        if low.startswith(("eli:", "assistant:", "- eli:", "- assistant:")):
            continue

        if any(marker in low for marker in bad_markers):
            continue

        cleaned.append(line)

    s = "\n".join(cleaned)
    s = re.sub(r'(?i)\[(?:username|name)\]', "unknown", s)
    s = re.sub(r'(?i)<local_user>|<username>', "unknown", s)
    s = re.sub(r'(?im)^recent turns:\s*$', '', s)
    s = re.sub(r'(?im)^reranked evidence:\s*$', '', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()

def _eli_is_subjective_opinion_prompt(text: str) -> bool:
    """
    Classify prompts asking for ELI's take, judgement, taste, stance, or
    persona-bound opinion. This must not generate canned replies.
    """
    import re
    q = re.sub(r"[^a-z0-9' ;,?.!-]+", " ", str(text or "").lower()).strip()
    q = re.sub(r"\s+", " ", q)

    if not q:
        return False

    opinion_markers = (
        "your opinion", "what's your opinion", "whats your opinion",
        "what do you think", "your take", "thoughts on",
        "give me your take", "real opinion", "non bullshit",
        "no bullshit", "be honest", "brutally honest",
        "where do you stand", "how do you rate",
        "do you like", "do you dislike",
        "what is your view", "what's your view", "whats your view",
    )

    if any(m in q for m in opinion_markers):
        return True

    # Lists of public/cultural/scientific figures after an opinion marker.
    if re.search(r"\b(opinion|thoughts|take|view)\b.*\b(on|about)\b", q):
        return True

    return False
