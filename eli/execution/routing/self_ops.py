"""Router self-improvement route guard — high-priority routing for "improve/fix
yourself" style requests. Moved from router_enhanced.py."""
from __future__ import annotations

import re

from eli.utils.log import get_logger
from eli.execution.routing._helpers import _mk  # noqa: F401

_SWLOG = get_logger(__name__)


def _eli_self_improvement_phrase_guard(text):
    raw = str(text or "")
    low = raw.lower().strip()

    # Self/system routes that the generic portable open_app / shell / memory
    # matchers would otherwise steal (claims-suite findings: "run a self test" →
    # OPEN_APP, etc.). Tight patterns, matched here BEFORE portable_route.
    if re.search(r"\b(generate|write|create)\s+(behaviou?ral\s+|unit\s+)?tests?\b|"
                 r"\btest\s+generation\b|\bgrow\s+(your\s+)?(test\s+)?coverage\b|"
                 r"\bwrite\s+tests?\s+for\s+(your|the)\b", low) and "report" not in low:
        return _mk("GENERATE_TESTS", {}, 0.95, matched_by="tests.generate.guard")
    if re.search(r"\btest\s+review\b|\breview\s+(the\s+)?test(s| results| suite)\b|"
                 r"\brun\s+(the\s+)?(full\s+)?(tests?|test\s+suite|project)\b.*\b(review|summari[sz]e|"
                 r"what('?s| is)?\s+(wrong|to\s+fix)|options|tell\s+me)\b|"
                 r"\b(run|do)\s+(a\s+)?(full\s+)?(test|project)\s+(run\s+and\s+)?(review|summary)\b", low):
        return _mk("TEST_REVIEW", {}, 0.96, matched_by="tests.review.guard")
    if re.search(r"\b(run\s+(the\s+|your\s+)?(test\s+suite|tests|pytest|claims\s+suite)|"
                 r"test\s+report|generate\s+(a\s+)?test\s+report|how('?s| is)\s+the\s+test\s+suite)\b", low):
        return _mk("RUN_TESTS", {}, 0.96, matched_by="tests.run.guard")
    if re.search(r"\bself[\s_-]?test\b|\brun\s+(a\s+)?self[\s_-]?test\b", low):
        return _mk("SELF_TEST", {}, 0.96, matched_by="self.test.guard")
    if re.search(r"\borchestrat(ion|or)\s+status\b|\b(show|explain)\s+(me\s+)?(your\s+)?(the\s+)?"
                 r"(agent\s+)?(dag|orchestration)\b|\bagent\s+dag\b|\bhow\s+are\s+your\s+agents\s+wired\b|"
                 r"\b(execution|agent)\s+layers\b", low):
        return _mk("ORCHESTRATION_STATUS", {}, 0.95, matched_by="orchestration.status.guard")
    # CODEBASE_GRAPH — ELI's own-architecture questions, answered from the live import
    # graph of its source (codebase_graph.py). Triggers on an explicit graph/map request
    # OR a component-name + connection/dependency verb. Kept below ORCHESTRATION_STATUS so
    # agent-DAG questions win there; bare "architecture" defers to the cognition self-report.
    _comp = (r"(router|executor|engine|stages?|pipeline|agent\s*bus|orchestrat(?:or|ion)|"
             r"gguf|inference|memory|vector\s*store|plugins?|perception|planning|world|"
             r"netguard|components?|modules?|subsystems?|internals?)")
    if (re.search(r"\b(codebase|code\s*base|component|module|dependency)\s+(graph|map|diagram)\b", low)
            or re.search(r"\b(show|explain|draw|describe|map)\s+(me\s+)?(your\s+)?"
                         r"(codebase|code\s*base|how\s+your\s+code\s+(is\s+)?(built|organi[sz]ed|connects|fits))\b", low)
            or re.search(r"\bhow\s+(are|do)\s+your\s+" + _comp + r"\s+(connect|wired|linked|relate)", low)
            or re.search(r"\bhow\s+does\s+(the\s+|your\s+)?" + _comp +
                         r"\b.{0,40}\b(connect|talk|link|relate|reach|wire|depend|feed|work\s+with)\b", low)
            or re.search(r"\bwhat\s+does\s+(the\s+|your\s+)?" + _comp + r"\b.{0,20}\bdepend\s+on\b", low)):
        return _mk("CODEBASE_GRAPH", {"question": low}, 0.9, matched_by="codebase.graph.guard")
    # AUTOPILOT_DEBUG — a pasted traceback / pytest failure, or an explicit "debug/diagnose
    # this error" request. Passes the RAW text (case-preserved) so file paths in the
    # traceback parse correctly. A bare Python traceback signature is enough on its own.
    if ("traceback (most recent call last)" in low
            or re.search(r"^failed\s+\S+\.py|^\s*failed\s+tests?/", low, re.M)
            or (re.search(r"\b(debug|diagnose|root[\s-]?cause|troubleshoot|"
                          r"what('?s| is)\s+(causing|wrong\s+with)|why\s+(is|does|did)\b[^.?!]{0,40}\b"
                          r"(fail|crash|break|error|except))\b", low)
                and re.search(r"\b(error|traceback|exception|failure|failing|crash(ed)?|"
                              r"stack\s*trace|pytest|assert(ion)?|bug)\b", low))):
        _dbg_args = {"error_text": raw}
        # Explicit closed-loop intent: "fix it and verify" / "repair this" → governed
        # apply loop; "reproduce/confirm this" → reproduce-only. Bare debug stays diagnosis.
        if re.search(r"\b(fix|repair|patch|resolve)\b.*\b(it|this|the\s+(bug|error|fail))\b|"
                     r"\b(fix|repair)\s+and\s+(verify|test|check)\b|\bmake\s+it\s+pass\b", low):
            _dbg_args["apply"] = True
        elif re.search(r"\b(reproduce|verify|confirm)\b", low):
            _dbg_args["verify"] = True
        return _mk("AUTOPILOT_DEBUG", _dbg_args, 0.93, matched_by="autopilot.debug.guard")
    if re.search(r"\blora\s+status\b|\b(lora|training)\s+(status|readiness|ready)\b|"
                 r"\bis\s+lora\s+ready\b|\bcheck\s+(the\s+)?lora\b|"
                 r"\bcan\s+you\s+(fine[- ]?tune|train)\b", low):
        return _mk("LORA_STATUS", {}, 0.95, matched_by="lora.status.guard")
    if re.search(r"\b(train|run|start)\s+(a\s+|the\s+)?lora\b|\blora\s+train(ing)?\b|"
                 r"\bfine[- ]?tune\b|\btrain\s+(an?\s+)?adapter\b", low):
        return _mk("LORA_TRAIN", {}, 0.95, matched_by="lora.train.guard")
    if re.search(r"\banaly[sz]e\s+(your(self)?|its?\s+own)\s+"
                 r"(failures?|errors?|mistakes|performance|metrics)\b", low):
        return _mk("SELF_ANALYZE", {}, 0.96, matched_by="self.analyze.guard")
    if re.search(r"\bproactive\s+status\b|\bstatus\s+of\s+(the\s+)?proactive\b", low):
        return _mk("PROACTIVE_STATUS", {}, 0.96, matched_by="proactive.status.guard")
    # ELI's self-generated agenda: "do you have any proposals/suggestions/ideas for me",
    # "what are you proposing", "what's on your agenda". Surfaces proposal_only goals +
    # capability proposals (GET_PROPOSALS) — was mis-routing to unrelated status dumps.
    if re.search(
        r"\b(?:do you have|got|any|you have)\b[^?]*\b(?:proposals?|suggestions?|ideas?|"
        r"recommendations?)\b[^?]*\b(?:for me|to make)\b"
        r"|\bwhat\s+(?:are\s+you|do\s+you)\s+propos"
        r"|\b(?:any|some)\s+(?:proposals?|suggestions?|recommendations?)\s*\??$"
        r"|\bwhat'?s?\s+on\s+your\s+agenda\b",
        low,
    ):
        return _mk("GET_PROPOSALS", {}, 0.95, matched_by="proactive.get_proposals")
    if re.search(r"\b(start|launch|enable|turn\s+on)\s+(the\s+)?proactive(\s+(mode|daemon))?\b", low):
        return _mk("PROACTIVE_START", {}, 0.96, matched_by="proactive.start.guard")
    if re.search(r"\b(stop|kill|disable|turn\s+off|shut\s+down)\s+(the\s+)?proactive(\s+(mode|daemon))?\b", low):
        return _mk("PROACTIVE_STOP", {}, 0.96, matched_by="proactive.stop.guard")
    if re.search(r"\b(execute|run)\s+(the\s+)?goal\b", low):
        _g = re.sub(r"\b(execute|run)\s+(the\s+)?goal\b", "", low).strip()
        return _mk("EXECUTE_GOAL", {"goal": _g or raw}, 0.95, matched_by="goal.execute.guard")

    if re.search(r"\b(self[- ]?improvement|improvement)\s+log\b", low) or (
        re.search(r"\bexact\s+error\s+message\b", low)
        and re.search(r"\blast\s+failure\b", low)
    ):
        return {
            "action": "SELF_IMPROVEMENT_LOG",
            "args": {"question": raw, "limit": 5},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_improvement_log_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    if re.search(
        r"\b(run|start|perform|execute)\s+(?:an?\s+)?(?:self[- ]?)?improvement(?:\s+cycle)?\b"
        r"|\bimprove\s+yourself\b"
        r"|\bself[- ]?improve\b",
        low,
    ):
        return {
            # Pass the raw utterance through: the effector sniffs it to choose
            # analyze (fast, read-mostly) vs propose (coding agent, backgrounded).
            # With args={} that sniff saw an empty string, so "propose"/"fix the
            # failing X" was unreachable from chat and every request ran analyze.
            "action": "SELF_IMPROVE",
            "args": {"_raw_user_text": raw},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_improvement_cycle_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    if re.search(r"\bwhat\s+have\s+you\s+improved\b|\bself[- ]?improvement\s+status\b", low):
        return {
            "action": "SELF_ANALYZE",
            "args": {},
            "confidence": 0.98,
            "meta": {
                "matched_by": "eli.self_improvement_status_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    # SELF_UPGRADE must be claimed here, before the portable open-app contract,
    # or "run upgrade" / "run the upgrade" gets misrouted to OPEN_APP (opening a
    # phantom app named "upgrade"). "run bash upgrade.sh" is NOT caught — the
    # regex requires 'upgrade' to follow run/the/a/self directly, so an
    # interposed binary keeps it on the shell path.
    if re.search(
        r"\b(?:run|start|perform|execute|do|go)\s+(?:an?\s+|the\s+)?(?:self[- ]?)?upgrade\b"
        r"|\bself[- ]?upgrade\b"
        r"|\bupgrade\s+(?:yourself|eli|the\s+system)\b"
        r"|\bupgrade\s+your\s+(?:own\s+)?(?:code|self)\b",
        low,
    ):
        return {
            "action": "SELF_UPGRADE",
            "args": {"request": raw},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_upgrade_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    # EXAMINE_CODE — "examine/review/scan <file|module|the codebase> for errors/bugs".
    # Must be claimed here (before portable_route) so it isn't read as OPEN_APP.
    # Code-specific: requires a code/file/module context word, a .py path, or an
    # eli.* dotted module — so "examine this image" / "review my document" do NOT
    # match. The fix offer + confirmation is handled by the pending_code_fix flow.
    # A GUI/runtime AUDIT-with-proof request ("scan the gui runtime wiring and
    # prove every hook with file-read evidence") is a grounded runtime audit
    # (GUI_RUNTIME_AUDIT) — part of ELI's self-inspection surface, NOT a code-
    # error examination. Defer it to the core router's gui_audit_proof contract
    # so EXAMINE_CODE doesn't steal it (regression: it ran in an earlier stage).
    _runtime_audit_proof = bool(
        re.search(r"\b(prove|proof|evidence|timestamps?|wiring|hooks?)\b", low)
        and re.search(r"\b(gui|runtime|wiring|pipeline|hooks?)\b", low)
    )
    if (
        not _runtime_audit_proof
        and (
        re.search(
            r"\b(examine|review|inspect|scan|audit)\b[^.?!]*"
            r"\b(code|codebase|repo|repository|file|files|module|modules|script|scripts|"
            r"function|functions|class|classes|for\s+errors|for\s+bugs|for\s+issues|"
            r"for\s+mistakes)\b",
            low,
        )
        or re.search(r"\b(examine|review|inspect|scan|audit|check)\b[^.?!]*\.py\b", low)
        or re.search(r"\b(examine|review|inspect|scan|audit|check)\b[^.?!]*\beli(?:\.\w+)+\b", low)
        # "check/look at <files|the code> for errors/issues" and the bare follow-up
        # "is there any issues with the files/code?" — these must run the real examiner,
        # NOT FILE_AUDIT (a directory file-counter whose output, when synthesised, gets
        # confabulated into invented files-with-bugs that don't exist). Requires an
        # error/issue word AND a code/file context word so casual "issues" don't match.
        or (
            # NOT a fix request — "fix the bugs in foo.py" is FIX_FILE's job; this is the
            # diagnose-only "is there any issues with the files?" form.
            not re.search(r"\b(fix|repair|correct|patch|solve|resolve|debug|rewrite)\b", low)
            and re.search(r"\b(issues?|errors?|bugs?|problems?|mistakes?|wrong|broken|faults?)\b", low)
            and re.search(r"\b(file|files|code|codebase|module|modules|script|scripts|"
                          r"function|functions|class|classes|\.py)\b", low)
        )
        or re.search(r"\b(check|look\s+(?:at|over|through)|go\s+through)\b[^.?!]*"
                     r"\b(file|files|code|codebase|module|modules|script|scripts)\b[^.?!]*"
                     r"\b(error|errors|bug|bugs|issue|issues|problem|problems|wrong|broken)\b", low)
        )
    ):
        return {
            "action": "EXAMINE_CODE",
            "args": {"request": raw},
            "confidence": 0.96,
            "meta": {
                "matched_by": "eli.examine_code_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    return None

# --- end ELI high-priority self-improvement route guard ---
