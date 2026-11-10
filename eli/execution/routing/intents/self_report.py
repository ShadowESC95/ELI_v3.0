"""Self-report and grounded-diagnostics contracts.

Questions about what ELI actually did — updates, latency, inference state — which must reach real evidence rather than be improvised in chat.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import re

# Stems that make a turn a REQUEST rather than a remark. The personal-memory
# precedence rule below is a substring test over a keyword table, and a bare
# substring cannot tell a question from a passing mention: the statement
#
#   "My memory is fine, it is yours that we are concerned about"
#
# contains both "memory" and "my memory", so it routed to
# PERSONAL_MEMORY_DEEP_EXPLAIN at 0.99 and answered a conversational aside with a
# database report — while "my memory" there meant the USER's, in a sentence
# explicitly saying the concern was ELI's. Same failure that once forced
# SELF_REPORT over the question beside it; the fix is the same shape: require
# that something was actually asked.
_ASKING_STEMS = (
    "what", "which", "how", "why", "when", "where", "who", "whose",
    "can you", "could you", "do you", "did you", "are you", "is your",
    "have you", "has your", "show me", "tell me", "list", "explain",
    "describe", "check", "report", "give me", "walk me",
)

# Word-boundary anchored, because a bare `in` test is the very defect this guard
# exists to stop: "it is yourS that we are concerned" contains the substring
# "is your", so the un-anchored version passed the sentence it was written to
# reject.
_ASKING_RE = re.compile(
    r"\b(?:" + "|".join(s.replace(" ", r"\s+") for s in _ASKING_STEMS) + r")\b",
    re.I,
)


_OWN_FACULTY_RE = re.compile(
    r"\b(?:improve|train|boost|sharpen|strengthen|exercise|protect|test|help(?: with)?|fix)\b.{0,24}\bmy memory\b"
    r"|\bmy memory\s+(?:is|has|was|'s|seems|feels|keeps)\s+(?:been\s+)?(?:so\s+|really\s+|very\s+|getting\s+)?"
    r"(?:bad|terrible|awful|poor|rubbish|shot|worse|failing|going|slipping|not what)\b"
    r"|\b(?:losing|lost)\s+my memory\b", re.I)


def _pm_own_faculty(low: str) -> bool:
    """The user means their own memory, the one in their head: "how do i improve my memory",
    "my memory is terrible lately". Nothing ELI has stored is being asked about."""
    return bool(_OWN_FACULTY_RE.search(low or ""))


def _pm_asks_something(low: str) -> bool:
    """True when the turn is a question or an explicit request, not a remark.

    Deliberately generous — a false negative just falls through to CHAT, which
    still answers the user; a false positive replaces their sentence with a
    runtime dump, which is the failure being fixed.
    """
    t = (low or "").strip()
    if not t:
        return False
    if "?" in t:
        return True
    return bool(_ASKING_RE.search(t))

from eli.execution.routing._helpers import _mk, _is_wallclock_question, runtime_status_asked
from eli.execution.routing.memory_runtime import (
    _eli_activity_question_is_about_dev_work,
    _eli_memory_runtime_route_lock_should_trigger,
)
from eli.execution.routing._helpers import (
    _mk,
)


def route_self_report(text: str, raw: str, low: str, text_l: str):
    # ---- WHICH VERSION, WHAT CHANGED (early: the long-question guard and the multi-command prepass
    # took these) ----
    # "notice any updates to your code?", "check the diff / your status logs pre and post install", "what
    # version are you": answered from the record of versions seen here, not a made-up status report.
    if re.search(
            r"\b(?:notice|noticed|see|seen|aware\s+of|know\s+of|spot|spotted|any|been)\s+(?:any\s+|some\s+|new\s+|recent\s+)?"
            r"(?:updates?|changes?|upgrades?|new\s+code|modifications?|patches?)\s+(?:to|in|of|on)\s+"
            r"(?:your|the|eli'?s|ur)\s+(?:own\s+)?(?:code|codebase|version|self|runtime|install|installation|software|build|"
            r"files|source|release|vr|legal)\b"
            r"|\bcheck\s+(?:the\s+|your\s+)?(?:diff|status\s+logs?|install\s+logs?|changelog|change\s+log)\b"
            r"|\b(?:pre|post)[- ]?(?:install|update|upgrade)\b"
            r"|\bwhat\s+(?:changed|is\s+new|'?s\s+new)\s+in\s+(?:your|this|the\s+(?:new|latest))\s+(?:code|version|build|release|install)\b"
            r"|\b(?:which|what)\s+version\s+(?:are\s+you|is\s+this|of\s+(?:you|eli)\s+is\s+this|are\s+you\s+running|is\s+running)\b"
            r"|\bwere\s+you\s+(?:updated|upgraded)\b|\bhave\s+you\s+been\s+(?:updated|upgraded)\b", low):
        return _mk("VERSION_CHANGES", {}, 0.94, matched_by="self.version_changes",
                   allow_chat_without_evidence=False)

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
        r"\b(?:notice|sense|feel)\b.{0,60}\b(?:any\s+)?changes?\b.{0,60}\b(?:in|to)\s+your\s+code\b",
        low,
    ) and not re.search(r"\b(?:git|commit|changelog|diff|show me|list|which)\b", low):
        pass  # casual rapport ("notice any changes in your code?") → CHAT, not audit dump
    elif re.search(
        r"\b(?:notice|sense|feel)\b.{0,40}\b(?:any\s+)?changes?\b.{0,40}\b(?:recently|lately|as of late)\b",
        low,
    ) and not re.search(r"\b(?:git|commit|changelog|diff|show me|list|which|updates?\s+and\s+checks?)\b", low):
        pass  # casual check-in ("notice any changes recently?") → CHAT, not git dump
    elif re.search(
        r"\b(what|which|any|show|tell|list)\b.{0,80}\b(updates?|checks?|repairs?|changes?|maintenance|work)\b.{0,80}\b(as of late|recently|lately|performed|done|happened|made)\b",
        low,
    ) or (
        re.search(
            r"\b(what have you been doing|what have you been working on"
            r"|what have you checked|what have you updated)\b",
            low,
        )
        # A bare "what have you been doing?" in conversation is small talk, not a
        # request for a git/runtime evidence dump — see the shared disambiguator.
        and _eli_activity_question_is_about_dev_work(low)
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

    # Strict control actions never fall through to generic CHAT
    # (ELI_PATCH_PERSONAL_MEMORY_ROUTE_PRECEDENCE_20260511). Personal-memory questions beat the
    # generic memory-runtime route. System architecture ("your memory system... which files/db
    # tables") is EXPLAIN_MEMORY_RUNTIME; the old block matched architecture keywords alone.
    _pm_text = low  # `low` is always defined: low = raw.lower()
    if (
        not _eli_memory_runtime_route_lock_should_trigger(_pm_text)
        and _pm_asks_something(_pm_text)
        and not _pm_own_faculty(_pm_text)
        and ("memory" in _pm_text)
        and (
            "personal" in _pm_text
            or "personalised" in _pm_text
            or "personalized" in _pm_text
            or "about me" in _pm_text
            or "my memory" in _pm_text
            or (
                ("internals" in _pm_text or "internally" in _pm_text)
                and re.search(r"\b(my|me|about me|stored about me)\b", _pm_text)
            )
        )
    ):
        return {
            "action": "PERSONAL_MEMORY_DEEP_EXPLAIN",
            "confidence": 0.99,
            "source": "router.personal_memory_precedence",
            "reason": "personalised/internal memory explanation must not be captured by generic memory runtime route",
            "query": text if 'text' in locals() else (_pm_text or ""),
        }

    # Tolerate natural leading filler ("do a", "run a", "please", "now") and trailing punctuation so
    # "do a self update" routes to SELF_UPDATE and not CHAT (which then says a fake "I'm updating
    # myself now"). "self upgrade" intentionally doesn't match; it routes to SELF_UPGRADE below.
    if re.fullmatch(
        r"(?:(?:please|pls|can you|could you|now)\s+)*"
        r"(?:(?:do|run|perform|execute|go)\s+(?:a|an|the)?\s*)?"
        r"(?:self[- ]?update|update yourself|refresh yourself|refresh all overlays)"
        r"(?:\s+now)?[.!\s]*",
        low):
        return _mk("SELF_UPDATE", {}, 0.99, matched_by="router.self_update", allow_chat_without_evidence=False)


    # What has worked and what keeps failing, from ELI's own record of its runs (runtime/action_outcomes):
    # "what has worked lately", "which of your commands keep failing", "what are you unreliable at".
    if (_pm_asks_something(low)
            and re.search(r"\b(?:work(?:ed|s|ing)?|fail(?:ed|s|ing)?|failures?|unreliable|reliable|reliability|succeed(?:ed|s)?|"
                          r"broken|go(?:es|ing)?\s+wrong|went\s+wrong)\b", low)
            and re.search(r"\b(?:your|you|eli'?s?)\b.{0,40}\b(?:actions?|commands?|tools?|abilit(?:y|ies)|capabilit(?:y|ies)|"
                          r"skills?|functions?|features?)\b|\b(?:actions?|commands?|tools?|capabilit(?:y|ies))\b.{0,30}\b(?:of\s+yours|you\s+(?:have|run|ran|use|tried))\b", low)
            and not re.search(r"\b(?:last|previous|that|this)\s+(?:response|reply|answer|turn)\b|\bhow\s+do\s+(?:you|your)\b", low)):
        return _mk("EXPLAIN_FAILURE_LOG", {"scope": "outcomes", "question": raw}, 0.96,
                   matched_by="router.what_has_worked", need_grounding=True, allow_chat_without_evidence=False)

    if re.search(r"\b(confidence in (?:your|my) last response|which agents contributed|what agents contributed|last response trace|previous response trace|last turn trace)\b", low):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.99, matched_by="router.explain_last_response", allow_chat_without_evidence=False)

    # How long / why slow the last response was, answered from the real recorded timing (model calls
    # and seconds in the turn record), not the model's guess. Live, "took you a minute and a half for
    # that response?" went to chat and the model invented "one and a half seconds" for an 83 s reply.
    if re.search(r"how\s+long\s+(?:did|does|will)\s+(?:that|this|it|your(?:\s+last)?\s+(?:response|reply|answer))\s+take|(?:took|take|taking)\s+(?:you\s+)?(?:about\s+|roughly\s+|like\s+)?(?:\d|a|an|half|couple|few|several|ages|forever|too\s+long|so\s+long)\b[^?.!\n]{0,45}\bfor\s+(?:that|this|your|the)\s+(?:last\s+)?(?:response|reply|answer|one)\b|(?:that|this|your)\s+(?:last\s+)?(?:response|reply|answer)\s+(?:took|was)\s+[^?.!\n]{0,20}\b(?:slow|forever|ages|too\s+long|so\s+long)\b", low):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.96, matched_by="router.explain_last_response.timing",
                   need_grounding=True, allow_chat_without_evidence=False)

    # Where did ELI get its last statement, in plain words. The jargon route above missed "where are
    # you getting this information?", so the model invented a source. The bus knows which agents
    # contributed, so answer from that. Needs that/this/it to stay about the prior statement.
    if re.search(
        r"\bwhere\s+(?:are|did)\s+you\s+get(?:ting)?\s+(?:that|this|it|the\s+\w+)\b"
        r"|\bwhere\s+(?:is|did)\s+(?:that|this|it)\s+(?:com(?:e|ing)\s+)?from\b"
        r"|\bhow\s+do\s+you\s+know\s+(?:that|this|any\s+of\s+(?:that|this))\b"
        r"|\bwhat\s+are\s+you\s+basing\s+(?:that|this|it)\s+on\b"
        r"|\bwhat(?:'s|\s+is)\s+(?:that|this)\s+based\s+on\b"
        r"|\bsays\s+who\b",
        low,
    ):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.95,
                   matched_by="router.explain_last_response.provenance",
                   need_grounding=True, allow_chat_without_evidence=False)

    # A request for raw metrics/agent logs/telemetry of a recent cycle must surface the real logged
    # telemetry (agent_dispatches + runtime_events via EXPLAIN_LAST_RESPONSE), not the settings
    # snapshot SELF_REPORT returns, which led ELI to claim "I have no logs".
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
    try:
        from eli.cognition.correction_patterns import (
            is_correction_query as _is_corr_q,
            is_model_identity_dispute as _is_model_dispute,
            is_runtime_recheck_correction as _is_runtime_recheck,
            is_codebase_health_query as _is_codebase_health,
        )
    except Exception:
        _is_corr_q = lambda t: False  # noqa: E731
        _is_model_dispute = lambda t: False  # noqa: E731
        _is_runtime_recheck = lambda t: False  # noqa: E731
        _is_codebase_health = lambda t: False  # noqa: E731
    if _is_model_dispute(low):
        # a dispute of which model ELI runs is said as a remark ("no, you are Mistral!"): its own
        # vocabulary is the test (meta "spoken_as_remark", route_contracts.fits)
        return _mk("RUNTIME_STATUS", {}, 0.98, matched_by="router.model_identity_dispute",
                   allow_chat_without_evidence=False, meta_extra={"spoken_as_remark": True})
    if _is_runtime_recheck(low):
        return _mk("RUNTIME_STATUS", {"question": raw}, 0.96,
                   matched_by="router.runtime_recheck_correction",
                   allow_chat_without_evidence=False)
    if _is_codebase_health(low):
        return _mk(
            "RUNTIME_AUDIT",
            {"query": raw, "audit_depth": "codebase"},
            0.98,
            matched_by="router.codebase_health",
            allow_chat_without_evidence=False,
            need_grounding=True,
            task_family="grounded_audit",
        )
    if _is_corr_q(low) and not _is_wallclock_question(low):
        return _mk("CHAT", {"message": raw}, 0.92, matched_by="router.correction_chat", allow_chat_without_evidence=True)

    if re.search(r"\b(who are you|what are you|what is your name|what's your name)\b", low) and re.search(
        r"\b(running on|model|context size|gpu layers|threads|batch|provider|runtime)\b",
        low,
    ):
        return _mk("SELF_REPORT", {}, 0.99, matched_by="router.self_report_with_runtime", allow_chat_without_evidence=False)

    # The runtime sense of each word (routing/_helpers.runtime_status_asked): bare "threads", "batch" and
    # "provider" sent "who's the best internet provider in my area" here.
    _runtime = runtime_status_asked(raw) or re.search(r"\bruntime\s+status\b", low)
    if _runtime and "model" in _runtime.group(0):
        return _mk("RUNTIME_STATUS", {"question": raw}, 0.99, matched_by="router.runtime_status_model_query",
                   allow_chat_without_evidence=False)
    if _runtime:
        return _mk("RUNTIME_STATUS", {}, 0.99, matched_by="router.runtime_status", allow_chat_without_evidence=False)

    if re.search(r"\bwhat(?:'s|\s+is)\s+(?:your\s+)?model\b", low):
        return _mk("RUNTIME_STATUS", {"question": raw}, 0.99,
                   matched_by="router.runtime_status_model_query", allow_chat_without_evidence=False)

    if re.search(r"\bcheck(?:\s+the|\s+your)?\s+model\b|\blook at (?:the|your) model\b", low):
        return _mk("RUNTIME_STATUS", {"question": raw}, 0.98,
                   matched_by="router.runtime_status_model_check", allow_chat_without_evidence=False)

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

    # "how do your agents work", "explain your agent bus": answered from the grounded cognition
    # report (EXPLAIN_COGNITION_RUNTIME). Must beat web_search and system.capabilities. "write a
    # report about your agent bus" is still GENERATE_DOCUMENT, so leave that alone.
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

    # Static profile vs learned understanding is route_contracts.wants_user_model_provenance, taken
    # by the precedence stage before this one (a second copy here routed "explain dynamic memory
    # allocation in c" to the user's profile).

    # Pure persona questions (no runtime keywords) → CHAT so ELI answers from
    # its own character and memory, not a raw JSON spec dump.
    if re.search(r"\b(who are you|what are you(?!\s+\w)|what is your name|what's your name|tell me about yourself|what is your purpose)\b", low) and not re.search(r"\b(model|running on|provider|context|gpu|llm|specs?|technical|runtime|layers|threads|batch)\b", low):
        return _mk("CHAT", {"message": raw}, 0.99, matched_by="router.identity_persona_chat", allow_chat_without_evidence=True)
    # Technical model/runtime queries → RUNTIME_STATUS (live mounted model evidence).
    # ELI's own model, asked of ELI: "which model has the biggest context window" is about models.
    from eli.execution.route_contracts import asks_about_eli
    if re.search(r"\b(what model(?: are you| do you use| is this)|which model(?: are you| do you use| is this)|what are you running on|what are you actually)\b", low) or (
            re.search(r"\b(what model|which model|what llm|which llm)\b", low) and asks_about_eli(raw)):
        return _mk("RUNTIME_STATUS", {"question": raw}, 0.99, matched_by="router.runtime_status_model_query", allow_chat_without_evidence=False)
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
