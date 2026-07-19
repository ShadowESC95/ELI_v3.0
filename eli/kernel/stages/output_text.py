"""Assistant-output text normalisation, shared by the engine and the
generation stage.

Strips model artefacts (special tokens, system-diagnostic lines, reasoning
scaffold), sanitises identity drift, and applies the identity-memory policy —
the final clean-up a generated reply gets before it is shown or stored. Kept
in its own leaf module (imports guards, never engine) so both the engine and
the generation mixin share one implementation.
"""
from __future__ import annotations

import re

from eli.kernel.stages.guards import _strip_reasoning_scaffold

_SYS_DIAG_RE = re.compile(
    r'(?:'
    r'total\s+(?:stored\s+)?memories\s*:'
    r'|total\s+conversation\s+turns\s*:'
    r'|memories\s+indexed\s+by\s+fts'
    r'|session\s+summar(?:ies|y)\s*(?:,|\.|:|\d)'
    r'|recall\s+log\s+entr'
    r'|memory\s+health\s+signals?'
    r'|no\s+obvious\s+weaknesses\s+in\s+memory'
    r'|faiss\s+(?:vectors?|count|index)'
    r')',
    re.IGNORECASE,
)


def _strip_special_tokens(text: str) -> str:
    t = text or ""
    for tok in ("[INST]", "[/INST]", "<s>", "</s>",
                "<|im_start|>", "<|im_end|>", "<|endoftext|>"):
        t = t.replace(tok, "")
    # Strip ChatML role lines leaked into the response
    # e.g. "user\nHey eli...\nassistant\n" or "system\n...\nuser\n"
    t = re.sub(r"^(?:system|user|assistant)\n", "", t, flags=re.I)
    return t.strip()

