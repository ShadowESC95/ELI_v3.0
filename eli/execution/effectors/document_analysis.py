"""Document-analysis effector — ANALYZE_PDF, ANALYZE_PDF_FOLDER (chunked LLM analysis)."""
from __future__ import annotations

from eli.utils.log import get_logger
from eli.execution.effectors._base import _save_artifact

log = get_logger(__name__)


def _maybe_background_file_analysis(action, args):
    from eli.execution.executor_enhanced import _maybe_background_file_analysis as _mb
    return _mb(action, args)


def _h_analyze_pdf(a, args):
    try:
        import re as _re2
        import subprocess as _sp
        from pathlib import Path as _PP
        from eli.perception.analyze_pdfs import analyze

        path = str(args.get("path") or "").strip()
        if not path:
            return {"ok": False, "action": a, "error": "Missing path",
                    "content": "Missing path", "response": "Missing path"}

        _instruction = str(args.get("instruction") or "").strip()
        _instr_low = _instruction.lower()

        analysis = analyze(path)
        doc = analysis.doc
        full_text = "\n\n".join(analysis.chunks) if analysis.chunks else analysis.preview

        # --- Task classification ---
        if any(w in _instr_low for w in ["falsif", "disprove", "refut", "critique", "challenge"]):
            _task_label = "Critical Analysis & Falsification"
            _directive = (
                "You are a rigorous scientific peer reviewer with expertise across physics, "
                "mathematics, and engineering. Your task is to CRITICALLY ANALYSE and attempt "
                "to FALSIFY this document.\n\n"
                "For EVERY claim, equation, model, or prediction you encounter:\n"
                "1. State the claim precisely and verbatim where possible.\n"
                "2. Identify all testable predictions and boundary conditions.\n"
                "3. Propose specific falsification criteria, experiments, or observational tests.\n"
                "4. Check dimensional consistency of every equation.\n"
                "5. Flag hidden assumptions, missing derivation steps, or scope overreach.\n"
                "6. Cross-reference against established physics (GR, QFT, thermodynamics, etc.).\n"
                "7. Verdict per claim: TESTABLE | UNFALSIFIABLE | ALREADY FALSIFIED | VALID.\n\n"
                "Conclude with: (a) overall falsifiability assessment, (b) strongest predictions, "
                "(c) fatal flaws if any, (d) recommended next steps for validation.\n"
                "Be exhaustive. Be brutal. Do not truncate."
            )
            _chunk_task = "critically analyse and attempt to falsify this section"
            _synth_task = "synthesise a complete falsification report from these section analyses"
        elif any(w in _instr_low for w in ["extract equation", "equation", "math"]):
            _task_label = "Equation Extraction & Analysis"
            _directive = (
                "You are ELI, a precise technical analyst. Extract and analyse EVERY equation in the document. "
                "For each: state the equation, define all symbols with units, check dimensional "
                "consistency, identify assumptions, note derivation gaps, and flag any errors."
            )
            _chunk_task = "extract and analyse all equations in this section"
            _synth_task = "compile a complete equation analysis from these section results"
        elif any(w in _instr_low for w in ["summari", "summarize", "summarise", "overview"]):
            _task_label = "Comprehensive Summary"
            _directive = (
                "You are a precise scientific analyst. Produce a COMPREHENSIVE summary covering: "
                "abstract, objectives, theoretical framework, key equations, methods, results, "
                "conclusions, open questions, and implications. Preserve full technical precision. "
                "Do not truncate or abbreviate — complete every section fully."
            )
            _chunk_task = "summarise this section comprehensively"
            _synth_task = "synthesise a complete summary from these section summaries"
        else:
            _task_label = "Technical Analysis"
            _directive = (
                "You are ELI, a rigorous and unsparing technical analyst. "
                "Perform a THOROUGH, COMPLETE analysis as requested. "
                "Be precise, rigorous, and unsparing. Do not truncate."
            )
            _user_req = _instruction or "Provide a thorough technical analysis."
            _chunk_task = f"perform this analysis on the section: {_user_req}"
            _synth_task = f"synthesise a complete document fulfilling this request: {_user_req}"

        from eli.cognition import gguf_inference as _pgi
        _llm_ready = _pgi.load_model() is not None

        def _run_gguf(user_msg: str, sys_msg: str) -> str:
            """Run GGUF with no artificial token cap — use all available context."""
            return _pgi.chat_completion(
                user_msg,
                system=sys_msg,
                max_tokens=None,   # auto: n_ctx - prompt_tokens - 128
                temperature=0.2,
            )

        _section_outputs = []

        if _llm_ready:
            # Determine how many chars fit per chunk given the directive overhead
            # ~4 chars per token, directive ~400 tokens → reserve 1600 chars for directive
            # Use available_generation_tokens to know how much output we can get
            _avail = _pgi.available_generation_tokens(
                _chunk_task + "\n\n" + full_text[:100],
                system=_directive
            )
            # Each chunk: leave room for output generation (roughly n_ctx/2 for input)
            _n_ctx = int(_pgi.context_window() or 0)
            # chars per chunk = roughly half the context minus directive, times 4 chars/token
            _chars_per_chunk = max(3000, (_n_ctx // 2 - 600) * 4)

            # Split full text into overlapping chunks
            _chunks_to_process = []
            _stride = max(1000, _chars_per_chunk - 500)  # 500-char overlap
            _pos = 0
            while _pos < len(full_text):
                _chunks_to_process.append(full_text[_pos: _pos + _chars_per_chunk])
                _pos += _stride
                if _pos >= len(full_text):
                    break

            log.debug(f"[ANALYZE_PDF] {len(_chunks_to_process)} chunk(s), "
                  f"~{_chars_per_chunk} chars each, task={_task_label}")

            for _ci, _chunk_text in enumerate(_chunks_to_process, 1):
                log.debug(f"[ANALYZE_PDF] Processing chunk {_ci}/{len(_chunks_to_process)}...")
                _chunk_prompt = (
                    f"Section {_ci} of {len(_chunks_to_process)}:\n\n"
                    f"{_chunk_text}\n\n"
                    f"Task: {_chunk_task}. Be thorough and complete."
                )
                try:
                    _out = _run_gguf(_chunk_prompt, _directive)
                    if _out and _out.strip():
                        _section_outputs.append(
                            f"## Section {_ci} Analysis\n\n{_out.strip()}"
                        )
                except Exception as _ce:
                    log.debug(f"[ANALYZE_PDF] Chunk {_ci} failed: {_ce}")
                    _section_outputs.append(
                        f"## Section {_ci}\n\n[Processing failed: {_ce}]"
                    )

            # Synthesis pass if multiple chunks
            _analysis_body = None
            if len(_section_outputs) > 1:
                log.debug(f"[ANALYZE_PDF] Running synthesis pass over {len(_section_outputs)} sections...")
                _synth_input = "\n\n".join(_section_outputs)
                # If synthesis input fits, do it in one pass; otherwise just concatenate
                _synth_avail = _pgi.available_generation_tokens(
                    _synth_task + "\n\n" + _synth_input[:200],
                    system=_directive
                )
                if len(_synth_input) // 4 < (_n_ctx // 2):
                    try:
                        _synthesis = _run_gguf(
                            f"{_synth_task}.\n\nSection analyses:\n\n{_synth_input}",
                            _directive
                        )
                        if _synthesis and _synthesis.strip():
                            _analysis_body = (
                                f"{_synthesis.strip()}\n\n"
                                f"---\n\n## Detailed Section Analyses\n\n"
                                + "\n\n".join(_section_outputs)
                            )
                    except Exception as _se2:
                        log.debug(f"[ANALYZE_PDF] Synthesis failed: {_se2}")
                if not _analysis_body:
                    _analysis_body = "\n\n".join(_section_outputs)
            elif _section_outputs:
                _analysis_body = _section_outputs[0]

        if not _analysis_body:
            if not _llm_ready:
                _analysis_body = (
                    "[GGUF model not available — raw extraction below]\n\n"
                    f"Pages: {doc.pages}  |  Characters: {doc.chars:,}\n\n"
                    f"{full_text}"
                )
            else:
                _analysis_body = (
                    "[All chunks failed to process]\n\n"
                    f"Pages: {doc.pages}  |  Characters: {doc.chars:,}\n\n"
                    f"{analysis.preview}"
                )

        if analysis.warnings:
            _analysis_body += f"\n\n---\n\n**Extraction warnings:** {'; '.join(analysis.warnings)}"

        _stem = _PP(doc.path).stem
        _doc_content = (
            f"# {_task_label}: {_stem}\n\n"
            f"**Source:** {doc.path}\n"
            f"**Pages:** {doc.pages}  |  **Characters:** {doc.chars:,}  |  "
            f"**Sections processed:** {len(_section_outputs)}\n"
            f"**Instruction:** {_instruction or 'analyse'}\n\n"
            f"---\n\n"
            f"{_analysis_body}"
        )

        _task_slug = _re2.sub(r"[^a-z0-9]+", "_", _task_label.lower()).strip("_")
        _saved_path = _save_artifact(_doc_content, "documents",
                                     f"{_stem}_{_task_slug}", fmt="docx")
        try:
            from eli.utils.platform_compat import open_file as _open_file
            _opened = bool(_open_file(_saved_path))
        except Exception:
            _opened = False

        _chat_response = (
            f"Document compiled: **{_PP(_saved_path).name}**\n"
            f"Sections processed: {len(_section_outputs)}  |  "
            f"Pages: {doc.pages}  |  Chars: {doc.chars:,}\n"
            f"Saved to: `{_saved_path}`"
            + ("\n_(opening…)_" if _opened else "")
        )
        return {"ok": True, "action": a, "content": _chat_response,
                "response": _chat_response, "path": doc.path,
                "pages": doc.pages, "chars": doc.chars,
                "sections": len(_section_outputs), "saved_to": _saved_path}
    except Exception as e:
        import traceback as _tb
        log.debug(f"[ANALYZE_PDF] Fatal: {_tb.format_exc()}")
        return {"ok": False, "action": a, "error": str(e),
                "content": str(e), "response": str(e)}


def _h_analyze_pdf_folder(a, args):
    try:
        import os as _ospdf
        from eli.perception.analyze_pdfs import (
            analyze_folder, store_analysis_to_memory, PDFAnalysis, PDFDoc)
        folder = str(args.get("folder") or args.get("path") or "").strip()
        if not folder:
            return {"ok": False, "action": a, "error": "Missing folder path",
                    "content": "Missing folder path", "response": "Missing folder path"}
        # Heavy folders → run async and hand back a job id (don't block).
        _bg = _maybe_background_file_analysis(a, args)
        if _bg is not None:
            return _bg
        recursive = bool(args.get("recursive", True))
        limit = args.get("limit")
        result = analyze_folder(folder, recursive=recursive, limit=int(limit) if limit else None)
        results = result.get("results", []) or []
        count = result.get("count", 0)
        errors = result.get("errors", []) or []
        _fname = _ospdf.path.basename(folder.rstrip("/")) or folder
        if count == 0:
            msg = (f"No readable PDF text found in “{_fname}” ({len(errors)} unreadable). "
                   f"The PDFs may be image-only/scanned (need OCR) or the folder has none.")
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "count": 0, "stored": 0, "errors": errors}

        # ── Per-file mode ───────────────────────────────────────────────
        # "summarise each file / put it in a document": summarise EVERY doc
        # individually, assemble a structured document, and write a real
        # .docx (md fallback). Returned inline too, so `check job N` surfaces
        # the full per-file breakdown — not a single folder-level blurb.
        if args.get("per_file") or args.get("each_file"):
            from eli.cognition import gguf_inference as _gi
            _model_ok = False
            try:
                _model_ok = _gi.load_model() is not None
            except Exception:
                _model_ok = False
            _doc_blocks: list = []
            _per_file_done = 0
            for r in results:
                _doc = r.get("doc", {}) or {}
                _name = _ospdf.path.basename(str(_doc.get("path", ""))) or "document.pdf"
                _pages = _doc.get("pages", "?")
                # Use the richest text we extracted: chunks joined, capped.
                _chunks = r.get("chunks") or []
                _body = ("\n\n".join(_chunks) if _chunks else (r.get("preview") or "")).strip()
                _doc_summary = ""
                if _body and _model_ok:
                    try:
                        _ask = (f"Summarise this single research document titled “{_name}”. "
                                f"Cover: (1) the central topic/claim, (2) the methods/formalism used, "
                                f"(3) key findings or conclusions, and (4) any notable equations or "
                                f"results named in the text. Be specific and grounded ONLY in the text "
                                f"below — do not invent results or citations.\n\n")
                        _sys = "You are a precise technical analyst summarising one research PDF."
                        # the body gets the window left after the instruction and the reply
                        from eli.cognition.context_budget import chars_per_token as _cpt_pdf
                        _room = int(max(0, int(_gi.context_window() or 0) - 520) * float(_cpt_pdf())
                                    ) - len(_ask) - len(_sys)
                        _doc_summary = (_gi.chat_completion(
                            _ask + _body[:max(0, _room)],
                            system=_sys,
                            max_tokens=520, temperature=0.3) or "").strip()
                    except Exception as _pf_err:
                        log.debug(f"[ANALYZE_PDF_FOLDER] per-file summary failed for {_name}: {_pf_err}")
                        _doc_summary = ""
                if not _doc_summary:
                    _prev = (r.get("preview") or "").strip()
                    _doc_summary = (f"(No model summary available — extracted opening text:)\n\n{_prev[:800]}"
                                    if _prev else "(No readable text extracted from this document.)")
                else:
                    _per_file_done += 1
                _doc_blocks.append(f"## {_name}\n_({_pages} pages)_\n\n{_doc_summary}")

            _doc_title = f"Per-file summaries — {_fname}"
            _doc_md = (f"# {_doc_title}\n\n"
                       f"{count} document(s) summarised individually.\n\n"
                       + "\n\n".join(_doc_blocks))
            if errors:
                _doc_md += f"\n\n## Unreadable files\n\n{len(errors)} file(s) could not be read."

            _fmt = str(args.get("format") or "docx").strip().lower().lstrip(".")
            if _fmt not in ("docx", "md", "txt"):
                _fmt = "docx"
            try:
                _doc_path = _save_artifact(_doc_md, "documents",
                                           f"{_fname}_per_file_summaries", fmt=_fmt)
            except Exception as _save_err:
                log.debug(f"[ANALYZE_PDF_FOLDER] doc save failed ({_save_err}); md fallback")
                _doc_path = _save_artifact(_doc_md, "documents",
                                           f"{_fname}_per_file_summaries", fmt="md")

            # Persist each doc to memory as well (same as the folder path).
            try:
                from eli.memory import resolve_db_paths as _rdb
                _db = _rdb().user_db
                if _db:
                    for r in results:
                        try:
                            store_analysis_to_memory(_db, PDFAnalysis(
                                doc=PDFDoc(**(r.get("doc") or {})),
                                preview=r.get("preview", ""),
                                chunks=r.get("chunks", []) or [],
                                warnings=r.get("warnings", []) or []))
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
            except Exception:
                log.debug("suppressed exception", exc_info=True)

            _header = (f"Summarised {count} document(s) in “{_fname}” individually "
                       f"({_per_file_done} via model). Saved to:\n{_doc_path}\n")
            msg = _header + "\n" + "\n\n".join(_doc_blocks)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "count": count, "per_file": True, "document_path": _doc_path,
                    "errors": errors}

        # Per-document index + aggregated opening-pages text for a REAL summary.
        index_lines: list = []
        preview_parts: list = []
        for r in results:
            _doc = r.get("doc", {}) or {}
            _name = _ospdf.path.basename(str(_doc.get("path", "")))
            _pages = _doc.get("pages", "?")
            _chars = int(_doc.get("chars", 0) or 0)
            _prev = (r.get("preview") or "").strip().replace("\n", " ")
            index_lines.append(f"  {len(index_lines) + 1}. {_name} ({_pages}p, {_chars} chars)")
            if _prev:
                preview_parts.append(f"### {_name}\n{_prev[:1400]}")

        # Synthesize an actual content summary from the extracted text — not a
        # bare count. Capped so the prompt fits n_ctx.
        summary = ""
        try:
            from eli.cognition import gguf_inference as _gi
            if preview_parts and _gi.load_model() is not None:
                _blob = "\n\n".join(preview_parts)[:14000]
                summary = (_gi.chat_completion(
                    f"Below are the opening pages of {count} PDF documents from the folder "
                    f"“{_fname}”. Write a concise, technical summary of what this body of work "
                    f"covers: the central topics, methods/formalism, and how the documents relate. "
                    f"Be specific and grounded ONLY in the text shown — do not invent results.\n\n{_blob}",
                    system="You are a precise technical analyst summarising a collection of research PDFs.",
                    max_tokens=700, temperature=0.3) or "").strip()
        except Exception as _sum_err:
            log.debug(f"[ANALYZE_PDF_FOLDER] summary synthesis failed: {_sum_err}")
            summary = ""

        # Persist each doc to memory (proper dataclass reconstruction; report honestly).
        stored, store_failed = 0, 0
        try:
            from eli.memory import resolve_db_paths as _rdb
            _db = _rdb().user_db
        except Exception:
            _db = None
        if _db:
            for r in results:
                try:
                    pa = PDFAnalysis(
                        doc=PDFDoc(**(r.get("doc") or {})),
                        preview=r.get("preview", ""),
                        chunks=r.get("chunks", []) or [],
                        warnings=r.get("warnings", []) or [],
                    )
                    store_analysis_to_memory(_db, pa)
                    stored += 1
                except Exception:
                    store_failed += 1

        parts = [f"Analysed {count} PDF(s) in “{_fname}”:", ""]
        if summary:
            parts += [summary, ""]
        parts.append("Documents:")
        parts += index_lines
        if errors:
            parts.append(f"\n({len(errors)} file(s) could not be read.)")
        msg = "\n".join(parts)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "count": count, "stored": stored, "store_failed": store_failed,
                "errors": errors, "summary": summary}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "ANALYZE_PDF": _h_analyze_pdf,
    "ANALYZE_PDF_FOLDER": _h_analyze_pdf_folder,
}.items():
    _register(_action, _fn)
