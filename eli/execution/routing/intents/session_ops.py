"""Session-level controls: continuity contract, persona lock, history, help.

Runs last, immediately before route()'s own CHAT fallback.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import re

from eli.execution.routing._helpers import (
    _mk,
)


def route_session_ops(text: str, raw: str, low: str, text_l: str):
    _low_raw = str(raw or "").lower()
    if any(_p in _low_raw for _p in (
        "one answer", "short answer", "short answers", "terribly short",
        "lobotom", "continuity", "awareness", "reasoning mode",
        "constitutional", "chain of thought", "cot", "pipeline",
        "fallback", "runtime audit", "runtime diagnostic",
        "what is with", "what's with", "why are you"
    )):
        return _mk(
            "CHAT",
            {"message": raw},
            0.92,
            matched_by="chat.meta_continuity_diagnostic",
            meta_extra={
                "requires_plan": True,
                "requires_memory": True,
                "quick_direct_allowed": False,
                "response_contract": "nonquick_continuity_diagnostic",
            },
        )

    # ── Persona lock ──────────────────────────────────────────────────────────
    # STATUS must be checked first — "persona lock status" contains "persona lock"
    # which would be caught by the SET regex if ordered after it.
    if re.search(r"\bpersona\s+lock\s+status\b|\bcheck\s+persona\s+lock\b|\bis\s+persona\s+locked\b", low):
        return _mk("PERSONA_LOCK_STATUS", {}, 0.92, matched_by="persona.lock_status")

    if re.search(r"\bunlock\s+persona\b|\bclear\s+persona\s+lock\b|\bpersona\s+unlock\b|"
                 r"\bremove\s+persona\s+lock\b|\breset\s+persona\s+lock\b", low):
        return _mk("PERSONA_LOCK_CLEAR", {}, 0.92, matched_by="persona.lock_clear")

    if re.search(r"\bpersona\s+lock\b|\block\s+persona\b|\bset\s+persona\s+lock\b", low):
        _model_arg = {}
        _m_model = re.search(r"\bto\s+([a-zA-Z0-9_:/-]+\.(?:gguf|bin|ggml))\b", low)
        if _m_model:
            _model_arg["model"] = _m_model.group(1)
        return _mk("PERSONA_LOCK_SET", _model_arg, 0.92, matched_by="persona.lock_set")

    # ── Skip YouTube ad ───────────────────────────────────────────────────────
    if re.search(r"\bskip\s+(?:the\s+)?(?:youtube\s+)?ad\b|\bskip\s+this\s+ad\b|"
                 r"\bbypass\s+(?:the\s+)?ad\b|\bskip\s+advertisement\b", low):
        return _mk("SKIP_YOUTUBE_AD", {}, 0.95, matched_by="media.skip_ad")

    # ── Clear chat history ───────────────────────────────────────────────────
    if re.search(r"\bclear\s+(?:chat|conversation|history|our\s+conversation)\b|"
                 r"\breset\s+(?:chat|conversation|history)\b|\berase\s+(?:chat|history)\b|"
                 r"\bwipe\s+(?:chat|conversation|history)\b", low):
        return _mk("CLEAR_CHAT_HISTORY", {}, 0.93, matched_by="system.clear_chat")

    # ── Listen for command ───────────────────────────────────────────────────
    if re.search(r"\b(listen\s+for\s+(?:a\s+)?command|start\s+listening|listen\s+for\s+speech|"
                 r"activate\s+(?:voice|speech)\s+input|begin\s+listening)\b", low):
        _timeout = 5.0
        _m_to = re.search(r"(\d+)\s*(?:second|sec|s)\b", low)
        if _m_to:
            _timeout = float(_m_to.group(1))
        return _mk("LISTEN_FOR_COMMAND", {"timeout": _timeout}, 0.90, matched_by="voice.listen_for_command")

    # ── Help ─────────────────────────────────────────────────────────────────
    if re.search(r"^\s*(?:help|commands?|what\s+(?:can\s+i|commands?|actions?)|"
                 r"what\s+(?:do\s+you|are\s+your)\s+(?:command|capability|action)|"
                 r"show\s+(?:me\s+)?(?:help|commands?|capabilities?)|list\s+command)\b", low):
        return _mk("HELP", {}, 0.90, matched_by="system.help")
    return None
