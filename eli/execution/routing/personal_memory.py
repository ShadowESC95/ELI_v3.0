"""Router personal-memory mode-aware fix — personal/profile-memory questions need
synthesis (not raw truth-reports or search-plugin output). Moved from router_enhanced.py."""
from __future__ import annotations

import re as _eli_pm_re

from eli.utils.log import get_logger
from eli.execution.routing._helpers import _mk  # noqa: F401

_SWLOG = get_logger(__name__)


def _eli_self_improvement_phrase_guard(*a, **k):
    from eli.execution.router_enhanced import _eli_self_improvement_phrase_guard as _f
    return _f(*a, **k)


# ELI_PERSONAL_MEMORY_MODE_AWARE_ROUTER_FIX_20260505
# High-priority route wrapper: personal memory questions need synthesis, not raw truth reports/search.


def _eli_pm_mk(action, args=None, confidence=0.99, matched_by="eli.personal_memory_mode_aware"):
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

def _eli_pm_wants_raw_memory_truth(low):
    return bool(_eli_pm_re.search(
        r"\b(memory truth report|memory count|how many memories|memory status|memory runtime status|raw counts?|db counts?|diagnostic counts?)\b",
        low,
    ))

def _eli_pm_wants_personal_memory(low):
    if _eli_pm_wants_raw_memory_truth(low):
        return False

    has_memory = bool(_eli_pm_re.search(r"\b(memory|remember|stored memories|what do you know about me|what you know about me|actual(?:ly)? remember)\b", low))
    has_depth = bool(_eli_pm_re.search(
        r"\b(full|in[- ]?depth|personalised|personalized|properly|not quick|not in quick mode|"
        r"stop giving me data dumps|data dumps|what you actually remember|about me|which files|db tables|functions|internally|cognition pipeline)\b",
        low,
    ))
    return has_memory and has_depth

def _eli_pm_pre_route(text):
    raw = str(text or "").strip()
    low = raw.lower()

    if _eli_pm_re.search(r"\bwhy\b.*\b(browser|web|online|search)\b", low) or _eli_pm_re.search(r"\bwhy.*go.*browser\b", low):
        return _eli_pm_mk(
            "ROUTING_FAULT_EXPLAIN",
            {"question": raw},
            0.995,
            "routing_fault.browser_complaint",
        )

    # "why did that one work and the others didn't", "why did you fail to open
    # downloads", "why was that routed wrong" — questions about ELI's own
    # dispatch. This runs in the PRE-route, ahead of chat.long_question_guard,
    # which would otherwise force anything 12+ words to CHAT; answered as chat
    # with no trace attached, the model invented a cause (it blamed a typo for
    # an app the user never named). ROUTING_FAULT_EXPLAIN answers from the
    # actual router result instead.
    if (
        _eli_pm_re.search(
            r"\bwhy\b[^?]*\b(?:did|does|didn'?t|do)\b[^?]*"
            r"\b(?:that|this|it|those|the\s+(?:others?|first|last|previous|other\s+ones?))\b"
            r"[^?]*\b(?:work(?:ing|ed)?|fail(?:ed)?|open|run|route[ds]?)\b", low)
        or _eli_pm_re.search(r"\bwhy\b[^?]*\b(?:fail(?:ed)?|not\s+work|didn'?t\s+work|did\s+not\s+work)\b", low)
        or _eli_pm_re.search(r"\bwhy\b[^?]*\b(?:route[ds]?|routing|mis-?rout\w*)\b", low)
    ):
        return _eli_pm_mk(
            "ROUTING_FAULT_EXPLAIN",
            {"question": raw},
            0.98,
            "routing_fault.dispatch_question",
        )

    if _eli_pm_re.search(r"\bstop giving me data dumps\b|\bwe are not in quick mode\b|\bfull and personalised response\b|\bfull and personalized response\b", low):
        return _eli_pm_mk(
            "PERSONAL_MEMORY_DEEP_EXPLAIN",
            {"question": raw, "reason": "user_rejected_data_dump"},
            0.995,
            "memory.personalized_no_data_dump",
        )

    if _eli_pm_wants_personal_memory(low):
        return _eli_pm_mk(
            "PERSONAL_MEMORY_DEEP_EXPLAIN",
            {"question": raw},
            0.99,
            "memory.personalized_deep_explain",
        )

    return None





# --- ELI high-priority self-improvement route guard ---
