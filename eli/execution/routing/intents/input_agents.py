"""Pointer/keyboard input control and agent-attribution questions.

Clicks, drags and typing, plus 'which agents contributed' introspection.

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


def route_input_agents(text: str, raw: str, low: str, text_l: str):
    if re.search(r"\bdouble[\s-]?click\b", low):
        return _mk("GAZE_CLICK", {"button": "left", "double": True}, 0.95, matched_by="gaze.click.double")
    if re.search(r"\bleft[\s-]?click\b|^\s*click\s*$", low):
        return _mk("GAZE_CLICK", {"button": "left"}, 0.94, matched_by="gaze.click.left")
    _gaze_on = False
    try:
        from eli.perception.gaze_engine import is_gaze_running as _igr
        _gaze_on = bool(_igr())
    except Exception:
        _gaze_on = False
    if _gaze_on:
        if re.search(r"\bopen\s+(?:that|it|this|here|there)\b", low):
            return _mk("GAZE_CLICK", {"button": "left", "double": True}, 0.95, matched_by="gaze.click.double")
        if re.search(r"\bclick\s+(?:it|that|this|here|there)\b", low):
            return _mk("GAZE_CLICK", {"button": "left"}, 0.94, matched_by="gaze.click.left")
    # ── end gaze engine control ───────────────────────────────────────────────

    # File Audit must come before RUNTIME_AUDIT or the broad 'do ... audit' pattern steals it.
    # "list" is kept out of bare "files?" matching so "list the files in my downloads folder" reaches
    # LIST_DIR. Only audit|examine|inspect|scan|inventory pair with bare "files?"; "list" needs a
    # code-specific noun or qualifier.
    if re.search(
        r"\b(audit|examine|inspect|scan|inventory)\b.{0,40}\b(files?|codebase|source|modules?|scripts?|project\s+files?)\b"
        r"|\b(list|inventory)\b.{0,40}\b(codebase|source|modules?|scripts?|project\s+files?)\b"
        r"|\bfile\s+(audit|scan|inventory|check|examination)\b"
        r"|\baudis?\s+(?:all\s+(?:of\s+)?(?:your|the)\s+)?files?\b"
        r"|\b(do|perform|run)\s+(?:a\s+)?file\s+audit\b"
        r"|\blist\s+(?:all\s+)?(?:your\s+)?files?\b",
        low,
    ):
        return _mk(
            "FILE_AUDIT",
            {"scope": "eli", "message": raw},
            0.95,
            matched_by="router.file_audit",
            need_grounding=True,
            task_family="grounded_audit",
        )

    if re.search(
        r"\b(run a full runtime audit|run full runtime audit|runtime audit|system audit|health check|diagnostic|diagnostics|what'?s actually broken|what is actually broken)\b"
        r"|\b(perform|do|run|wanna run|want to run|let'?s run)\b.{0,30}\b(full.?time\s+audit|full\s+audit|runtime audit|system audit|audit)\b"
        r"|\b(fulltime|full.time)\s+audit\b"
        r"|\bdo an? audit\b"
        # "run a full runtime <X>" / "do a full system <X>": the "full runtime|system" phrase IS
        # the audit intent — the trailing word is often STT noise ("audit" mis-heard as "artist",
        # which otherwise got launched as an app via the greedy OPEN_APP contract).
        r"|\b(?:run|do|perform|start|execute)\s+(?:a\s+)?full\s+(?:runtime|system)\b"
        r"|\bcheck (the\s+)?(runtime|system|modules?|pipeline)\b",
        low,
    ):
        return _mk("RUNTIME_AUDIT", {}, 0.95, matched_by="audit.runtime.preempt",
                   need_grounding=True, task_family="grounded_audit")

    # See notes on cognition.trace.chat_handoff in _route_grounded_runtime_intent.
    if re.search(r"\b(confidence in your last response|which agents contributed|agents contributed|grounded trace|trace metadata)\b", low):
        return _mk("CHAT", {"message": raw}, 0.98,
                   matched_by="cognition.trace.chat_handoff",
                   allow_chat_without_evidence=False)

    if re.search(r"\b(memory system|memory runtime|how does your memory work internally|which files|which db tables|which functions)\b", low):
        return _mk("EXPLAIN_MEMORY_RUNTIME", {}, 0.99, matched_by="router.memory_status_to_grounded", allow_chat_without_evidence=False)

    # Reasoning-mode status (ELI_ROUTER_REASONING_MODE_STATUS_FIX_20260505): answer the active mode
    # label without hijacking into full runtime diagnostics. Only queries about the current mode
    # are caught; "all modes", "how many" or differences go to CHAT so it reads the source files.
    _asking_about_all_modes = re.search(
        r"\b(all|every|each|how many|list|explain|full|describe|detail|difference|differ|compare|what are|tell me about|tell me all|tell me everything|what do|how do|modes?\s+you\s+have|modes?\s+does|all.*mode|every.*mode)\b",
        low,
    )
    if not _asking_about_all_modes and (
        re.fullmatch(r"(?:what(?:'s| is)|which is|tell me|show me)?\s*(?:your|eli'?s|the|my)?\s*(?:current|active)?\s*reasoning mode(?:\s*,?\s*eli)?\??", low)
        or re.search(r"\b(?:what(?:'s| is)|which|current|active)\b.{0,40}\breasoning mode\b", low)
    ):
        return _mk("REASONING_MODE_STATUS", {}, 0.995, matched_by="reasoning.mode_status", allow_chat_without_evidence=False)

    # Broad "explain / describe / list all reasoning modes" goes to EXPLAIN_ALL_REASONING_MODES so
    # the answer is built from reasoning_modes.py. A frustrated correction that just mentions a mode
    # ("you are not in quick mode") is relational CHAT. Needs a modes topic ("reasoning mode(s)" /
    # plural "modes"), not a bare singular "<x> mode".
    _is_mode_correction = bool(re.search(
        r"\byou('?re| are| were)\s+not\b|\bnot\s+in\b|\bwhat\s+are\s+you\s+talking\s+about\b"
        r"|\bthat('?s| is| was)\s+not\b|\byou\s+said\b|\bnot\s+(?:quick|normal|advanced|research|expert)\b",
        low))
    if (_asking_about_all_modes and not _is_mode_correction
            and re.search(r"\b(reasoning modes?|modes)\b", low)):
        return _mk(
            "EXPLAIN_ALL_REASONING_MODES", {},
            0.99,
            matched_by="reasoning.all_modes_grounded",
            allow_chat_without_evidence=False,
            need_grounding=True,
            task_family="grounded_audit",
        )

    if re.search(r"\b(cognition pipeline|input to output|every step|no vague descriptions)\b", low):
        return _mk("CHAT", {"message": raw}, 0.95, matched_by="router.cognition_status_to_chat")

    # The report of what ELI keeps on the user ("show user info", "dump my profile") is
    # route_contracts.identity_question's USER_INFO_REPORT, read by the router's identity stage; it
    # was a MEMORY_RECALL here that the executor turned into the report before its registry.
    return None
