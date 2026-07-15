"""Router live-route second-fix — high-priority pre-route for cases the base
router lets fall into CHAT/search (reasoning-mode status, memory-runtime,
window-grid follow-up). Moved from router_enhanced.py."""
from __future__ import annotations

import re as _eli_lrf_re

from eli.utils.log import get_logger
from eli.execution.routing._helpers import _mk  # noqa: F401  (globals().get("_mk"))

_SWLOG = get_logger(__name__)




def _eli_lrf_mk(action, args=None, confidence=0.99, matched_by="eli.live_route_second_fix"):
    mk = globals().get("_mk")
    if callable(mk):
        try:
            return mk(action, args or {}, confidence, matched_by=matched_by, allow_chat_without_evidence=False)
        except TypeError:
            try:
                return mk(action, args or {}, confidence, matched_by=matched_by)
            except TypeError:
                _SWLOG.debug("suppressed exception", exc_info=True)
    return {
        "action": action,
        "args": args or {},
        "confidence": confidence,
        "meta": {"matched_by": matched_by, "allow_chat_without_evidence": False},
    }

def _eli_lrf_word_to_int(s):
    s = str(s or "").strip().lower()
    table = {
        "one": 1, "two": 2, "to": 2, "too": 2,
        "three": 3, "tree": 3, "free": 3,
        "four": 4, "for": 4, "fore": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
    }
    if s.isdigit():
        return int(s)
    return table.get(s)

def _eli_lrf_pre_route(text):
    from eli.execution.router_enhanced import _eli_phase10_is_codebase_audit_request
    raw = str(text or "").strip()
    low = raw.lower().strip()
    low = low.replace("×", "x")

    # Exact/near-exact mode query: status action only, not full RUNTIME_STATUS.

    if _eli_phase10_is_codebase_audit_request(raw):
        return _mk(
            "RUNTIME_AUDIT",
            {"query": raw, "audit_depth": "codebase"},
            0.99,
            matched_by="phase10.codebase_audit_guard",
            allow_chat_without_evidence=False,
            need_grounding=True,
            task_family="grounded_audit",
        )

    # Only report current mode status if the query is specifically about the active/current mode.
    # Queries asking about ALL modes, explaining modes, differences, etc. must reach CHAT/synthesis.
    _lrf_asking_all_modes = _eli_lrf_re.search(
        r"\b(all|every|each|how many|list|explain|full|describe|detail|difference|differ|compare|what are|tell me about|tell me all|tell me everything|what do|how do|modes?\s+you\s+have|modes?\s+does)\b",
        low,
    )
    # A frustrated correction that merely MENTIONS a mode ("you are not in quick mode",
    # "what are you talking about", "that's not right") must NOT trigger the all-modes
    # explainer — that's a relational CHAT turn, not a request to list the modes.
    _is_mode_correction = bool(_eli_lrf_re.search(
        r"\byou('?re| are| were)\s+not\b|\bnot\s+in\b|\bwhat\s+are\s+you\s+talking\s+about\b"
        r"|\bthat('?s| is| was)\s+not\b|\byou\s+said\b|\bnot\s+(?:quick|normal|advanced|research|expert)\b",
        low))
    # Require an actual modes TOPIC ("reasoning mode(s)" or plural "modes"), not a bare
    # singular "<x> mode" mention, so "quick mode" in passing doesn't match.
    if (_lrf_asking_all_modes and not _is_mode_correction
            and _eli_lrf_re.search(r"\b(reasoning modes?|modes)\b", low)):
        return _eli_lrf_mk(
            "EXPLAIN_ALL_REASONING_MODES", {},
            0.99,
            "reasoning.all_modes_grounded.second_fix",
        )
    if "reasoning mode" in low and not _lrf_asking_all_modes and not _eli_lrf_re.search(
        r"\b(cognition pipeline|input to output|every step|memory system|db tables|functions|files|runtime audit|diagnostic|diagnostics|full audit)\b",
        low,
    ):
        return _eli_lrf_mk("REASONING_MODE_STATUS", {}, 0.995, "reasoning.mode_status.second_fix")

    # Internal memory architecture question must not become document/search plugin output.
    if _eli_lrf_re.search(r"\b(memory system|memory internally|memory.*db tables|db tables|which files|which functions)\b", low) and _eli_lrf_re.search(r"\b(memory|db|sqlite|faiss|functions?|files?|internally|runtime)\b", low):
        return _eli_lrf_mk(
            "EXPLAIN_MEMORY_RUNTIME",
            {"question": raw, "detail": "full"},
            0.985,
            "memory.runtime_architecture.second_fix",
        )

    # Bare grid follow-up, including STT's "tree" for "three" and optional trailing "grid/layout/windows".
    m = _eli_lrf_re.fullmatch(
        r"\s*(\d{1,2}|one|two|three|tree|four|five|six|seven|eight)\s*(?:x|by)\s*(\d{1,2}|one|two|three|tree|four|five|six|seven|eight)\s*(?:grid|layout|windows?)?\s*",
        low,
    )
    if m:
        cols = _eli_lrf_word_to_int(m.group(1))
        rows = _eli_lrf_word_to_int(m.group(2))
        if cols and rows and 1 <= cols <= 8 and 1 <= rows <= 8:
            return _eli_lrf_mk(
                "TILE_WINDOWS",
                {"cols": cols, "rows": rows, "grid": [cols, rows]},
                0.995,
                "window.grid_followup.second_fix",
            )

    return None
