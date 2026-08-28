"""CHAT-path honesty hard gate — no ungrounded factual answer ships without a hedge.

Every CHAT final response must either:
  1. Pass deterministic grounding (evidence_used / render_action), OR
  2. Carry an explicit hedge + uncertainty marker.

Disable with ELI_CHAT_HONESTY_GATE=0.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

UNCERTAINTY_MARKER = "⚠ uncertain:"

_HEDGE_MARKERS = (
    "can't verify", "cannot verify", "won't guess", "wont guess", "not certain",
    "i'm not sure", "im not sure", "don't have grounded", "rather not guess",
    "won't invent", "wont invent", "couldn't ground", "could not ground",
)

_SURFACE_ACTIONS = frozenset({
    "SELF_REPORT", "RUNTIME_AUDIT", "IMPORT_AUDIT", "RESOLVE_RUNTIME_PATHS",
    "GUI_RUNTIME_AUDIT", "EXPLAIN_MEMORY_RUNTIME", "MEMORY_STATUS",
    "PERSONAL_MEMORY_DEEP_EXPLAIN", "EXPLAIN_COGNITION_RUNTIME",
    "EXPLAIN_LAST_RESPONSE", "SELF_ANALYZE", "SELF_IMPROVE", "RUNTIME_STATUS",
})


def _enabled() -> bool:
    return os.environ.get("ELI_CHAT_HONESTY_GATE", "1").strip().lower() not in (
        "0", "false", "no", "off")


def has_hedge_marker(text: str) -> bool:
    low = str(text or "").lower()
    return any(m in low for m in _HEDGE_MARKERS) or UNCERTAINTY_MARKER.lower() in low


def _surface_action_for_input(user_input: str) -> str | None:
    """Map runtime/identity/memory questions mis-routed to CHAT → surface action."""
    low = str(user_input or "").lower()
    try:
        from eli.runtime.deterministic_grounding_gate import (
            _eli_v14_identity_question,
            _eli_v14_runtime_question,
        )
        if _eli_v14_runtime_question(user_input):
            return "SELF_REPORT"
        if _eli_v14_identity_question(user_input):
            return "SELF_REPORT"
    except Exception:
        log.debug("surface action detection skipped", exc_info=True)

    if re.search(r"\bwho are you\b", low) and "who am i" not in low:
        return "SELF_REPORT"
    if "personal memory" in low or "what do you remember about me" in low:
        return "PERSONAL_MEMORY_DEEP_EXPLAIN"
    if "memory system" in low or "how does your memory" in low:
        return "EXPLAIN_MEMORY_RUNTIME"
    if "last response" in low and ("confidence" in low or "why did you" in low):
        return "EXPLAIN_LAST_RESPONSE"
    return None


def _try_deterministic_render(user_input: str, mode_label: str) -> str | None:
    action = _surface_action_for_input(user_input)
    if not action or action not in _SURFACE_ACTIONS:
        return None
    try:
        from eli.runtime.deterministic_grounding_gate import render_action
        rendered = render_action(action, {}, user_input, mode_label=mode_label)
        text = str(rendered or "").strip()
        if text and not text.startswith('{"surface": "missing_deterministic_renderer"'):
            return text
    except Exception:
        log.debug("deterministic render in honesty gate failed", exc_info=True)
    return None


def apply_chat_honesty_gate(
    response: str,
    *,
    user_input: str = "",
    evidence_used: bool = False,
    grounding_confidence: float | None = None,
    reasoning_mode: str | None = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Apply the CHAT honesty gate.

    Returns (response_text, response_mode, meta_patch).
    response_mode ∈ {"grounded", "deterministic", "ungrounded_hedge", "uncertain", "synthesized"}.
    """
    text = str(response or "").strip()
    meta: Dict[str, Any] = {}

    if not _enabled():
        return text, "grounded" if evidence_used else "synthesized", meta

    mode_label = str(reasoning_mode or "quick")

    # Already hedged — ship as-is.
    if has_hedge_marker(text):
        return text, "ungrounded_hedge", {"honesty_gate": "passed_hedge"}

    # Grounded with evidence — ship as-is.
    if evidence_used:
        return text, "grounded", {"honesty_gate": "passed_evidence"}

    # Mis-routed surface question → deterministic render instead of synthesis.
    det = _try_deterministic_render(user_input, mode_label)
    if det:
        return det, "deterministic", {"honesty_gate": "deterministic_render"}

    # Factual checkable turn without evidence → hedge floor.
    try:
        from eli.runtime.grounding_escalation import classify_factual, _hedge
        from eli.core.config import network_allowed

        is_fact, domain = classify_factual(user_input)
        if is_fact:
            gconf = float(grounding_confidence if grounding_confidence is not None else 0.0)
            # Hard floor: ungrounded factual → full hedge.
            if gconf < 0.55:
                hedged = _hedge(domain, network_allowed(), searched=False)
                marked = f"{UNCERTAINTY_MARKER} {hedged}"
                return marked, "ungrounded_hedge", {
                    "honesty_gate": "forced_hedge",
                    "grounding_confidence": gconf,
                    "fact_domain": domain,
                }
            # Mid-grounding: prepend uncertainty marker, keep synthesis.
            if gconf < 0.75 and not has_hedge_marker(text):
                marked = f"{UNCERTAINTY_MARKER} {text}"
                return marked, "uncertain", {
                    "honesty_gate": "uncertainty_marker",
                    "grounding_confidence": gconf,
                    "fact_domain": domain,
                }
    except Exception:
        log.debug("factual classification in honesty gate skipped", exc_info=True)

    return text, "synthesized", {"honesty_gate": "passed_synthesized"}
