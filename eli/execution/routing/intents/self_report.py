"""Self-report and grounded-diagnostics contracts.

Questions about what ELI actually did — updates, latency, inference state — which must reach real evidence rather than be improvised in chat.

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


def route_self_report(text: str, raw: str, low: str, text_l: str):
    _introspective_subject = re.search(
        r"\b(memory continuity|how you feel|how you'?re feeling|your feelings?|your mood|"
        r"your awareness|your (?:consciousness|experience|thoughts|continuity|wellbeing|"
        r"head|mind|state of mind))\b",
        low,
    )
    _dev_update_subject = re.search(
        r"\b(code|git|commit|version|build|patch|capabilit|module|test|upgrade|"
        r"checks?|updates?|maintenance|deploy)\b",
        low,
    )
    if _introspective_subject and not _dev_update_subject:
        pass  # fall through to reflective CHAT / introspection routing
    elif re.search(
        r"\b(what|which|any|show|tell|list)\b.{0,80}\b(updates?|checks?|repairs?|changes?|maintenance|work)\b.{0,80}\b(as of late|recently|lately|performed|done|happened|made)\b",
        low,
    ) or re.search(
        r"\b(what have you been doing|what have you been working on|what have you checked|what have you updated)\b",
        low,
    ):
        return {
            "action": "SELF_REPORT",
            "args": {
                "question": raw,
                "self_report_scope": "recent_updates",
            },
            "confidence": 0.995,
            "meta": {
                "matched_by": "router.primary_contract_diagnostics.self_report_recent_updates",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_report_runtime",
                "forbid_chat_fallback": True,
                "forbid_fake_update_claims": True,
            },
        }
    # --- end primary contract diagnostics: self-report recent updates ---

    # --- strict control actions: never fall through to generic CHAT ---    # ELI_PATCH_PERSONAL_MEMORY_ROUTE_PRECEDENCE_20260511
    # Personalised/internal-memory questions must beat the generic memory-runtime route.
    # This is routing precedence only; final response synthesis remains downstream.
    _pm_text = low  # `low` is always defined: low = raw.lower()
    if (
        ("memory" in _pm_text)
        and (
            "personal" in _pm_text
            or "personalised" in _pm_text
            or "personalized" in _pm_text
            or "about me" in _pm_text
            or "my memory" in _pm_text
            or "internals" in _pm_text
            or "internally" in _pm_text
            or "which files" in _pm_text
            or "which db" in _pm_text
            or "which tables" in _pm_text
            or "which functions" in _pm_text
        )
    ):
        return {
            "action": "PERSONAL_MEMORY_DEEP_EXPLAIN",
            "confidence": 0.99,
            "source": "router.personal_memory_precedence",
            "reason": "personalised/internal memory explanation must not be captured by generic memory runtime route",
            "query": text if 'text' in locals() else (_pm_text or ""),
        }

    # Tolerate natural leading filler ("do a", "run a", "please", "now") and
    # trailing punctuation so "do a self update" still routes to SELF_UPDATE
    # instead of falling through to CHAT (which then confabulates "I'm updating
    # myself now" — a fake action). "self upgrade" intentionally does NOT match
    # here; it routes to SELF_UPGRADE further down.
    if re.fullmatch(
        r"(?:(?:please|pls|can you|could you|now)\s+)*"
        r"(?:(?:do|run|perform|execute|go)\s+(?:a|an|the)?\s*)?"
        r"(?:self[- ]?update|update yourself|refresh yourself|refresh all overlays)"
        r"(?:\s+now)?[.!\s]*",
        low):
        return _mk("SELF_UPDATE", {}, 0.99, matched_by="router.self_update", allow_chat_without_evidence=False)

    if re.search(r"\b(confidence in (?:your|my) last response|which agents contributed|what agents contributed|last response trace|previous response trace|last turn trace)\b", low):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.99, matched_by="router.explain_last_response", allow_chat_without_evidence=False)

    # A request for the RAW metrics / agent logs / telemetry / trace of a recent
    # cycle must surface the real logged telemetry (agent_dispatches + runtime_events
    # via EXPLAIN_LAST_RESPONSE), NOT the settings snapshot SELF_REPORT returns. A
    # real session asked "I want raw metric breakdowns and agent logs for that cycle"
    # → SELF_REPORT → ELI wrongly claimed "I have no logs" and confabulated.
    if (re.search(r"\b(metric|metrics|telemetry|agent log|agent logs|raw log|raw logs|"
                  r"dispatch log|breakdown|trace|logs?)\b", low)
            and re.search(r"\b(that|this|last|previous|prior|the)\s+"
                          r"(cycle|response|turn|request|run|answer|reply)\b", low)
            and not re.search(r"\b(delete|clear|disable|turn off|stop)\b", low)):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.98,
                   matched_by="router.explain_last_response.metrics",
                   need_grounding=True, allow_chat_without_evidence=False)

    # --- explicit grounded speech-act route authority ---
    # This is routing authority, not a canned response layer.
    if re.search(r"\b(i did not ask|i didn't ask|not what i asked|that's not what i asked|that is not what i asked)\b", low):
        return _mk("CHAT", {"message": raw}, 0.92, matched_by="router.correction_chat", allow_chat_without_evidence=True)

    if re.search(r"\b(who are you|what are you|what is your name|what's your name)\b", low) and re.search(
        r"\b(running on|model|context size|gpu layers|threads|batch|provider|runtime)\b",
        low,
    ):
        return _mk("SELF_REPORT", {}, 0.99, matched_by="router.self_report_with_runtime", allow_chat_without_evidence=False)

    if re.search(r"\b(what are you actually running on|running on right now|context size|gpu layers|threads|batch|provider|runtime status)\b", low):
        return _mk("RUNTIME_STATUS", {}, 0.99, matched_by="router.runtime_status", allow_chat_without_evidence=False)

    # Self-referential temperature: "what's your temperature", "your temperature out now",
    # "what is your inference temperature", "what temp are you running at" etc.
    # Must fire BEFORE the weather prepass which also matches "temperature".
    if re.search(
        r"\b(?:your|eli(?:'s)?|its)\s+(?:inference\s+)?temp(?:erature)?\b"
        r"|\binference\s+temp(?:erature)?\b"
        r"|\bgeneration\s+temp(?:erature)?\b"
        r"|\btemp(?:erature)?\s+(?:(?:are\s+)?you(?:'re|\s+are)?\s+(?:running|at|set|using|on)|out\s+now|setting|parameter|value|right\s+now|currently)\b",
        low,
    ):
        return _mk("RUNTIME_STATUS", {}, 0.97, matched_by="router.self_temp_runtime_status", allow_chat_without_evidence=False)

    if re.search(r"\b(explain exactly how your memory system works internally|memory system works internally|which files.*which db tables.*which functions|memory runtime surface|memory runtime)\b", low):
        return _mk("EXPLAIN_MEMORY_RUNTIME", {}, 0.99, matched_by="router.memory_runtime", allow_chat_without_evidence=False)

    if "memory" in low and re.search(
        r"\b(db paths?|database|sqlite|tables?|functions?|internally|semantic|rag|faiss|fts5|vector(?:ing|s)?|index(?:ing|es)?|hyde|short[- ]?term|long[- ]?term)\b",
        low,
    ):
        return _mk("EXPLAIN_MEMORY_RUNTIME", {}, 0.99, matched_by="router.memory_runtime_architecture", allow_chat_without_evidence=False)

    if re.search(r"\b(explain your cognition pipeline|cognition pipeline from input to output|input to output.*every step|cognitive pipeline)\b", low):
        return _mk("EXPLAIN_COGNITION_RUNTIME", {}, 0.99, matched_by="router.cognition_runtime", allow_chat_without_evidence=False)

    # Self-referential: "how do your AGENTS work/function/operate", "explain your agents",
    # "how does your agent bus work", "search your code and tell me how your agents work".
    # This is a question about ELI's OWN agent architecture — answered by the grounded cognition
    # report (the agent bus, the 14 agents + roles, the ThreadPoolExecutor). MUST fire here,
    # before: (a) web_search — 'search your code…' was web-searched and returned external VS Code
    # articles; (b) system.capabilities — 'your agents' dumped the raw 207-ACTION list (agents ≠
    # capabilities). Routes to EXPLAIN_COGNITION_RUNTIME.
    # …UNLESS the user is asking ELI to PRODUCE an artifact about its internals
    # ("generate/write/create a document/report about your agent bus"). That is a
    # generative request (GENERATE_DOCUMENT), not a runtime-dump request — the
    # introspection guard used to swallow it because it merely mentions "agent bus".
    _doc_gen_intent = bool(re.search(
        r"\b(generate|create|write|make|draft|produce|compose|prepare|build)\b"
        r".{0,30}\b(document|doc|report|essay|article|paper|write-?up|brief|memo|"
        r"guide|overview|summary|story|blog|post|readme)\b", low))
    if not _doc_gen_intent and (
        re.search(r"\b(?:how|what|explain|describe|tell me|show me)\b.{0,40}\b(?:your|the|eli'?s)\s+agents?\b", low)
        or re.search(r"\b(?:your|the|eli'?s)\s+agents?\b.{0,40}\b(work|works|function|functions|operate|operates|run|runs|behave|coordinate|interact|process)\b", low)
        or re.search(r"\bagent\s+bus\b", low)
        or re.search(r"\bhow\s+(?:do|does|the\s+(?:fuck|hell|heck)\s+do)\b.{0,30}\bagents?\b", low)
        or (re.search(r"\bsearch\s+(?:your|the|my)\s+(?:own\s+)?code", low)
            and re.search(r"\b(agents?|cognition|memory|pipeline|reasoning)\b", low))
    ):
        return _mk("EXPLAIN_COGNITION_RUNTIME", {"question": raw}, 0.99,
                   matched_by="router.agents_architecture", allow_chat_without_evidence=False)

    if re.search(r"\b(what do you know about me from memory|what do you know about me|what do you remember of me|who am i|what is my name|do you remember me|do you know me|my preferences|my persona|my ethos)\b", low):
        return _mk("USER_IDENTITY_SUMMARY", {}, 0.99, matched_by="router.user_identity_summary", allow_chat_without_evidence=False)

    # Pure persona questions (no runtime keywords) → CHAT so ELI answers from
    # its own character and memory, not a raw JSON spec dump.
    if re.search(r"\b(who are you|what are you(?!\s+\w)|what is your name|what's your name|tell me about yourself|what is your purpose)\b", low) and not re.search(r"\b(model|running on|provider|context|gpu|llm|specs?|technical|runtime|layers|threads|batch)\b", low):
        return _mk("CHAT", {"message": raw}, 0.99, matched_by="router.identity_persona_chat", allow_chat_without_evidence=True)
    # Technical model/runtime queries → SELF_REPORT (actual spec evidence needed)
    if re.search(r"\b(what model(?: are you| do you use| is this)?|which model(?: are you| do you use| is this)?|what llm|which llm|what are you running on|what are you actually)\b", low):
        return _mk("SELF_REPORT", {}, 0.99, matched_by="router.self_report", allow_chat_without_evidence=False)
    # --- end explicit grounded speech-act route authority ---

    # --- dynamic evidence preempts ---
    if re.search(r"\b(who are you.*running on right now|model.*context size.*gpu layers|what are you actually running on right now|you know who you are)\b", low):
        return _mk("CHAT", {"message": raw}, 0.95, matched_by="router.runtime_status_to_chat", allow_chat_without_evidence=False)

    if re.search(r"\b(what do you know about me from memory|what do you know about me|what do you remember of me|who am i|what is my name|do you remember me)\b", low):
        return _mk("CHAT", {"message": raw}, 0.95, matched_by="router.memory_status_to_chat", allow_chat_without_evidence=False)

    if re.search(r"\b(explain exactly how your memory system works internally|memory system works internally|which files.*which db tables.*which functions)\b", low):
        return _mk("EXPLAIN_MEMORY_RUNTIME", {}, 0.99, matched_by="router.memory_runtime_to_grounded", allow_chat_without_evidence=False)

    if re.search(r"\b(run a full runtime audit|runtime audit.*broken or missing)\b", low):
        return _mk("RUNTIME_AUDIT", {}, 0.99, matched_by="router.runtime_audit", allow_chat_without_evidence=False, need_grounding=True, task_family="grounded_audit")

    if re.search(r"\b(what imports are failing|what imports are missing|imports are failing or missing)\b", low):
        return _mk("IMPORT_AUDIT", {}, 0.99, matched_by="router.import_audit", allow_chat_without_evidence=False, need_grounding=True, task_family="grounded_audit")

    if re.search(
        r"\b(diagnose\s+wrappers?|show\s+(?:the\s+)?(?:executor\s+)?(?:wrapper|middleware)\s+(?:chain|stack|table)|"
        r"what\s+wraps?\s+(?:the\s+)?executor|executor\s+middleware\s+(?:chain|stack|table)|"
        r"list\s+(?:the\s+)?executor\s+wrappers?)\b",
        low,
    ):
        return _mk(
            "DIAGNOSE_WRAPPERS", {}, 0.99,
            matched_by="router.diagnose_wrappers",
            allow_chat_without_evidence=False,
            need_grounding=True,
            task_family="grounded_audit",
        )

    if re.search(r"\b(show me the resolved runtime paths|resolved runtime paths|runtime paths for every critical file|critical file you depend on)\b", low):
        return _mk("RESOLVE_RUNTIME_PATHS", {}, 0.99, matched_by="runtime.paths.preempt", allow_chat_without_evidence=False)

    if re.search(r"\b(audit your gui file|gui runtime audit|gui file.*wired incorrectly)\b", low):
        return _mk(
            "GUI_RUNTIME_AUDIT",
            {},
            0.99,
            matched_by="router.gui_runtime_audit",
            allow_chat_without_evidence=False,
            need_grounding=True,
            task_family="grounded_audit",
        )

    return None
