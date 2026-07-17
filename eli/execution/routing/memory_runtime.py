"""Router memory-runtime intents — cognition-failure guard, recent-memory-processing and self-report-recent-updates questions, and the memory-runtime route lock. Moved from router_enhanced.py."""
from __future__ import annotations

import re as _eli_recent_mem_re
import re


def _eli_runtime_cognition_failure_guard(text):
    raw = str(text or "")
    low = raw.lower().strip()

    # "how many layers are you using?" means GPU offload layers in ELI's own
    # runtime — n_gpu_layers is live in the GPU_STATUS snapshot. Left to CHAT it
    # confabulated (a real session answered with After Effects compositing
    # advice), and "are you using the gpu?" drew "I am running on CPU, not GPU"
    # while 11 layers were offloaded. Grounded report, not a guess.
    _gpu_layer_question = (
        re.search(r"\blayers?\b", low)
        and re.search(r"\b(you|your|offload\w*|gpu|vram|cuda|model|running|loaded|use|using)\b", low)
        and not re.search(r"\b(image|photo|photoshop|after\s?effects|composit\w*|design|"
                          r"canvas|css|z-index|onion\s?skin)\b", low)
    )
    if re.search(r"\bnvidia-smi\b", low) or _gpu_layer_question or (
        re.search(r"\b(gpu|vram|cuda|nvidia)\b", low)
        and re.search(r"\b(stat|stats|status|diagnostic|diagnostics|usage|using|use|memory|"
                      r"performance|running on|running|offload\w*|accelerat\w*|"
                      r"tell me what it means|temp|temperature|how hot)\b", low)
    ):
        return {
            "action": "GPU_STATUS",
            "args": {"question": raw, "explain": True},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.gpu_status_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_diagnostic",
            },
        }

    if re.search(r"\b(full\s+)?runtime\s+audit\b", low) or re.search(r"\bwhat'?s?\s+actually\s+(broken|missing)\b", low):
        return {
            "action": "RUNTIME_AUDIT",
            "args": {},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.runtime_audit_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    if re.search(r"\bcognition\s+pipeline\b", low) or re.search(r"\binput\s+to\s+output\b", low):
        return {
            "action": "EXPLAIN_COGNITION_RUNTIME",
            "args": {},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.cognition_pipeline_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    # SELF_ANALYZE inspects ELI's OWN failure log — so require a self-reference
    # ("your/you/eli"), not a bare "recent failures" / "root cause" which over-matched
    # questions about the user's own code/project.
    if (re.search(r"\b(your|you'?ve had|eli'?s)\s+(?:recent\s+)?failures?\b", low)
            or re.search(r"\bactual root cause of (?:your|eli'?s|the last)\b", low)
            or re.search(r"\banalyse?\s+(?:your|eli'?s)\s+(?:own\s+)?(?:recent\s+)?failures?\b", low)):
        return {
            "action": "SELF_ANALYZE",
            "args": {},
            "confidence": 0.98,
            "meta": {
                "matched_by": "eli.failure_analysis_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    return None


def _eli_recent_memory_processing_question(text: object) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False

    # Preserve existing count-only route.
    if _eli_recent_mem_re.search(r"\b(how many|count|number of)\b.{0,40}\b(memories|memory rows|memory entries)\b", low):
        return False

    # "processed"/"processing" are deliberately NOT memory terms. They also
    # appear in recent_terms below, so counting them here collapsed the two-signal
    # AND at the bottom of this function into a bare "processing" + "you" test —
    # which routed behavioural complaints ("you're not processing my questions
    # properly, are you okay?") to a SQLite memory inventory. The explicit
    # patterns below still catch "what memories have you been processing".
    memory_terms = _eli_recent_mem_re.search(
        r"\b(memories|memory|remembered|remembering|learning|stored|recalled|recall)\b",
        low,
    )
    recent_terms = _eli_recent_mem_re.search(
        r"\b(lately|recently|latest|last|currently|been|processing|working on|what .* processing)\b",
        low,
    )

    patterns = [
        r"\bwhat\s+memories\s+have\s+you\s+been\s+processing\b",
        r"\bwhat\s+memory\s+have\s+you\s+been\s+processing\b",
        r"\bwhat\s+have\s+you\s+been\s+remembering\b",
        r"\bwhat\s+have\s+you\s+remembered\s+(?:recently|lately)\b",
        r"\bwhat\s+have\s+you\s+been\s+learning\s+(?:recently|lately)\b",
        r"\bwhat\s+memories\s+(?:did|do)\s+you\s+(?:process|have)\s+(?:recently|lately)\b",
        r"\bwhat\s+recent\s+memories\b",
        r"\bshow\s+(?:me\s+)?recent\s+memories\b",
        r"\blatest\s+memory\s+(?:activity|processing|updates)\b",
        r"\brecent\s+memory\s+(?:activity|processing|updates)\b",
    ]
    if any(_eli_recent_mem_re.search(p, low) for p in patterns):
        return True

    return bool(memory_terms and recent_terms and "you" in low)


def _eli_self_report_recent_updates_question(text):
    low = str(text or "").strip().lower()
    if not low:
        return False

    self_ref = any(x in low for x in (
        "eli",
        "yourself",
        "about yourself",
        "your runtime",
        "your system",
    ))

    update_ref = any(x in low for x in (
        "what updates",
        "which updates",
        "updates and checks",
        "checks have been",
        "checks were",
        "performed as of late",
        # NOTE: bare "as of late" removed — it fires on casual idioms like
        # "are you saying i'm obsessive these days as of late".
        # "performed as of late" above is specific enough.
        "recent checks",
        "routine updates",
        "recent updates",
        "what have you been doing",
        "what have you been processing",
        "what have you been working on",
    ))

    return bool(update_ref and (self_ref or " you " in f" {low} " or " your " in f" {low} "))


def _eli_memory_runtime_route_lock_should_trigger(text):
    import re as _re

    raw = str(text or "").strip()
    low = raw.lower()

    if not low:
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


def _eli_memory_runtime_route_lock_result(raw):
    return {
        "action": "EXPLAIN_MEMORY_RUNTIME",
        "args": {"question": str(raw or ""), "detail": "full"},
        "confidence": 0.995,
        "meta": {
            "matched_by": "eli.memory_runtime_route_lock_v1",
            "need_grounding": True,
            "allow_chat_without_evidence": False,
            "task_family": "memory_runtime",
            "response_contract": "canonical_grounded_memory_runtime_no_raw_gguf",
        },
    }
