"""Router intent contracts — the leaf phase contracts (codebase-audit, pdf-enrich, precedence, frontier/identity/runtime-status, memory-question, persona-override, follow-up passthrough, tiny-fragment, media-query cleaner, identity scope). The phase38 *dispatch wrappers* stay in the router: they are the dispatch chain itself."""
from __future__ import annotations

import re as _eli_mqc_re

from eli.utils.log import get_logger
from eli.execution.routing._helpers import _extract_pdf_paths, _mk  # noqa: F401
from eli.execution.routing.profile import _eli_identity_scope_for_text  # noqa: F401

log = get_logger(__name__)
_SWLOG = log


_PM_PERSON_ATTR = (
    r"(?:interest|interests|habit|habits|personality|character|background|"
    r"academ\w+|research|work|studies|field|life|preference|preferences|"
    r"trait|traits|history|project|projects)"
)


def _eli_mqc_clean_query(q: str) -> str:
    """
    Normalize whitespace and strip leading verb tokens, but preserve the
    original casing of title/artist names. Apply known STT alias fixes
    case-insensitively without lowercasing the rest of the query.
    """
    q = _eli_mqc_re.sub(r"\s+", " ", str(q or "")).strip(" .,!?:;")
    q = _eli_mqc_re.sub(r"^(open|play|search|find)\s+", "", q, flags=_eli_mqc_re.I).strip()
    # Strip trailing service qualifiers — the router already extracted `target`.
    # Without this, "logic by diabolic on spotify" → query="logic by diabolic on spotify"
    # instead of the expected "logic by diabolic".
    q = _eli_mqc_re.sub(
        r"\s+on\s+(spotify|youtube|yt|youtube\.com|mpv|soundcloud|apple\s+music|tidal|deezer)\s*$",
        "",
        q,
        flags=_eli_mqc_re.I,
    ).strip()
    q = _eli_mqc_re.sub(
        r"\b(?:alt\s*[- ]?\s*0|hours\s+of\s+zero|i'll\s+just\s+air|"
        r"and\s+i'll\s+just\s+air|algezera|algezira|algazear|algazare|"
        r"algiers\s+era)\b",
        "al jazeera",
        q,
        flags=_eli_mqc_re.I,
    )
    return _eli_mqc_re.sub(r"\s+", " ", q).strip()

def _eli_phase10_is_codebase_audit_request(text: str) -> bool:
    """
    Prevent broad memory-runtime regexes from hijacking codebase audits.
    """
    s = str(text or "").lower()

    audit_words = (
        "audit", "inspect", "scan", "check", "verify", "examine",
        "what is wrong", "what's wrong", "broken", "missing",
    )
    code_words = (
        "router", "executor", "engine", "agent_bus", "world_model",
        "gguf_inference", "orchestrator", "output_governor",
        "output_governer", "response_governance", "response_governence",
        "hyde", "vector_store", "working_memory", "introspection_agent",
        "reranker", "llm_intent", "hardware_profile", "runtime_settings",
        "pipeline", "self_upgrade", "habits_memory_db", "knowledge_graph",
        "memory_adapter", "memory_truth", "memory_service", "sqlite_memory",
        "os_controller", "screen_locator", "log_rotation", "/runtime",
        "python files", ".py", "eli_pro_audio_gui", "router_enhanced",
        "executor_enhanced",
    )

    return any(a in s for a in audit_words) and any(c in s for c in code_words)


def _eli_phase11_enrich_pdf_route(raw, result):
    try:
        if not isinstance(result, dict):
            return result

        action = str(result.get("action") or "").upper().strip()
        if action != "ANALYZE_PDF":
            return result

        args = result.setdefault("args", {})
        if not isinstance(args, dict):
            return result

        text = str(raw or "")
        if ".pdf" not in text.lower():
            return result

        paths = []
        try:
            paths = list(_extract_pdf_paths(text))
        except Exception:
            paths = []

        if not paths:
            return result

        args["path"] = str(args.get("path") or paths[0])
        args["paths"] = paths

        meta = result.setdefault("meta", {})
        if isinstance(meta, dict):
            entities = meta.setdefault("entities", {})
            if isinstance(entities, dict):
                entities["path"] = args["path"]
                entities["paths"] = paths
            meta["multipdf_count"] = len(paths)
            # ELI_PHASE33_MULTIPDF_IDEMPOTENT
            _phase11_matched_by = str(meta.get("matched_by") or "analyze.pdf")
            if "+phase11_multipdf" not in _phase11_matched_by:
                meta["matched_by"] = _phase11_matched_by + "+phase11_multipdf"
            else:
                meta["matched_by"] = _phase11_matched_by

        return result
    except Exception:
        return result