def _sanitize_identity_drift(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(
    r"^\s*(?:hello[,!]?\s*)?(?:i am|i'm)\s+(?:an?\s+)?ai(?:\s+assistant|\s+model|)[:,.!\s-]*",
    "",
    t,
     flags=re.I).strip()
    t = re.sub(
    r"^\s*as an ai(?:\s+assistant|\s+model|)[:,.!\s-]*",
    "",
    t,
     flags=re.I).strip()
    t = re.sub(
    r"^\s*i do(?:n't| not) have a head[.!?\s]*",
    "",
    t,
     flags=re.I).strip()
    t = re.sub(
    r"^\s*i do(?:n't| not) have personal memor(?:y|ies)[.!?\s]*",
    "",
    t,
     flags=re.I).strip()
    t = re.sub(
    r"^\s*i can(?:not|'t) retain information[.!?\s]*",
    "",
    t,
 flags=re.I).strip()
    return t

def _policy_identity_memory_response(user_text: str, model_text: str) -> str:
    u = (user_text or "").lower()
    t = (model_text or "").strip()
    if not t:
        return t
    identity_triggers = (
        "who are you", "what are you", "what are you actually running on",
        "how does your memory work", "what do you know about me",
        "runtime audit", "memory internals", "cognition pipeline",
    )
    if any(k in u for k in identity_triggers):
        t = re.sub(
    r"^\s*(?:hello[,!]?\s*)?(?:i am|i'm)\s+eli[^.?!]*[.?!]?\s*",
    "",
    t,
     flags=re.I).strip()
        t = re.sub(
    r"^\s*how (?:can|may) i assist you today[.?!\s]*",
    "",
    t,
     flags=re.I).strip()
    return t

def _strip_system_diag_lines(text: str) -> str:
    """Remove lines that are DB health metrics — they leak into identity responses."""
    lines = text.splitlines()
    kept = [ln for ln in lines if not _SYS_DIAG_RE.search(ln)]
    # Remove bullet numbering holes (e.g. "8. " with nothing after)
    return "\n".join(kept).strip()

def _normalize_assistant_text(user_text: str, text: str) -> str:
    t = _strip_special_tokens(text)
    t = _strip_reasoning_scaffold(t)
    t = _sanitize_identity_drift(t)
    t = _policy_identity_memory_response(user_text, t)
    # Strip system health metric lines that bleed into user-facing identity responses
    if any(k in (user_text or "").lower() for k in (
        "what do you know about me", "tell me about me", "my memory",
        "who am i", "what have you learned about me",
    )):
        t = _strip_system_diag_lines(t)

    # Strip any turn-boundary continuation the model leaked through stop tokens
    # Pattern: response ends with "\n\n<role>:" or "\n<role>:" or the user's own text
    _role_markers = re.compile(
        r'\n+(?:User|USER|ELI|Assistant|ASSISTANT)\s*:\s*.*$',
        re.DOTALL
    )
    t = _role_markers.sub('', t).strip()

    # Strip canned completion-style prefixes ("Short answer:", "Answer:", etc.)
    t = re.sub(r"^\s*Short\s+answer\s*:\s*", "", t, flags=re.I).strip()
    t = re.sub(r"^\s*(?:Answer|Response|Reply)\s*:\s*", "", t, flags=re.I).strip()

    # Strip incomplete JSON/list artifacts from truncated max_tokens responses
    # Catches lone "[", "{", "[\n", "{\n" at the end of otherwise complete text
    t = re.sub(r'\s*[\[{]\s*$', '', t).strip()

    # Strip model meta-commentary: "(Note: This response deviates...)" patterns
    t = re.sub(r'\s*\(Note:[^)]{0,300}\)', '', t, flags=re.I).strip()
    t = re.sub(r'\s*\[Note:[^\]]{0,300}\]', '', t, flags=re.I).strip()
    # Strip leaked INTERNAL-STATE / context-metadata lines the model echoed verbatim from its
    # brief — "[Your current activity: …, attention: …]", "[Remembered past topics: …]",
    # "[ELI INTERNAL STATE]" etc. These are context scaffolding, never user-facing. Anchored to
    # whole lines with a known internal label, so it never touches legitimate bracketed text
    # like "[MEMORY SEARCH RESULT: …]" or an array index. (2026-06-09: a casual "back in a
    # minute" drew a reply that ended in these bracketed metadata lines.)
    t = re.sub(
        r'(?im)^[ \t]*\[(?:your\s+)?(?:current\s+activity|attention|remembered\s+past\s+topics'
        r'|recalled\s+(?:past\s+)?topics|recalled\s+research|eli\s+internal\s+state'
        r'|internal\s+state|world\s+state)\b[^\]\n]*\]\s*$',
        '', t).strip()
    # Strip trailing self-critique the model appended to phatic responses
    t = re.sub(
        r'\s*\(Note:.*$', '', t, flags=re.I | re.DOTALL
    ).strip()

    if user_text and len(user_text.strip()) >= 8:
        _user_stripped = user_text.strip()
        _user_low = _user_stripped.lower()

        # ── HEAD echo: model starts by repeating the user's message ──
        # Compare normalised (whitespace-collapsed) versions for robustness
        _t_norm = re.sub(r'\s+', ' ', t.strip()).lower()
        _u_norm = re.sub(r'\s+', ' ', _user_low)
        if _t_norm.startswith(_u_norm):
            # Exact leading match — strip the echoed portion
            t = t.strip()[len(_user_stripped):].lstrip('\n\r :–—').strip()
        else:
            # Partial leading match: first 50 chars align → find where user text ends
            _check = min(50, len(_u_norm))
            if _check >= 20 and _t_norm[:_check] == _u_norm[:_check]:
                # Find the last ~20 chars of user text in the response and cut there
                _tail_probe = _u_norm[-20:]
                _cut = _t_norm.find(_tail_probe)
                if _cut > 0:
                    t = t.strip()[_cut + 20:].lstrip('\n\r :–—').strip()

        # ── TAIL echo: model ends by repeating the user's message ──
        _t_lines = t.strip().splitlines()
        while _t_lines:
            _last = re.sub(r'\s+', ' ', _t_lines[-1].strip()).lower()
            if _last and (_last == _u_norm or
                          (_u_norm in _last and len(_last) < len(_u_norm) + 20)):
                _t_lines.pop()
            else:
                break
        t = '\n'.join(_t_lines).strip()

    low = t.lower().strip()
    canned_prefixes = (
        "hello, i am eli", "how may i assist you today",
        "i don't have personal friendships", "i do not have personal friendships",
    )
    if any(low.startswith(p) for p in canned_prefixes):
        t = re.sub(r"^(hello,?\s*i am eli[.!?\s]*)", "", t, flags=re.I).strip()
        t = re.sub(
    r"^(how may i assist you today[.!?\s]*)",
    "",
    t,
     flags=re.I).strip()
        t = re.sub(
    r"^(i do(?:n't| not) have personal friendships[.!?\s]*)",
    "",
    t,
     flags=re.I).strip()
    t = re.sub(r'(?i)\[(?:user|username|name)\]', '', t)
    t = re.sub(r'(?i)<(?:local_user|username|name)>', '', t)
    t = re.sub(r'\s+,', ',', t)
    t = re.sub(r',\s*\.', '.', t)
    t = re.sub(r'\(\s*\)', '', t)
    t = re.sub(r' {2,}', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = t.strip()
    return t or ""
