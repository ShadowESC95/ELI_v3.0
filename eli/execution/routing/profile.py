"""Router profile/identity scope intents — identity scope, profile-scope contract,
and the preference/inventory/full-dump/memory-count predicates.
Moved from router_enhanced.py."""
from __future__ import annotations


from eli.utils.log import get_logger

log = get_logger(__name__)
_SWLOG = log


def _eli_identity_scope_for_text(text):
    import re as _re
    low = _re.sub(r"\s+", " ", str(text or "").lower()).strip(" .,!?:;")

    if _re.search(r"\b(what is my name|what's my name|do you know my name)\b", low):
        return "name_only"

    if _re.search(r"\b(who am i|who i am)\b", low):
        return "identity_only"

    if _re.search(r"\b(do you remember me|do you know me)\b", low):
        return "memory_presence_only"

    return "identity_only"
# =============================================================================

# =============================================================================
# ELI PROFILE MEMORY SCOPE CONTRACT
# Separates:
#   - identity-only questions
#   - memory-presence questions
#   - generic profile inventory
#   - explicit preference detail requests
#   - full profile dump requests
# No user names are hardcoded here.
# =============================================================================
# ELI_PHASE53_HELPER_TRYBLOCK_HOIST_DIAGNOSTIC_SHELL_RETIREMENT_V1: profile_scope_helpers
# Phase53: helper/import-only Try shell hoisted to module scope.
# The former except branch only printed a diagnostic and swallowed import-time
# helper-definition failure; that stale diagnostic shell is intentionally retired.

def _eli_profile_scope_low(text):
    import re as _re
    return _re.sub(r"\s+", " ", str(text or "").strip().lower())

def _eli_profile_scope_result(action, question, scope, confidence=0.995, matched_by="profile.scope_contract"):
    return {
        "action": action,
        "args": {
            "question": str(question or ""),
            "profile_scope": scope,
        },
        "confidence": confidence,
        "meta": {
            "matched_by": matched_by,
            "profile_scope_contract": scope,
            "active_user_scoped": True,
            "forbid_schema_dump": True,
            "forbid_reflection_spam": True,
            "forbid_news_rows": True,
        },
    }

def _eli_is_explicit_preference_request(low):
    import re as _re
    if _re.search(r"\b(show|list|summari[sz]e|tell me|what are|display|read)\b.{0,80}\b(my|stored|profile)?\s*preferences\b", low):
        return True
    if _re.search(r"\bmy stored preferences\b", low):
        return True
    if low in {"my preferences", "show preferences", "show my preferences"}:
        return True
    return False

def _eli_is_generic_profile_inventory(low):
    return low in {
        "what do you know about me",
        "what do you remember about me",
        "what do you remember of me",
        "do you know me",
    }

def _eli_is_full_profile_dump(low):
    import re as _re
    return bool(_re.search(r"\b(dump|show|print|read|display)\b.{0,80}\b(full|complete|entire|all)\b.{0,80}\b(profile|personal memory|memory profile)\b", low))
# =============================================================================

# =============================================================================
# ELI MEMORY COUNT GROUNDED SYNTHESIS CONTRACT
# Count questions are evidence-backed questions. Quick mode may direct-return.
# Non-quick modes must synthesize from compact evidence and validate the answer.
# =============================================================================
# ELI_PHASE53_HELPER_TRYBLOCK_HOIST_DIAGNOSTIC_SHELL_RETIREMENT_V1: memory_count_helper
# Phase53: helper/import-only Try shell hoisted to module scope.
# The former except branch only printed a diagnostic and swallowed import-time
# helper-definition failure; that stale diagnostic shell is intentionally retired.

def _eli_is_memory_count_question(text):
    import re
    low = str(text or "").strip().lower()
    return bool(
        re.search(r"\b(how many|number of|count)\b.{0,80}\b(memories|memory entries|stored memories|memory rows)\b", low)
        or re.fullmatch(r"(memory count|count memories|count memory|memories count|memory rows)", low)
    )

# =============================================================================
# ELI RECENT MEMORY PROCESSING ROUTE
# Questions like "what memories have you been processing lately?" are not CHAT.
# They require grounded memory/runtime evidence, otherwise the model invents
# plausible-sounding fake memory activity.
# =============================================================================
# ELI_PHASE53_HELPER_TRYBLOCK_HOIST_DIAGNOSTIC_SHELL_RETIREMENT_V1: recent_memory_processing_helper
# Phase53: helper/import-only Try shell hoisted to module scope.
# The former except branch only printed a diagnostic and swallowed import-time
# helper-definition failure; that stale diagnostic shell is intentionally retired.



from eli.utils.log import get_logger
log = get_logger(__name__)