def _eli_phase38_route_precedence_contract(raw):
    try:
        from eli.execution.route_contracts import classify_precedence_route
        return classify_precedence_route(raw)
    except Exception:
        return None


def _eli_phase38_frontier_status_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()
    if not low:
        return None

    if _re.search(
        r"\b("
        r"frontier status"
        r"|full system (?:status|audit|wiring|matrix)"
        r"|cross[- ]system (?:status|audit|wiring|matrix)"
        r"|full (?:project|eli) (?:audit|wiring|matrix)"
        r"|memory.*self[- ]aware.*proactive.*image.*world.*labs"
        r"|chat flow.*memory.*self.*proactive.*image.*world.*labs"
        r")\b",
        low,
    ):
        return {
            "action": "FRONTIER_STATUS",
            "args": {"question": str(raw or "")},
            "confidence": 0.997,
            "meta": {
                "matched_by": "eli.frontier_status_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
                "response_contract": "quick_direct_nonquick_persona_synthesis",
            },
        }
    return None


def _eli_phase39_identity_audit_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()
    if not low:
        return None

    if _re.search(
        r"\b("
        r"eli identity audit"
        r"|classif(?:y|ication) (?:eli|yourself|you)"
        r"|what (?:exactly )?is eli"
        r"|what should eli be classified as"
        r"|what are you classified as"
        r"|verified (?:eli )?(?:identity|classification) audit"
        r"|full (?:verified )?(?:eli )?(?:identity|classification) audit"
        r")\b",
        low,
    ):
        return {
            "action": "ELI_IDENTITY_AUDIT",
            "args": {"question": str(raw or "")},
            "confidence": 0.997,
            "meta": {
                "matched_by": "eli.identity_audit_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_identity_audit",
                "response_contract": "verified_local_classification_matrix",
            },
        }
    return None


