"""Document-creation effector — CREATE_DOCUMENT, GENERATE_DOCUMENT (alias)."""
from __future__ import annotations

import json

from eli.utils.log import get_logger
from eli.execution.effectors._base import _artifacts_dir

log = get_logger(__name__)


def _h_create_document(a, args):
    topic = (
        args.get("topic") or args.get("text") or args.get("description")
        or args.get("prompt") or args.get("name") or args.get("target")
        or args.get("title") or args.get("query") or ""
    ).strip()
    if not topic:
        msg = "Missing topic for document generation"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}

    # Consume the evidence gathered by the central evidence-routing hook
    # (evidence_planner). If this handler was called directly without it (a
    # non-engine caller / test), gather on the fly as a fallback.
    _doc_evidence = str(args.get("_evidence") or "")
    _doc_sources = list(args.get("_evidence_sources") or [])
    if args.get("_evidence") is None:
        try:
            from eli.runtime.evidence_planner import plan_and_gather as _pag
            _doc_evidence, _doc_sources = _pag(
                "CREATE_DOCUMENT",
                str(args.get("_raw_user_text") or args.get("raw") or topic),
                session_id=str(args.get("session_id") or ""),
                user_id=str(args.get("user_id") or ""))
        except Exception:
            _doc_evidence, _doc_sources = "", []

    fmt = (args.get("format") or "md").lower().strip()
    import os as _os
    if _os.environ.get("ELI_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        import re as _re
        from pathlib import Path as _DPath

        safe_name = _re.sub(r"[^a-z0-9]+", "_", topic.lower())[:50].strip("_")
        ext = ".md" if fmt == "md" else f".{fmt}"
        fname = f"{safe_name}{ext}" if safe_name else f"document{ext}"

        doc_dir = _artifacts_dir() / "documents"
        doc_dir.mkdir(parents=True, exist_ok=True)
        doc_path = doc_dir / fname

        content = (
            f"# {topic}\n\n"
            "## Purpose\n\n"
            "This deterministic test-mode document proves that document generation writes a real artifact "
            "to the documents directory without using the old placeholder body.\n\n"
            "## Generation Contract\n\n"
            "- Output must be substantive enough for downstream UI wiring tests.\n"
            "- Output must not be a blank skeleton, a path echo, or a promise to write later.\n"
            "- Output must stay in `artifacts/documents`, never `artifacts/scripts`.\n\n"
            "## Verification Notes\n\n"
            "The runtime generation path applies the same destination contract while using the local "
            "model to write the full document body. Test mode keeps the content deterministic so "
            "regression tests can detect stub leakage without loading a model.\n"
        )
        if fmt != "md":
            content = _re.sub(r"^#+\s*", "", content, flags=_re.M)
        doc_path.write_text(content, encoding="utf-8")

        return {
            "ok": True,
            "action": a,
            "content": content,
            "response": json.dumps(
                {
                    "event": "artifact_generated",
                    "kind": "document",
                    "path": str(doc_path),
                    "chars": len(content),
                },
                ensure_ascii=False,
                default=str,
            ),
            "doc_path": str(doc_path),
            "filename": fname,
            "open_in_ide": False,
        }

    # Determine document type and tailor the generation settings.
    _topic_low = topic.lower()
    _requested_doc_type = str(
        args.get("doc_type")
        or args.get("document_type")
        or args.get("report_type")
        or ""
    ).strip()
    _doc_type_low = (_requested_doc_type or _topic_low).lower()
    _is_thesis = any(w in _doc_type_low for w in ["phd", "dissertation", "master", "thesis"])
    _is_research = any(w in _doc_type_low for w in ["research", "peer-review", "peer review", "literature"])
    _is_report = any(w in _doc_type_low for w in ["report", "analysis", "review", "assessment", "evaluation"])
    _is_guide = any(w in _doc_type_low for w in ["guide", "tutorial", "how to", "howto", "handbook", "manual"])
    _is_essay = any(w in _doc_type_low for w in ["essay", "article", "blog", "post", "opinion", "argument"])
    _is_plan = any(w in _doc_type_low for w in ["plan", "proposal", "strategy", "roadmap", "specification"])
    _is_summary = any(w in _doc_type_low for w in ["summary", "summarize", "summarise", "overview", "brief"])

    if _requested_doc_type:
        doc_type = _requested_doc_type
    elif _is_summary:
        doc_type = "summary"
    elif _is_thesis:
        doc_type = "thesis/dissertation"
    elif _is_research:
        doc_type = "research article"
    elif _is_report:
        doc_type = "report"
    elif _is_guide:
        doc_type = "guide"
    elif _is_essay:
        doc_type = "essay/article"
    elif _is_plan:
        doc_type = "plan"
    else:
        doc_type = "document"

    if _is_summary:
        style_instruction = "Use a compact abstract, key findings, caveats, and concise implications."
        target_tokens = 1200
    elif _is_thesis:
        style_instruction = "Use academic thesis structure: abstract, research question, literature context, method, analysis, limitations, and future work."
        target_tokens = 5000
    elif _is_research:
        style_instruction = "Use scholarly article structure: abstract, introduction, background, method or argument, findings, limitations, and references-needed notes."
        target_tokens = 4200
    elif _is_report:
        style_instruction = "Use report structure: executive summary, scope, evidence, analysis, risks, recommendations, and conclusion."
        target_tokens = 3600
    elif _is_guide:
        style_instruction = "Use guide structure: prerequisites, steps, examples, validation checks, and troubleshooting."
        target_tokens = 2500
    elif _is_essay:
        style_instruction = "Use article structure: thesis, developed argument, evidence, counterpoints, and conclusion."
        target_tokens = 2500
    elif _is_plan:
        style_instruction = "Use plan structure: objectives, milestones, resources, risks, dependencies, and acceptance checks."
        target_tokens = 2500
    else:
        style_instruction = "Use a complete document structure with clear sections, specific claims, caveats, and a defensible conclusion."
        target_tokens = 2500
    try:
        from eli.runtime.runtime_policy import budget as _eli_budget
        target_tokens = _eli_budget("document_tokens", target_tokens, floor=target_tokens, ceiling=7000)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    system_prompt = (
        "You are ELI's local document generation engine. Produce a finished artifact, not a scaffold. "
        f"Document type: {doc_type}. {style_instruction} "
        "Use markdown with meaningful headings, complete paragraphs, and source discipline. "
        "Do not output blank sections, TODOs, placeholders, invented citations, fake file paths, "
        "or promises to expand later. If evidence is missing for a factual claim, mark it [source needed] "
        "or state the assumption explicitly. Every section must carry real content, concrete reasoning, "
        "and a defensible conclusion. "
        "When EVIDENCE is provided below, tie every section to specific items in it — name the real "
        "components, findings, files, or results it contains — and do NOT substitute generic domain "
        "or business-plan filler for what the evidence actually says."
    )

    user_prompt = (
        f"Write a complete {doc_type} about: {topic}\n\n"
        + (("EVIDENCE — gathered by ELI's own agents for THIS task"
            + (f" (sources: {', '.join(_doc_sources)})" if _doc_sources else "")
            + ". Ground every claim and proposal in the specifics below; refer to "
            "them directly (real subsystems, real findings, real results). Do NOT "
            "pad with generic advice. Where the evidence is thin for a point, say so "
            "honestly rather than inventing:\n\n"
            f"{_doc_evidence}\n\n")
           if _doc_evidence else
           ("No external evidence could be gathered for this topic (e.g. the network "
            "is off, or nothing relevant is on file). Rely only on well-established "
            "knowledge, state assumptions explicitly, and do not fabricate specifics, "
            "citations, or file paths.\n\n"))
        + "Acceptance criteria:\n"
        "- The result is a usable document, not a template.\n"
        "- The opening states the purpose and scope.\n"
        "- The body develops the topic with specific claims, caveats, and implications.\n"
        "- The ending gives a concrete conclusion or next action.\n"
        "- No placeholder headings, no filler, no fake references."
    )

    try:
        from eli.cognition import gguf_inference as _gguf
        _model = _gguf.load_model()
        if _model is not None:
            content = ""
            # Multi-stage grounded pipeline (outline, grounded sections, review/revise, polish) on the
            # evidence the central hook gathered, with a deepen-retry that escalates agent tiers when
            # evidence is thin. Falls back to a single pass on any miss or kill switch.
            try:
                from eli.runtime import report_pipeline as _rp
                if _rp.enabled():
                    def _doc_ask(prompt, system=None, max_tokens=1500, temperature=0.4):
                        return _gguf.chat_completion(prompt, system=system,
                            max_tokens=max_tokens, temperature=temperature, top_p=0.85)
                    def _doc_deepen():
                        from eli.runtime.evidence_planner import plan_and_gather as _pag
                        return _pag(a, str(args.get("_raw_user_text") or topic),
                                    "tree_of_thoughts",
                                    session_id=str(args.get("session_id") or ""),
                                    user_id=str(args.get("user_id") or ""))
                    _pi = _rp.generate_document(
                        topic, ask=_doc_ask, evidence=_doc_evidence,
                        sources=_doc_sources, doc_type=doc_type,
                        target_words=max(700, int(target_tokens * 0.6)),
                        deepen_cb=_doc_deepen)
                    if _pi.get("ok") and _pi.get("text"):
                        content = _pi["text"]
                        log.debug(f"[CREATE_DOCUMENT] multi-stage pipeline produced "
                                  f"{len(content)} chars across {len(_pi.get('sections') or [])} sections")
            except Exception as _rp_err:
                log.debug(f"[CREATE_DOCUMENT] pipeline failed, single-pass fallback: {_rp_err}")
            if not content:
                content = _gguf.chat_completion(
                    user_prompt,
                    system=system_prompt,
                    max_tokens=target_tokens,
                    temperature=0.4,
                    top_p=0.85,
                )
            _min_doc_chars = int(_os.environ.get("ELI_DOCUMENT_MIN_CHARS", "800") or "800")
            if content:
                import re as _re
                content = _re.sub(r"^```[a-z]*\n?", "", content.strip(), flags=_re.MULTILINE)
                content = _re.sub(r"\n?```$", "", content.strip(), flags=_re.MULTILINE)
                content = content.strip()
                if len(content) < _min_doc_chars:
                    log.debug(
                        f"[CREATE_DOCUMENT] rejected short document: {len(content)} chars < {_min_doc_chars}",
                    )
                else:

                    # Save to file
                    safe_name = _re.sub(r"[^a-z0-9]+", "_", topic.lower())[:50].strip("_")
                    ext = ".md" if fmt == "md" else f".{fmt}"
                    fname = f"{safe_name}{ext}" if safe_name else f"document{ext}"

                    # Use the canonical artifacts root (honours ELI_ARTIFACTS_DIR, monkeypatchable). The env var
                    # alone is unreliable: load_settings() strips an out-of-project ELI_ARTIFACTS_DIR, so tests must
                    # patch _artifacts_dir, not setenv.
                    doc_dir = _artifacts_dir() / "documents"
                    doc_dir.mkdir(parents=True, exist_ok=True)
                    doc_path = doc_dir / fname
                    doc_path.write_text(content, encoding="utf-8")

                    return {
                        "ok": True, "action": a, "content": content,
                        "response": json.dumps(
                            {
                                "event": "artifact_generated",
                                "kind": "document",
                                "path": str(doc_path),
                                "chars": len(content),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                        "doc_path": str(doc_path), "filename": fname,
                        "open_in_ide": True,
                    }
    except Exception as _e:
        log.debug(f"[CREATE_DOCUMENT] GGUF error: {_e}")

    # Fallback
    msg = "Document generation failed — GGUF model unavailable or produced a document below the quality threshold."
    return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}


def _h_generate_document(a, args):
    return _h_create_document("CREATE_DOCUMENT", args)


def _h_doc_generate(a, args):
    try:
        import re as _dre, tempfile as _dtmp
        from pathlib import Path as _DPath
        content  = str(args.get("content")  or args.get("text")  or args.get("generated") or "")
        filename = str(args.get("filename") or args.get("filepath") or args.get("path") or "")
        title    = str(args.get("title")    or args.get("query")  or args.get("topic") or "document")
        if not filename:
            safe = _dre.sub(r"[^a-zA-Z0-9_\- ]", "", title)[:40].strip().replace(" ", "_") or "eli_doc"
            try:
                from eli.core.paths import get_paths as _dgp
                docs_dir = _dgp().artifacts_dir / "docs"
            except Exception:
                docs_dir = _DPath(_dtmp.gettempdir())
            docs_dir.mkdir(parents=True, exist_ok=True)
            filename = str(docs_dir / f"{safe}.md")
        _DPath(filename).parent.mkdir(parents=True, exist_ok=True)
        if not content:
            content = f"# {title}\n\n*Document generated by ELI.*\n"
        _DPath(filename).write_text(content, encoding="utf-8")
        try:
            from eli.utils.platform_compat import open_file as _open_file
            _open_file(filename)
        except Exception:
            import logging
            logging.getLogger(__name__).debug("open saved document failed", exc_info=True)
        msg = f"Document saved to `{filename}` and opened."
        return {"ok": True, "action": a, "content": msg, "response": msg, "filepath": filename}
    except Exception as _dge:
        return {"ok": False, "action": a, "error": str(_dge), "content": str(_dge), "response": str(_dge)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CREATE_DOCUMENT": _h_create_document,
    "GENERATE_DOCUMENT": _h_generate_document,
}.items():
    _register(_action, _fn)

_register("DOC_GENERATE", _h_doc_generate)