def _eli_phase38_runtime_status_or_name_source_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()

    runtime_status_query = (
        ("who are you" in low or "what are you" in low)
        and (
            "actually running" in low
            or "running on right now" in low
            or "model" in low
            or "context size" in low
            or "gpu layers" in low
            or "everything" in low
        )
    )

    if runtime_status_query:
        return {
            "action": "RUNTIME_STATUS",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_runtime_status_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_status",
                "response_contract": "quick_direct_nonquick_persona_synthesis",
            },
        }

    if (
        ("how do you know" in low and "name" in low)
        or ("where" in low and "name" in low and ("file" in low or "located" in low or "stored" in low))
        or ("which file" in low and "name" in low)
    ):
        return {
            "action": "NAME_SOURCE_AUDIT",
            "args": {"question": str(raw or "")},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.final_name_source_audit_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    return None


def _eli_phase38_identity_name_source_single_safe_contract(raw):
    low = str(raw or "").lower().strip()

    if (
        ("how do you know" in low and "name" in low)
        or ("where" in low and "name" in low and ("file" in low or "located" in low or "stored" in low))
        or ("which file" in low and "name" in low)
    ):
        return {
            "action": "NAME_SOURCE_AUDIT",
            "args": {"question": str(raw or "")},
            "confidence": 0.99,
            "meta": {
                "matched_by": "identity.name_source_audit.single_safe",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    return None


def _eli_phase38_final_memory_question_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()

    _mem_kw = ("memory" in low or "memori" in low)
    asks_memory_internals = (
        "memory system" in low
        or "memory internals" in low
        or "memory pipeline" in low
        or "memory architecture" in low
        or ("which files" in low and ("db tables" in low or "database" in low or "tables" in low))
        or ("which db tables" in low)
        or ("which functions" in low and _mem_kw)
        # "how/explain/describe/walk me through … your memory … work/store/recall/pipeline …"
        or (
            _mem_kw
            and _re.search(r"\b(how|explain|describe|walk me through|break ?down|outline|detail)\b", low)
            and _re.search(
                r"\b(work|works|working|function|functions|operate|store|storage|stored|"
                r"recall|retriev|pipeline|architecture|internal|internally|flow|mechanism|"
                r"start to finish|end to end)\b", low)
        )
        # Mechanism / recall+storage phrasing — common as a follow-up that omits
        # the word "memory" entirely ("the full info, all paths, databases,
        # mechanism of recall and storage, everything").
        or _re.search(r"\bmechanism(s)?\s+of\s+(recall|storage|retrieval|memory)", low)
        or _re.search(r"\b(recall and storage|storage and recall|store and recall|recall and store)\b", low)
        or ("all paths" in low and ("database" in low or "db" in low or "sqlite" in low))
        or "all the databases" in low
        or "all databases" in low
        # Explicit mechanism names → unmistakably about the retrieval/storage stack.
        or _re.search(r"\b(faiss|fts5?|knowledge graph|kg|hyde|rag|vector store|embedder|"
                      r"vector index|dag pipeline)\b", low)
    )

    asks_personal_memory = (
        "what do you know about me" in low
        or "what you know about me" in low
        or "what have you stored about me" in low
        or "what you actually remember" in low
        or "remember about me" in low
        or ("most recent things" in low and "stored" in low and "me" in low)
        or "patterns have you detected" in low
        or "how i interact with you" in low
        # "what do you know about my interests/habits/personality/academia…",
        # "do you know anything about my background", "tell me about my research".
        # These are personal-knowledge questions and must reach the clean
        # PERSONAL_MEMORY_SUMMARY (which excludes news cache / dumps). Without
        # this they fell to MEMORY_RECALL, which surfaced NEWS digests and then
        # synthesised a 39k-char prompt into a lone "-" (spoken aloud).
        or bool(_re.search(
            r"\b(?:what (?:do|can) you know|what do you (?:remember|recall)|"
            r"do you (?:know|remember) (?:anything|much|something)|"
            r"what have you (?:learned|gathered|got))\b"
            r".{0,40}\babout (?:me\b|my " + _PM_PERSON_ATTR + r")",
            low,
        ))
        or bool(_re.search(
            r"\btell me (?:more )?(?:about|what you know about)\s+(?:me\b|my "
            + _PM_PERSON_ATTR + r")",
            low,
        ))
    )

    if asks_memory_internals and asks_personal_memory:
        return {
            "action": "PERSONAL_MEMORY_DEEP_EXPLAIN",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_memory_hybrid_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "personal_memory",
                "forbid_schema_dump": True,
                "forbid_reflection_spam": True,
                "forbid_news_rows": True,
                "response_contract": "quick_direct_nonquick_persona_synthesis",
            },
        }

    if asks_memory_internals:
        return {
            "action": "EXPLAIN_MEMORY_RUNTIME",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_memory_internals_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    if asks_personal_memory:
        return {
            "action": "PERSONAL_MEMORY_SUMMARY",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_personal_memory_summary_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "personal_memory",
                "forbid_schema_dump": True,
                "forbid_reflection_spam": True,
                "forbid_news_rows": True,
            },
        }

    return None


def _eli_phase38_persona_override_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")

    if low in {
        "elaborate more",
        "elaborate",
        "continue",
        "go on",
        "more detail",
        "explain more",
        "tell me more",
    }:
        return _mk(
            "CHAT",
            {
                "message": (
                    "Continue the immediately previous answer. "
                    "No role prefix. No HR filler. Stay in ELI's direct voice."
                )
            },
            0.95,
            matched_by="eli.final_followup_override",
            allow_chat_without_evidence=False,
        )

    if low in {
        "what's the story here we go",
        "whats the story here we go",
        "what's the story",
        "whats the story",
    }:
        return _mk(
            "CHAT",
            {
                "message": (
                    "Brief operational status in ELI's direct, dry voice. "
                    "One or two sentences max. Say what's running, call out anything broken or "
                    "degraded if relevant, say what's next if there is something. "
                    "No bullet points. No corporate framing. No \"I am happy to report\" filler. "
                    "Just the facts, ELI's way."
                )
            },
            0.95,
            matched_by="eli.final_story_status_override",
            allow_chat_without_evidence=False,
        )

    return None


def _eli_phase38_followup_passthrough_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")

    if low in {
        "elaborate more",
        "elaborate",
        "continue",
        "go on",
        "more",
        "more detail",
        "expand",
        "explain more",
        "tell me more",
        "go ahead",
        "go ahead please",
        "yeah go ahead",
        "please go ahead",
        "yes go ahead",
        "sure go ahead",
        "go",
        "proceed",
        "keep going",
    }:
        return _mk(
            "CHAT",
            {"message": str(raw or "")},
            0.90,
            matched_by="eli.followup.short_contextual",
            allow_chat_without_evidence=False,
        )

    return None


def _eli_phase38_identity_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")

    # Symbolic-world / room questions → CHAT, so the persona handoff's LIVE current
    # room + 9-room topology is used. These previously fell to the llm_intent fallback,
    # which guessed GAZE_STATUS — a path with no world context — so the model defaulted
    # to "Core Room" instead of reading the real room (anomaly_room/workshop/…) from the
    # world state. Route them here so ELI answers its actual room.
    if _re.search(
        r"\b(?:what|which)\s+room\s+(?:are\s+you|you'?re|is\s+eli)\b"
        r"|\bwhere\s+are\s+you\s+(?:right\s+now|currently|at|in\s+your\s+world)\b"
        r"|\b(?:are\s+you\s+(?:still\s+)?(?:stuck\s+)?in|been\s+in)\s+(?:the\s+)?\w+(?:\s+\w+)?\s+room\b"
        r"|\byour\s+(?:favou?rite|other\s+favou?rite|other)\s+room\b"
        r"|\bwhich\s+room\b",
        low,
    ):
        return _mk(
            "CHAT",
            {"message": raw},
            0.97,
            matched_by="world.room_query",
            allow_chat_without_evidence=True,
        )

    if _re.search(
        r"\b(who are you|what are you(?!\s+\w)|what is your name|what's your name|tell me about yourself)\b",
        low,
    ):
        # Persona/identity questions answered from ELI's character + memory.
        # SELF_REPORT (raw spec JSON) is wrong here — that's for runtime/model queries.
        if not _re.search(r"\b(model|running on|provider|context|gpu|llm|specs?|technical|runtime|layers|threads|batch)\b", low):
            return _mk(
                "CHAT",
                {"message": raw},
                0.99,
                matched_by="identity.persona_chat",
                allow_chat_without_evidence=True,
            )
        return _mk(
            "SELF_REPORT",
            {},
            0.99,
            matched_by="identity.final_self_report",
            allow_chat_without_evidence=False,
        )

    # "do you know who I am AND who you are" — a rhetorical/relational identity check that
    # wants a natural answer covering BOTH (who you are / I'm ELI), not a one-sided
    # user-identity summary dump.
    if (_re.search(r"\bwho\s+(?:i\s+am|am\s+i)\b", low)
            and _re.search(r"\bwho\s+(?:you\s+are|are\s+you)\b", low)):
        return _mk(
            "CHAT", {"message": raw}, 0.96,
            matched_by="identity.relational_both", allow_chat_without_evidence=True,
        )

    if _re.search(
        r"\b(who am i|do you know who i am|do you know me|do you remember me|"
        r"what is my name|what('s| is) my name|what do you know about me|"
        r"you do not know who i am|you don'?t know who i am|"
        r"don'?t you know who i am|don'?t you know me|"
        r"you don'?t know me|you have no idea who i am)\b",
        low,
    ):
        return _mk(
            "USER_IDENTITY_SUMMARY",
            {},
            0.99,
            matched_by="identity.final_user_summary",
            allow_chat_without_evidence=False,
        )

    # Elliptical name follow-up: "and my name?", "my name?", "what about my
    # name". A bare name-only question after the topic was already raised used
    # to fall through to CHAT, where the constitutional critique then nuked the
    # correct stored-name answer to "[no memories found]". Route it to the grounded
    # USER_IDENTITY_SUMMARY (returns the name verbatim). The pattern must END at
    # "my name" so the statement "my name is <name>" is NOT captured here.
    if _re.fullmatch(
        r"\s*(and|but|so|ok|okay|well|hmm?)?[,\s]*"
        r"(what(?:'s| is| about)?\s+)?my name\s*\??\s*",
        low,
    ):
        return _mk(
            "USER_IDENTITY_SUMMARY",
            {},
            0.99,
            matched_by="identity.final_user_summary_elliptical",
            allow_chat_without_evidence=False,
        )

    return None


def _eli_phase38_media_query_cleaner_post(result):
    try:
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "PLAY_MEDIA":
            args = result.setdefault("args", {})
            query = str(args.get("query") or "")
            cleaned = _eli_mqc_clean_query(query)
            if cleaned:
                args["query"] = cleaned
                result.setdefault("meta", {})["query_cleaned_by"] = "eli.final_media_query_cleaner"
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    return result


def _eli_phase38_tiny_fragment_post(raw, result):
    import re as _re

    try:
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "CHAT":
            low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")
            words = _re.findall(r"[a-z0-9']+", low)

            allowed_short = _re.search(
                r"\b("
                r"who are you|who am i|who made you|who built you|"
                r"what are you|what is this|what is it|"
                r"are you (?:sentient|alive|conscious|real|there|ok|okay)|"
                r"describe yourself|describe your|introduce yourself|tell me about yourself|"
                r"how many memories|"
                r"elaborate more|elaborate|continue|go on|more detail|expand|explain more|tell me more|"
                r"remember this|save this|help me|explain|why|how|what|where|when|who|"
                r"okay|ok|yes|no|nope|yep|yeah|sure|agreed|correct|exactly|right|fair|"
                r"nice|cool|great|perfect|good|got it|understood|alright|indeed|true|false|"
                r"hi|hello|hey|howya|hiya|yo|sup|afternoon|morning|evening|night|"
                r"for fucksake|fucksake|for fuck sake|jesus|jaysus|seriously|what the|"
                r"fuck off|piss off|cop on|come on|wise up"
                r")\b",
                low,
            )

            ends_in_terminator = bool(_re.search(r"[.?!]\s*$", str(raw or "").strip()))

            looks_fragmentary = (
                not ends_in_terminator
                and (
                    len(words) <= 1
                    or str(raw or "").strip().endswith("-")
                    or low in {"preview", "resil", "i u", "here i will", "find your mo"}
                )
            )

            if looks_fragmentary and _re.fullmatch(
                r"(?:date|the\s+date|is\s+the\s+date|what\s+date|what\s+is\s+the\s+date|what's\s+the\s+date|today|what\s+day|what\s+days|their\s+date|tell\s+me\s+their\s+days)",
                low,
            ):
                return _mk("DATE", {}, 0.999, matched_by="system.date.fragment")

            if looks_fragmentary and _re.fullmatch(
                r"(?:time|the\s+time|what\s+time|what\s+is\s+the\s+time|what's\s+the\s+time)",
                low,
            ):
                return _mk("TIME", {}, 0.999, matched_by="system.time.fragment")

            if looks_fragmentary and low in {
                "date",
                "the date",
                "is the date",
                "what date",
                "what days",
                "what's the day",
                "what's the days",
                "what day",
                "what day is it",
                "what day is this",
                "their date",
                "tell me their days",
            }:
                return _mk("DATE", {}, 0.999, matched_by="system.date.fragment")

            if looks_fragmentary and low in {
                "time",
                "the time",
                "what time",
                "what is the time",
                "what's the time",
            }:
                return _mk("TIME", {}, 0.999, matched_by="system.time.fragment")

            if looks_fragmentary and not allowed_short:
                # install/download commands are valid 2-word actions — bypass
                # the fragment guard and let the remediation handler process them.
                _idm = _re.match(r"^(install|download|get|setup)\s+(\S+)", low)
                if _idm:
                    return _mk(
                        "CONFIRM_PENDING_REMEDIATION",
                        {"message": str(raw or "").strip()},
                        0.97,
                        matched_by="remediation.install_download_fragment_bypass",
                    )

                # Pending-repair confirmation bypass — if a repair plan is waiting
                # and the input contains a clear yes/no word (even with STT noise),
                # route to remediation instead of dropping as a fragment.
                try:
                    from eli.runtime import grounded_remediation as _gr_frag
                    if _gr_frag.get_pending():
                        if _re.search(r'\b(yes|confirm|confirmed|proceed|go ahead|do it)\b', low, _re.I):
                            return _mk(
                                "CONFIRM_PENDING_REMEDIATION",
                                {"message": str(raw or "").strip()},
                                0.95,
                                matched_by="pending_remediation.yes_intercept",
                            )
                        if _re.search(r'\b(no|cancel|abort|stop|never mind)\b', low, _re.I):
                            return _mk(
                                "CONFIRM_PENDING_REMEDIATION",
                                {"message": str(raw or "").strip()},
                                0.95,
                                matched_by="pending_remediation.no_intercept",
                            )
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)

                grid_text = str(raw or "").strip().lower().replace("×", "x")
                grid_text = _re.sub(r"\btree\b", "3", grid_text)
                grid_text = _re.sub(r"\bthree\b", "3", grid_text)
                grid_text = _re.sub(r"\btwo\b", "2", grid_text)
                grid_text = _re.sub(r"\bfour\b", "4", grid_text)

                grid_m = _re.fullmatch(r"(\d{1,2})\s*(?:x|by)\s*(\d{1,2})", grid_text)
                if grid_m:
                    cols, rows = int(grid_m.group(1)), int(grid_m.group(2))
                    if 1 <= cols <= 8 and 1 <= rows <= 8:
                        return _mk(
                            "TILE_WINDOWS",
                            {"cols": cols, "rows": rows, "grid": [cols, rows]},
                            0.985,
                            matched_by="window.grid_followup",
                        )

                # The structured record belongs in the log and in meta, NOT in
                # `response`/`content` -- those are what the user reads and
                # what TTS speaks. Setting them to a JSON blob is why a
                # mistyped "ply" was answered with
                #   {"event": "input_fragment_guard", "heard": "ply", ...}
                # on screen. diagnostic_patterns.py and memory.py both already
                # carry filters for this exact string, which is a workaround
                # for a leak that should not happen at source.
                _heard = str(raw or "").strip()
                _diag = {
                    "event": "input_fragment_guard",
                    "heard": _heard,
                    "routed_to_cognition": False,
                    "reason": "fragmentary_input",
                }
                msg = (f"I only caught \u201c{_heard}\u201d \u2014 could you say that again?"
                       if _heard else "I didn't catch that \u2014 could you say it again?")

                return _mk(
                    "NOOP",
                    {"message": msg, "response": msg, "content": msg,
                     "diagnostic": _diag},
                    0.999,
                    matched_by="eli.tiny_chat_fragment_guard",
                )

    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)

    return result


def _eli_phase38_personal_memory_summary_compat_post(out):
    if isinstance(out, dict) and out.get("action") == "PERSONAL_MEMORY_SUMMARY":
        out = dict(out)
        meta = dict(out.get("meta") or {})
        meta["matched_by"] = "eli.personal_memory_summary_first_class"
        meta["forbid_schema_dump"] = True
        meta["forbid_reflection_spam"] = True
        meta["forbid_news_rows"] = True
        out["meta"] = meta
    return out


def _eli_phase38_identity_scope_post(raw, out):
    if isinstance(out, dict) and str(out.get("action") or "").upper() == "USER_IDENTITY_SUMMARY":
        out = dict(out)

        route_args = dict(out.get("args") or {})
        route_args["question"] = str(raw or "")
        route_args["identity_scope"] = _eli_identity_scope_for_text(raw)
        out["args"] = route_args

        meta = dict(out.get("meta") or {})
        meta["identity_scope_contract"] = route_args["identity_scope"]
        meta["forbid_profile_memory_dump"] = True
        meta["forbid_preferences"] = True
        out["meta"] = meta

    return out
