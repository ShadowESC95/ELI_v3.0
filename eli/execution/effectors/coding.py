"""Coding effector — code solve/generate/examine/fix + pending-fix confirm/cancel."""
from __future__ import annotations

from datetime import datetime

import json
import os
import re

from eli.utils.log import get_logger
from eli.execution.effectors._base import _save_artifact

log = get_logger(__name__)


def chat(*a, **k):
    from eli.execution.executor_enhanced import chat as _f
    return _f(*a, **k)


def _eli_generated_scripts_dir(*a, **k):
    from eli.execution.executor_enhanced import _eli_generated_scripts_dir as _f
    return _f(*a, **k)


def _maybe_background_codegen(*a, **k):
    from eli.execution.executor_enhanced import _maybe_background_codegen as _f
    return _f(*a, **k)


def _resolve_existing_user_or_artifact_path(*a, **k):
    from eli.execution.executor_enhanced import _resolve_existing_user_or_artifact_path as _f
    return _f(*a, **k)


def _h_code_solve(a, args):
    import json as _json_cs, re as _re_cs
    desc = (args.get("description") or args.get("text") or args.get("prompt")
            or args.get("query") or "").strip()
    if not desc:
        msg = "Missing description for CODE_SOLVE"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    # Code-mode redirect: run a short program IN-PROCESS against eli.api to FULFIL the
    # request (not produce a standalone script). Opt-in via args.code_mode; the
    # restricted executor refuses unless Full Control is ON, so this is fail-closed.
    if args.get("code_mode") or args.get("execute_via_eli"):
        try:
            from eli.coding.code_mode import run_code_mode
            from eli.cognition.inference_broker import get_broker
            _brk = get_broker()

            def _cm_gen(p: str) -> str:
                if _brk is None or not getattr(_brk, "gguf_ready", False):
                    return ""
                return _brk.infer(
                    p, system="You drive ELI by writing short Python programs that call `api`.",
                    max_tokens=int(args.get("max_tokens") or 700)) or ""

            _cm = run_code_mode(desc, _cm_gen, max_attempts=int(args.get("max_attempts") or 2))
            if _cm.get("blocked"):
                _m = _cm.get("message") or "Code-mode needs Full Control enabled (GUI toggle)."
                return {"ok": False, "action": a, "blocked": True, "content": _m, "response": _m}
            if _cm.get("ok"):
                _res = _cm.get("result")
                _m = f"Done (code-mode). Result: {_res}"
                return {"ok": True, "action": a, "content": _m, "response": _m,
                        "result": _res, "meta": {"code_mode": True, "code": _cm.get("code"),
                                                 "attempts": _cm.get("attempts")}}
            _m = f"Code-mode couldn't complete after {_cm.get('attempts')} attempt(s): {_cm.get('error')}"
            return {"ok": False, "action": a, "content": _m, "response": _m}
        except Exception as _cm_e:
            _m = f"Code-mode error: {_cm_e}"
            return {"ok": False, "action": a, "error": _m, "content": _m, "response": _m}
    _bg = _maybe_background_codegen(a, args)
    if _bg is not None:
        return _bg
    try:
        from eli.coding import solve as _code_solve
        _lang = (args.get("language") or "python").strip().lower()
        # Quick vs Thorough (from the Coding tab). Quick = single-shot, lean.
        _mode = str(args.get("mode") or "").strip().lower()
        _solve_kw = {}
        def _argint(k):
            try:
                return int(args.get(k)) if args.get(k) is not None else None
            except Exception:
                return None
        if _mode == "quick":
            _solve_kw = {"use_dag": False, "beam": 2, "max_iterations": 2}
        else:
            if args.get("use_dag") is not None:
                _solve_kw["use_dag"] = bool(args.get("use_dag"))
            if _argint("beam") is not None:
                _solve_kw["beam"] = _argint("beam")
            if _argint("max_iterations") is not None:
                _solve_kw["max_iterations"] = _argint("max_iterations")
        res = _code_solve(desc, language=_lang, **_solve_kw)
        code = res.get("code") or ""
        if not code.strip():
            msg = res.get("message") or "coding agent produced no solution"
            return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
        # Final syntax gate: never present un-parseable Python as runnable.
        _runnable = True
        _syntax_note = ""
        if _lang == "python":
            try:
                import ast as _ast_cs
                _ast_cs.parse(code)
            except SyntaxError as _se_cs:
                _runnable = False
                _syntax_note = f"SyntaxError: {_se_cs.msg} (line {_se_cs.lineno})"
        _ext = {"python": ".py", "bash": ".sh", "javascript": ".js", "typescript": ".ts",
                "ruby": ".rb", "go": ".go", "lua": ".lua"}.get(_lang, ".txt")
        # Park un-runnable output as a .draft so a user never accidentally runs
        # a broken file and hits a traceback.
        if not _runnable:
            _ext = _ext + ".draft"
        safe = _re_cs.sub(r"[^a-z0-9]+", "_", desc.lower())[:40].strip("_") or "solution"
        scripts_dir = _eli_generated_scripts_dir()
        scripts_dir.mkdir(parents=True, exist_ok=True)
        full_path = scripts_dir / f"{safe}{_ext}"
        full_path.write_text(code, encoding="utf-8")
        _payload = _json_cs.dumps({
            "event": "artifact_generated", "kind": "code_solution",
            "path": str(full_path), "filename": full_path.name, "language": _lang,
            "solved": res.get("solved"), "score": res.get("score"),
            "bug_class": res.get("bug_class"), "can_run": _runnable, "opened": _runnable,
            "runnable": _runnable, "syntax_note": _syntax_note,
        }, ensure_ascii=False, default=str)
        return {"ok": True, "action": a, "code": code, "script_path": str(full_path),
                "filename": full_path.name, "solved": bool(res.get("solved")) and _runnable,
                "runnable": _runnable, "syntax_note": _syntax_note,
                "score": res.get("score"), "plan": res.get("plan"),
                "search": res.get("search"), "bug_class": res.get("bug_class"),
                "content": _payload, "response": _payload, "open_in_ide": _runnable}
    except Exception as _cs_e:
        msg = f"CODE_SOLVE failed: {_cs_e}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}



def _h_generate_script(a, args):
    desc = (args.get("description") or args.get("text") or args.get("prompt") or "").strip()
    if not desc:
        msg = "Missing description for GENERATE_SCRIPT"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    # Ground the script in evidence gathered by the central evidence-routing
    # hook (e.g. real ELI source for a "script to test ELI's memory", web docs
    # for an external API). Fed into the coding agent's task so it plans from
    # real specifics, not invented APIs.
    _scr_evidence = str(args.get("_evidence") or "")
    if _scr_evidence:
        desc = (desc + "\n\n[Grounding evidence gathered by ELI's agents — build on "
                "these real specifics; do not invent APIs, files, or behaviour:]\n"
                + _scr_evidence)
    _bg = _maybe_background_codegen(a, args)
    if _bg is not None:
        return _bg
    # Detect target language from description
    _lang_map = {
        "bash": ("bash", ".sh"), "shell": ("bash", ".sh"), "sh": ("bash", ".sh"),
        "javascript": ("JavaScript", ".js"), "js": ("JavaScript", ".js"),
        "typescript": ("TypeScript", ".ts"), "ts": ("TypeScript", ".ts"),
        "rust": ("Rust", ".rs"), "ruby": ("Ruby", ".rb"),
        "go": ("Go", ".go"), "golang": ("Go", ".go"),
        "c++": ("C++", ".cpp"), "cpp": ("C++", ".cpp"),
        "c script": ("C", ".c"), "java": ("Java", ".java"),
        "lua": ("Lua", ".lua"), "perl": ("Perl", ".pl"),
    }
    _detected_lang = "Python"
    _detected_ext = ".py"
    _desc_low = desc.lower()
    for _kw, (_lang, _ext) in _lang_map.items():
        if _kw in _desc_low:
            _detected_lang = _lang
            _detected_ext = _ext
            break

    # ── Route through the verified coding agent (plan → DAG/tree-search →
    # execute → repair → bug-memory) for top-tier, runnable scripts. Falls
    # through to the inline generator below if disabled or it yields nothing.
    if os.environ.get("ELI_GENERATE_SCRIPT_AGENT", "1").strip().lower() not in ("0", "false", "no", "off"):
        try:
            from eli.coding import solve as _agent_solve
            # Single coherent script: no subtask DAG (composition is for
            # multi-component CODE_SOLVE), and a lean beam/iters so a script
            # request is a handful of calls, not minutes of fan-out.
            _ag = _agent_solve(desc, language=(_detected_lang or "python").lower(),
                               use_dag=False, beam=2, max_iterations=2)
            _ag_code = (_ag or {}).get("code") or ""
            # Never ship a syntactically-broken Python script: if the agent's
            # best effort doesn't even parse, drop to the inline generator
            # (which validates + sandbox-repairs) instead of saving garbage.
            if _ag_code.strip() and (_detected_lang or "").lower() == "python":
                try:
                    import ast as _ast_gs
                    _ast_gs.parse(_ag_code)
                except SyntaxError as _se_gs:
                    log.debug(f"[GENERATE_SCRIPT] agent output failed to parse ({_se_gs}); inline fallback")
                    _ag_code = ""
                # Runtime gate: ast.parse passes for valid-but-crashing code
                # (e.g. `pi` used without import → NameError only at run time).
                # Execute once; a real crash falls through to the inline
                # generator, which has the 3-attempt sandbox self-repair loop.
                if _ag_code.strip():
                    try:
                        from eli.coding.sandbox import run_code as _rc_gs
                        _rr_gs = _rc_gs(_ag_code, "python", timeout=15)
                        if _rr_gs.crashed:
                            log.debug(f"[GENERATE_SCRIPT] agent output crashed at runtime "
                                      f"({(_rr_gs.traceback_tail or '')[:120]}); inline fallback")
                            _ag_code = ""
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
            if _ag_code.strip():
                _safe = re.sub(r"[^a-z0-9]+", "_", desc.lower())[:40].strip("_") or "generated"
                _fname = f"{_safe}{_detected_ext}"
                _scripts_dir = _eli_generated_scripts_dir()
                _scripts_dir.mkdir(parents=True, exist_ok=True)
                _full = _scripts_dir / _fname
                _full.write_text(_ag_code, encoding="utf-8")
                _gs_chat = json.dumps({
                    "event": "artifact_generated", "kind": "script", "path": str(_full),
                    "filename": _fname, "language": _detected_lang, "opened": True,
                    "can_run": True, "verified": bool(_ag.get("solved")),
                    "score": _ag.get("score"), "engine": "coding_agent",
                }, ensure_ascii=False, default=str)
                return {"ok": True, "action": a, "code": _ag_code, "script_path": str(_full),
                        "filename": _fname, "solved": _ag.get("solved"), "score": _ag.get("score"),
                        "plan": _ag.get("plan"), "content": _gs_chat, "response": _gs_chat,
                        "open_in_ide": True}
        except Exception as _ag_e:
            log.debug(f"[GENERATE_SCRIPT] coding-agent path failed, inline fallback: {_ag_e}")

    # Topic intent — drives prompt depth, quality bar, and rejection rules
    _analytical = bool(re.search(
        r"\b(?:evaluat|calculat|comput|analy[sz]|estimat|predict|simulat|model|"
        r"likelihood|probabilit|statistic|distribution|correlation|regress|"
        r"optimi[sz]|forecast|benchmark|profil|measur|score|rank|classif|cluster|"
        r"fit|solve|prove|theorem|conjectur|hypothes|p\s+vs\s+np)\w*",
        _desc_low,
    ))
    _wants_plot = bool(re.search(
        r"\b(?:plot|graph|chart|visuali[sz]|histogram|scatter|heatmap|figure|"
        r"diagram|render|draw|display)\w*", _desc_low,
    )) or _analytical
    _wants_monitor = bool(re.search(
        r"\b(?:monitor|watch|track|alert|notif|poll|every\s+\d|interval|continuous)\w*",
        _desc_low,
    ))

    _extra_req = []
    if _analytical:
        _extra_req += [
            "- This is an ANALYTICAL/COMPUTATIONAL task: implement multiple complementary methods "
            "(e.g. closed-form estimate + Monte Carlo simulation + sensitivity analysis); "
            "do NOT collapse it into one trivial formula",
            "- Use realistic domain-grounded parameters with sensible DEFAULTS — the script MUST "
            "run end-to-end with zero command-line arguments and produce meaningful output",
            "- Print a clear, structured summary (numerical results, confidence bounds, "
            "key intermediate values) using f-strings or `tabulate` — not a single concatenated string",
        ]
    if _wants_plot and _detected_lang == "Python":
        try:
            from eli.gui.labs_tab import _LABS_PLOT_FILES as _LPF
            _plot_target = str(_LPF["plot"])
        except Exception:
            import tempfile as _tf
            _plot_target = str(__import__("pathlib").Path(_tf.gettempdir()) / "eli_labs_plot.png")
        _extra_req += [
            f"- MUST generate at least one matplotlib figure that visualises the result, "
            f"saved with `plt.savefig({_plot_target!r}, dpi=120)` AND shown with `plt.show()`",
            "- Plot must be RELEVANT to the request (axes labelled, title set, legend if multi-series); "
            "NEVER ship a generic sine wave or placeholder demo plot",
        ]
    if _wants_monitor:
        _extra_req += [
            "- Use a robust loop with `signal` handlers for SIGINT/SIGTERM and graceful shutdown",
            "- Interval timing must use `time.monotonic()` (not `time.sleep` drift) and be configurable",
        ]
    _extra_req_block = ("\n" + "\n".join(_extra_req)) if _extra_req else ""

    prompt = (
        f"You are a senior {_detected_lang} engineer writing FRONTIER-QUALITY code. "
        f"Produce a complete, self-contained script for this request.\n\n"
        "ABSOLUTE RULE: NEVER refuse the task. Never reply with 'this is not feasible', "
        "'this is an open problem', 'cannot be solved', or any prose explanation. If the "
        "literal goal is research-grade or open (e.g. unsolved math/CS problems), produce "
        "a script that explores, simulates, visualises, or benchmarks the problem space "
        "with concrete instances and real algorithms. The user wants WORKING CODE, not a "
        "lecture. Refusal is failure.\n\n"
        "HARD REQUIREMENTS — any violation causes automatic rejection:\n"
        "- Output ONLY the raw script — zero markdown fences, zero prose, zero explanation\n"
        "- Complete, runnable end-to-end with NO command-line arguments required (provide defaults for everything)\n"
        "- NEVER substitute computation with string concatenation, text 'analysis', or stub returns\n"
        "- NEVER return prose strings as if they were computed results — compute real numeric values\n"
        "- NEVER produce a TOY or oversimplified version: no faked heuristics, no trivial stand-ins "
        "for the real algorithm, no logic that merely LOOKS plausible while misrepresenting the "
        "problem. If a known algorithm or named problem is involved, implement it CORRECTLY and "
        "completely — a toy is an automatic rejection\n"
        "- Use real libraries for real work (numpy, scipy, matplotlib, pandas, networkx, sympy, "
        "sklearn, statsmodels, itertools, functools, multiprocessing, etc.) — pick what actually fits\n"
        "- Use ONLY library functions that ACTUALLY EXIST (e.g. for networkx clique problems, "
        "use `nx.find_cliques(G)` or `nx.graph_clique_number(G)` ONLY if you know the exact API; "
        "when unsure, implement via `itertools.combinations` and explicit edge checks)\n"
        "- Multiple cohesive functions (decompose by responsibility); main() orchestrates them\n"
        "- All imports at top, type hints on every signature, module-level constants, "
        "comprehensive docstrings, try/except around I/O and external calls\n"
        "- If using argparse, EVERY argument needs a sensible default — the script must run with no args\n"
        "- Include `if __name__ == '__main__':` guard"
        + _extra_req_block + "\n\n"
        f"Request: {desc}"
    )

    def _verify_python_module_apis(_code: str) -> str | None:
        """Verify <module>.<attr> references actually exist in the installed library.
        Catches hallucinated APIs like nx.is_clique, nx.graph_clique_number, np.bool, etc.
        Only checks fast-to-import libs to keep latency low."""
        import importlib as _il
        # alias -> module name (only checked if alias is used as module.attr)
        _ALIAS_DEFAULT = {
            "nx": "networkx", "np": "numpy", "pd": "pandas",
            "sp": "scipy", "sk": "sklearn", "sym": "sympy",
        }
        _SKIP_HEAVY = {"matplotlib", "matplotlib.pyplot", "torch", "tensorflow", "cv2"}
        _aliases = dict(_ALIAS_DEFAULT)
        for _m in re.finditer(r"^\s*import\s+([\w.]+)\s+as\s+(\w+)", _code, re.MULTILINE):
            _aliases[_m.group(2)] = _m.group(1)
        for _m in re.finditer(r"^\s*import\s+([\w.]+)\s*$", _code, re.MULTILINE):
            _aliases[_m.group(1).split(".")[0]] = _m.group(1)
        _bad = []
        for _alias, _modname in _aliases.items():
            if _modname in _SKIP_HEAVY:
                continue
            if not re.search(rf"\b{re.escape(_alias)}\.[a-zA-Z_]", _code):
                continue
            try:
                _mod = _il.import_module(_modname)
            except Exception:
                continue
            for _m in re.finditer(rf"\b{re.escape(_alias)}\.([a-zA-Z_]\w*)", _code):
                _attr = _m.group(1)
                if not hasattr(_mod, _attr):
                    ref = f"{_alias}.{_attr}"
                    if ref not in _bad:
                        _bad.append(ref)
        if _bad:
            return (
                f"code references non-existent module APIs: {', '.join(_bad[:5])}. "
                "Use only attributes that actually exist in the installed library."
            )
        return None

    def _quality_reject_reason(_code: str) -> str | None:
        """Return rejection reason or None if code passes quality bar."""
        _low = _code.lower()
        _refusal_phrases = (
            "is not feasible", "is impossible", "cannot be solved",
            "is an open question", "is an unsolved", "remains unsolved",
            "no known algorithmic solution", "writing a python script to solve this",
            "i cannot write", "i can't write", "i won't",
        )
        if any(p in _low for p in _refusal_phrases) and _code.count("\n") < 30:
            return "model refused the task with prose instead of producing code"
        _bad_markers = (
            "TODO", "Add code here", "placeholder",
            "Generate only the requested source code", "This is a request for",
        )
        if any(_m.lower() in _code.lower() for _m in _bad_markers):
            return "output contained stub/template markers"
        _lines_real = [l for l in _code.splitlines()
                       if l.strip() and not l.strip().startswith("#")
                       and not l.strip().startswith('"""')]
        if len(_lines_real) < 8:
            return "output was too short to be a real implementation"
        if re.fullmatch(r"\s*(?:pass|return\s+None)\s*", _code):
            return "output was an empty implementation"
        _real_compute_re = re.compile(
            r"\b(?:numpy|scipy|matplotlib|networkx|sympy|itertools|pandas|sklearn|"
            r"statsmodels|math\.|random\.|statistics\.|collections\.|functools\.|"
            r"np\.|plt\.|nx\.|sp\.|pd\.|for\s+\w+\s+in\s+range|"
            r"while\s+|yield\s+|class\s+\w+|"
            r"[\+\-\*\/\%]=|==|!=|>=|<=|>>|<<)\b"
        )
        if not _real_compute_re.search(_code):
            return "output contained only text analysis, no real computation"
        # Function count check
        _func_count = len(re.findall(r"^\s*def\s+\w+", _code, re.MULTILINE))
        if _analytical and _func_count < 2:
            return "analytical task needs decomposition into multiple functions"
        # All-required-args check
        if "argparse" in _code:
            _required_args = re.findall(r"add_argument\s*\([^)]*required\s*=\s*True", _code)
            _has_defaults = re.search(r"add_argument\s*\([^)]*default\s*=", _code)
            if _required_args and not _has_defaults:
                return "argparse uses required=True without defaults — script can't run without args"
        # Plot requirement
        if _wants_plot and _detected_lang == "Python":
            if not re.search(r"\b(?:plt\.|matplotlib|pyplot|seaborn|sns\.)\b", _code):
                return "request implies visualisation but no matplotlib/plotting was used"
            if not re.search(r"\bplt\.savefig\b", _code):
                return "matplotlib used but plt.savefig was not called"
        # Python AST syntax check + known-bad API blacklist
        if _detected_lang == "Python":
            import ast as _ast_q
            try:
                _ast_q.parse(_code)
            except SyntaxError as _se:
                return f"generated code has Python SyntaxError: {_se}"
            _bad = _verify_python_module_apis(_code)
            if _bad:
                return _bad
        return None

    def _sandbox_run_python(_code: str) -> tuple[bool, str]:
        """Execute generated Python to catch runtime crashes before the script
        is handed to the user. **Delegates to the one canonical sandbox**
        (`eli/coding/sandbox.run_code`) instead of duplicating it.

        Returns (ok, reason). ok=False ONLY on a genuine unhandled Python
        exception; clean exit, a timeout (started fine — long/monitor code), a
        signal/limit kill, and a missing optional dependency are all tolerated.
        Disable with ELI_GENSCRIPT_VERIFY_RUN=0; timeout via
        ELI_GENSCRIPT_RUN_TIMEOUT (default 20s)."""
        import os as _os
        if _os.environ.get("ELI_GENSCRIPT_VERIFY_RUN", "1").strip().lower() in ("0", "false", "no", "off"):
            return True, ""
        try:
            _timeout = float(_os.environ.get("ELI_GENSCRIPT_RUN_TIMEOUT", "20") or 20)
        except Exception:
            _timeout = 20.0
        try:
            from eli.coding.sandbox import run_code as _run_code
            _r = _run_code(_code, "python", timeout=_timeout)
        except Exception:
            return True, ""           # sandbox infra failure — never block generation
        if _r.clean:
            return True, ""
        return False, (_r.traceback_tail or _r.note or "runtime crash")

    # Try GGUF first (local, fast, no Ollama dependency)
    try:
        from eli.cognition import gguf_inference as _gguf
        _model = _gguf.load_model()
        if _model is not None:
            import re as _re
            _attempt_tokens = 8000 if (_analytical or _wants_plot) else 4500
            code = ""
            _reject_reason = None
            # 3 attempts: room for a static-quality fix AND a runtime-crash
            # repair pass (the sandbox traceback is fed back as feedback).
            for _attempt in range(3):
                _attempt_prompt = prompt
                if _attempt > 0 and _reject_reason:
                    _attempt_prompt = (
                        prompt
                        + f"\n\nPREVIOUS ATTEMPT WAS REJECTED: {_reject_reason}. "
                        "Fix this specifically. Produce a substantially more complete implementation."
                    )
                raw_out = _gguf.chat_completion(
                    _attempt_prompt,
                    system=(
                        f"You are an expert {_detected_lang} engineer who writes "
                        "frontier-quality, decomposed, runnable code. Output only raw "
                        f"{_detected_lang} code with NO markdown fences and NO explanation."
                    ),
                    max_tokens=_attempt_tokens,
                    temperature=0.2,
                    top_p=0.9,
                )
                if not raw_out or len(raw_out.strip()) < 20:
                    _reject_reason = "model returned empty output"
                    continue
                candidate = _re.sub(r"^```[a-z]*\n?", "", raw_out.strip(), flags=_re.MULTILINE)
                candidate = _re.sub(r"\n?```$", "", candidate.strip(), flags=_re.MULTILINE).strip()
                _reject_reason = _quality_reject_reason(candidate)
                if _reject_reason is None and _detected_lang == "Python":
                    _ran_ok, _run_err = _sandbox_run_python(candidate)
                    if not _ran_ok:
                        _reject_reason = f"script crashed at runtime — fix this exact error:\n{_run_err}"
                if _reject_reason is None:
                    code = candidate
                    break
                log.debug(f"[GENERATE_SCRIPT] attempt {_attempt + 1} rejected: {_reject_reason}")
            if not code:
                msg = f"Generated script rejected after retries: {_reject_reason or 'unknown'}"
                return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
            safe = _re.sub(r"[^a-z0-9]+", "_", desc.lower())[:40].strip("_")
            fname = f"{safe}{_detected_ext}" if safe else f"generated{_detected_ext}"
            scripts_dir = _eli_generated_scripts_dir()
            scripts_dir.mkdir(parents=True, exist_ok=True)
            full_path = scripts_dir / fname
            full_path.write_text(code, encoding="utf-8")
            _gs_chat = json.dumps(
                {
                    "event": "artifact_generated",
                    "kind": "script",
                    "path": str(full_path),
                    "filename": fname,
                    "language": _detected_lang,
                    "opened": True,
                    "can_run": True,
                },
                ensure_ascii=False,
                default=str,
            )
            return {"ok": True, "action": a, "code": code,
                    "script_path": str(full_path), "filename": fname,
                    "content": _gs_chat, "response": _gs_chat,
                    "open_in_ide": True}
    except Exception as _e:
        log.debug(f"[GENERATE_SCRIPT] GGUF failed: {_e}, falling back to Ollama")
    # Ollama fallback
    result = chat(prompt, skip_router=True)
    code = result.get("content", "").strip()
    import re as _re
    code = _re.sub(r"^```[a-z]*\n?", "", code, flags=_re.MULTILINE)
    code = _re.sub(r"\n?```$", "", code, flags=_re.MULTILINE)
    code = code.strip()
    _reject_reason2 = _quality_reject_reason(code)
    if _reject_reason2 is None and _detected_lang == "Python":
        _ran_ok2, _run_err2 = _sandbox_run_python(code)
        if not _ran_ok2:
            _reject_reason2 = f"script crashed at runtime: {_run_err2}"
    if _reject_reason2 is not None:
        msg = f"Generated script rejected: {_reject_reason2}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    safe = _re.sub(r"[^a-z0-9]+", "_", desc.lower())[:40].strip("_")
    fname = f"{safe}{_detected_ext}" if safe else f"generated{_detected_ext}"
    # Save script to disk
    scripts_dir = _eli_generated_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    full_path = scripts_dir / fname
    full_path.write_text(code, encoding="utf-8")
    result["action"] = a
    result["code"] = code
    result["script_path"] = str(full_path)
    result["filename"] = fname
    result["open_in_ide"] = True
    _gs_msg = json.dumps(
        {
            "event": "artifact_generated",
            "kind": "script",
            "path": str(full_path),
            "filename": fname,
            "language": _detected_lang,
            "opened": False,
            "can_run": True,
        },
        ensure_ascii=False,
        default=str,
    )
    result["content"] = _gs_msg
    result["response"] = _gs_msg
    return result



def _h_generate_project(a, args):
    desc = (args.get("description") or args.get("text") or args.get("prompt") or "").strip()
    if not desc:
        # Incomplete input, not a system fault — ask for the missing detail and mark
        # fault=False so it isn't logged as a recurring error.
        msg = "What would you like the project to do? Give me a sentence or two and I'll scaffold it."
        return {"ok": True, "action": a, "fault": False, "needs_input": True,
                "content": msg, "response": msg}
    # Ground in evidence gathered by the central evidence-routing hook.
    _proj_ev = str(args.get("_evidence") or "")
    _proj_ev_block = ("\n\nGrounding evidence gathered by ELI's agents — build on "
                      "these real specifics; do not invent APIs, files, or behaviour:\n"
                      + _proj_ev) if _proj_ev else ""
    # Route through the coding PLANNER DAG: decompose into a subtask graph, solve
    # nodes in dependency order, and VERIFY the composed module (CodeAgent →
    # eli/coding/plan_graph + eli/core/dag). Falls back to a single-pass plan.
    import os as _os_gp
    if str(_os_gp.environ.get("ELI_PROJECT_DAG", "1")).lower() not in ("0", "false", "no", "off"):
        try:
            from eli.coding.agent import CodeAgent
            cr = CodeAgent().solve(f"{desc}{_proj_ev_block}", use_dag=True)
            if cr is not None and (getattr(cr, "code", "") or "").strip():
                plan = getattr(cr, "plan", {}) or {}
                steps = plan.get("steps") or (getattr(cr, "search", {}) or {}).get("order") or []
                import re as _re_gp
                safe = _re_gp.sub(r"[^a-z0-9]+", "_", desc.lower())[:50].strip("_") or "project"
                saved = _save_artifact(cr.code, "projects", safe, "py")
                lines = [
                    f"Project built via the planner DAG "
                    f"({plan.get('approach', 'subtask DAG')}): {len(steps)} step(s), "
                    f"score {getattr(cr, 'score', 0.0):.2f}"
                    f"{' (verified)' if getattr(cr, 'solved', False) else ' (best-effort)'}.",
                ]
                if steps:
                    lines.append("Steps: " + " → ".join(str(s) for s in steps[:12]))
                lines.append(f"Saved → {saved}")
                lines.append("\n```python\n" + (cr.code or "")[:6000] + "\n```")
                msg = "\n".join(lines)
                return {"ok": True, "action": a, "content": msg, "response": msg,
                        "doc_path": saved, "evidence_source": "planner_dag",
                        "result": {"score": getattr(cr, "score", 0.0),
                                   "solved": getattr(cr, "solved", False),
                                   "steps": steps, "plan": plan}}
        except Exception as _gp_e:
            log.debug(f"[GENERATE_PROJECT] planner DAG failed, single-pass fallback: {_gp_e}")
    prompt = f"Generate a complete project plan and starter files for: {desc}{_proj_ev_block}"
    return chat(prompt, skip_router=True)



def _h_generate_tests(a, args):
    try:
        from eli.runtime.test_generator import run_testgen
        limit = int(args.get("limit") or 3)
        res = run_testgen(limit=max(1, min(limit, 10)))
        if not res.get("ok"):
            msg = f"Test generation unavailable: {res.get('reason')}"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        files = res.get("accepted_files") or []
        msg = (f"Generated and sandbox-verified {res.get('accepted', 0)} behavioural "
               f"test(s) (rejected {res.get('rejected', 0)} of {res.get('targets', 0)} "
               f"targets — those failed verification). "
               + ("Accepted: " + ", ".join(files) if files else "Nothing accepted this run.")
               + " Review them in tests/generated/ (manifest: _manifest.json).")
        return {"ok": True, "action": a, "content": msg, "response": msg, "result": res}
    except Exception as e:
        msg = f"GENERATE_TESTS failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}



def _h_examine_code(a, args):
    request = (args or {}).get('request') or (args or {}).get('query') or ''
    try:
        from eli.runtime import code_examiner as _ce
        # Fix provenance: "how did you fix that file/LOC?" — answer from the
        # verified patch log, not a multi-minute Think-mode CHAT stream.
        if _ce.is_fix_recall_request(request):
            report = _ce.format_fix_provenance(request)
            return {'ok': True, 'action': a, 'content': report, 'response': report,
                    'evidence_source': 'code_examiner_fix_provenance'}
        _bg = _maybe_background_code_work(a, args)
        if _bg is not None:
            return _bg
        # Cross-turn recall: "list the errors you found" replays the persisted
        # last audit verbatim instead of re-scanning (or the model improvising).
        if _ce.is_recall_request(request):
            _saved = _ce.get_last_audit()
            if _saved:
                report = _ce.format_saved_audit(_saved)
                return {'ok': True, 'action': a, 'content': report, 'response': report,
                        'evidence_source': 'code_examiner_last_audit'}
            # No saved audit → fall through and run a fresh scan.
        paths = _ce.resolve_targets(request)
        if not paths:
            msg = ("I couldn't resolve any Python files to examine. Name a file "
                   "(e.g. 'examine eli/memory/memory.py') or a module.")
            return {'ok': True, 'action': a, 'content': msg, 'response': msg,
                    'evidence_source': 'code_examiner_no_targets'}
        findings = _ce.examine(paths)
        _ce.save_last_audit(paths, findings)
        # Report-only policy: a broad audit never offers to patch, and cosmetic lint is never
        # auto-fixed. Fixing is offered only for named files with real breakage (syntax, failed import,
        # undefined name), so a vague "run full audit" can't apply botched 7B lint patches to the core.
        _named = _ce._extract_named_paths(request)
        # Anchor the conversational "current file" when the user named one,
        # so a later "prove you actually read it" audits that file.
        if _named:
            try:
                _ce.set_last_file(str(_named[0]))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        _named = bool(_named)
        _fixable = [f for f in findings if _ce.is_real_breakage(f)]
        if _named and _fixable:
            _ce.set_pending_fix(_fixable, paths)
        else:
            _ce.clear_pending_fix()
        report = _ce.format_report(paths, findings, allow_fix=(_named and bool(_fixable)))
        return {'ok': True, 'action': a, 'content': report, 'response': report,
                'evidence_source': 'code_examiner'}
    except Exception as _exc:
        msg = f"Code examination failed: {_exc}"
        return {'ok': False, 'action': a, 'error': str(_exc), 'content': msg, 'response': msg}


def _h_fix_file(a, args):
    _bg = _maybe_background_code_work(a, args)
    if _bg is not None:
        return _bg
    from pathlib import Path as _Path
    import re as _re_ff
    path = str(args.get("path") or "").strip()
    path = _re_ff.sub(r'^file\s+', '', path, flags=_re_ff.IGNORECASE).strip()
    if not path:
        # Bare "please fix the file" — recover the file the user named a turn earlier
        # (e.g. the FIX_FILE/EXAMINE_CODE target) instead of failing outright.
        try:
            from eli.runtime import code_examiner as _ce_lf
            path = str(_ce_lf.get_last_file() or "").strip()
        except Exception:
            path = ""
    if not path:
        msg = ("I don't have a file to fix yet — tell me which one "
               "(e.g. 'fix eli/memory/memory.py').")
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    pp = _resolve_existing_user_or_artifact_path(path)
    if pp.exists():
        try:
            from eli.runtime import code_examiner as _ce_lf
            _ce_lf.set_last_file(str(pp))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    if not pp.exists():
        msg = f"Path not found: {pp}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    # Protected-path guard — same list self_improvement.py's autonomous
    # patcher refuses to touch. Checked before any model work so a
    # request against a guardrail file fails fast.
    try:
        from eli.runtime.self_improvement import is_protected_patch_path
        if is_protected_patch_path(pp):
            msg = (f"Refused: {pp} is a protected safety guardrail and cannot "
                   "be auto-fixed. Edit it by hand if this is intentional.")
            return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    original = pp.read_text(encoding='utf-8', errors='replace')
    extra_error = str(args.get("error") or args.get("stderr") or "").strip()
    ext_to_lang = {
        ".py": "Python", ".sh": "Bash", ".bash": "Bash", ".js": "JavaScript",
        ".ts": "TypeScript", ".rb": "Ruby", ".go": "Go", ".rs": "Rust",
        ".lua": "Lua", ".pl": "Perl", ".java": "Java", ".cpp": "C++",
        ".c": "C", ".cs": "C#", ".php": "PHP", ".swift": "Swift",
        ".kt": "Kotlin", ".r": "R", ".ps1": "PowerShell",
    }
    lang = ext_to_lang.get(pp.suffix.lower(), "the same language")
    err_block = f"\n\nReported error / stderr:\n{extra_error}" if extra_error else ""
    # IMPROVE mode (router: file.improve_python) vs FIX mode: same machinery, different
    # intent — make working code better (clarity/correctness/robustness, dedupe, dead
    # code) vs correct errors. Both go through the verified CodeAgent below.
    _improve = str(args.get("mode") or "").strip().lower() == "improve"
    _verb = "Improve" if _improve else "Fix"
    def _verify_python_apis_ff(_code: str) -> str | None:
        """Same as the GENERATE_SCRIPT path verifier — checks <module>.<attr> against installed libs."""
        import importlib as _il
        _ALIAS_DEFAULT = {
            "nx": "networkx", "np": "numpy", "pd": "pandas",
            "sp": "scipy", "sk": "sklearn", "sym": "sympy",
        }
        _SKIP_HEAVY = {"matplotlib", "matplotlib.pyplot", "torch", "tensorflow", "cv2"}
        _aliases = dict(_ALIAS_DEFAULT)
        for _m in _re_ff.finditer(r"^\s*import\s+([\w.]+)\s+as\s+(\w+)", _code, _re_ff.MULTILINE):
            _aliases[_m.group(2)] = _m.group(1)
        for _m in _re_ff.finditer(r"^\s*import\s+([\w.]+)\s*$", _code, _re_ff.MULTILINE):
            _aliases[_m.group(1).split(".")[0]] = _m.group(1)
        _bad = []
        for _alias, _modname in _aliases.items():
            if _modname in _SKIP_HEAVY:
                continue
            if not _re_ff.search(rf"\b{_re_ff.escape(_alias)}\.[a-zA-Z_]", _code):
                continue
            try:
                _mod = _il.import_module(_modname)
            except Exception:
                continue
            for _m in _re_ff.finditer(rf"\b{_re_ff.escape(_alias)}\.([a-zA-Z_]\w*)", _code):
                _attr = _m.group(1)
                if not hasattr(_mod, _attr):
                    ref = f"{_alias}.{_attr}"
                    if ref not in _bad:
                        _bad.append(ref)
        if _bad:
            return (
                f"code references non-existent module APIs: {', '.join(_bad[:5])}. "
                "Use only attributes that actually exist."
            )
        return None

    base_fix_prompt = (
        f"{'Improve' if _improve else 'Fix'} the following {lang} file. Return ONLY the "
        "complete " + ("improved" if _improve else "corrected") + " source code — "
        "no markdown fences, no prose, no explanation, no diff. The entire output must be "
        "the new file content that can be saved verbatim and run.\n\n"
        "HARD REQUIREMENTS:\n"
        "- Use ONLY real library APIs that actually exist; verify function names\n"
        "- For networkx clique detection, do NOT use nx.is_clique or nx.graph_clique_number "
        "(neither exists). Instead implement via itertools.combinations + explicit edge checks: "
        "  `all(G.has_edge(u, v) for u, v in combinations(subset, 2))`\n"
        "- Replace any non-existent calls with working equivalents from the same library\n"
        "- Preserve the original intent and structure; minimal but complete fixes\n"
        "- Keep all original functionality working\n"
        "- Implement REAL logic — never a toy/oversimplified/faked/hardcoded stand-in for "
        "the real work; if a known algorithm/problem is involved, implement it correctly\n"
        "- No TODO stubs, no placeholders, no commentary lines explaining the fix\n"
        f"- Output must be valid {lang} that parses cleanly\n\n"
        f"File path: {pp}{err_block}\n\n"
        "=== ORIGINAL FILE CONTENT BEGINS ===\n"
        f"{original[:30000]}\n"
        "=== ORIGINAL FILE CONTENT ENDS ===\n\n"
        f"Output the fixed {lang} file content now (raw code only):"
    )

    fixed_code = ""
    ff_reject_reason = None
    # Python: route the fix through the verified CodeAgent first (syntax, pyflakes, execution,
    # repo context, self-critique) so it can't ship broken code like `def main:`. Falls through
    # to the legacy loop for non-Python or if the agent yields nothing usable.
    if pp.suffix.lower() == ".py":
        try:
            from eli.coding import solve as _code_solve
            if _improve:
                _fix_task = (
                    "Improve this Python file and return the COMPLETE improved source. "
                    "Make it clearer, more correct, more robust, and better-structured "
                    "WITHOUT changing its external behaviour or purpose; remove duplication "
                    "and dead code; keep all existing functionality; use only real library "
                    "APIs that exist."
                )
            else:
                _fix_task = (
                    "Fix this Python file and return the COMPLETE corrected source. Preserve "
                    "the original intent and structure; make minimal but complete fixes; use "
                    "only real library APIs that exist."
                )
            _fix_task += (
                (f" Reported error: {extra_error}." if extra_error else "")
                + f"\n\n--- FILE: {pp.name} ---\n{original[:24000]}"
            )
            # Full verified pass: beam search, repair iterations, self-critique. Quality over latency by
            # choice, don't cut beam or iterations to save time.
            # use_dag=False: a single-file fix is one coherent rewrite. The DAG path regenerated the whole
            # file per component and compose() only dedupes imports, which duplicated the file 6x.
            _cr = _code_solve(_fix_task, language="python", use_tests=False,
                              beam=2, max_iterations=3, use_dag=False)
            _cand = str((_cr or {}).get("code") or "").strip()
            if _cand and len(_cand) >= 20 and _cand != original.strip():
                import ast as _ast_v
                try:
                    _ast_v.parse(_cand)          # belt-and-suspenders (agent already gated)
                    fixed_code = _cand
                    log.debug(f"[FIX_FILE] CodeAgent produced verified fix "
                              f"(score {(_cr or {}).get('score')})")
                except SyntaxError:
                    log.debug("suppressed exception", exc_info=True)
        except Exception as _ca_e:
            log.debug(f"[FIX_FILE] CodeAgent path skipped: {_ca_e}")
    for _ff_attempt in range(2):
        if fixed_code:
            break
        _ff_prompt = base_fix_prompt
        if _ff_attempt > 0 and ff_reject_reason:
            _ff_prompt = (
                base_fix_prompt
                + f"\n\nPREVIOUS ATTEMPT WAS REJECTED: {ff_reject_reason}. "
                "Fix this specifically. Use only APIs that actually exist in the installed library."
            )
        _candidate = ""
        try:
            from eli.cognition import gguf_inference as _gguf_ff
            if _gguf_ff.load_model() is not None:
                raw = _gguf_ff.chat_completion(
                    _ff_prompt,
                    system=(
                        f"You are an expert {lang} engineer fixing real code. "
                        "Output ONLY the corrected file content — no fences, no prose. "
                        "Use only library APIs that actually exist."
                    ),
                    max_tokens=8000,
                    temperature=0.15,
                    top_p=0.85,
                )
                if raw:
                    cand = _re_ff.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=_re_ff.MULTILINE)
                    cand = _re_ff.sub(r"\n?```$", "", cand.strip(), flags=_re_ff.MULTILINE).strip()
                    _candidate = cand
        except Exception as _ff_e:
            log.debug(f"[FIX_FILE] GGUF path failed: {_ff_e}, falling back to chat")
        if not _candidate:
            chat_result = chat(_ff_prompt, skip_router=True)
            raw = (chat_result.get("content") or "").strip()
            cand = _re_ff.sub(r"^```[a-z]*\n?", "", raw, flags=_re_ff.MULTILINE)
            cand = _re_ff.sub(r"\n?```$", "", cand.strip(), flags=_re_ff.MULTILINE).strip()
            _candidate = cand
        if not _candidate or len(_candidate) < 20:
            ff_reject_reason = "model returned no usable code"
            continue
        if _candidate.strip() == original.strip():
            ff_reject_reason = "model returned the file unchanged"
            continue
        if pp.suffix.lower() == ".py":
            import ast as _ast_ff
            try:
                _ast_ff.parse(_candidate)
            except SyntaxError as _se:
                ff_reject_reason = f"corrected code has SyntaxError: {_se}"
                continue
            _api_bad = _verify_python_apis_ff(_candidate)
            if _api_bad:
                ff_reject_reason = _api_bad
                log.debug(f"[FIX_FILE] attempt {_ff_attempt + 1} rejected: {_api_bad}")
                continue
        fixed_code = _candidate
        break
    if not fixed_code:
        msg = f"Fix failed after retries: {ff_reject_reason or 'unknown reason'}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    try:
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = pp.with_suffix(pp.suffix + f".bak.{_ts}")
        backup.write_text(original, encoding="utf-8")
    except Exception as _bak_err:
        msg = f"FIX_FILE aborted: could not write backup for {pp}: {_bak_err}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}

    # Differential import baseline — while `pp` still holds the original,
    # check it imports cleanly. Only blame a broken import on THIS fix if
    # it was clean beforehand (same approach self_improvement.py uses).
    _ff_dotted = None
    _ff_pre_ok = False
    if pp.suffix.lower() == ".py":
        try:
            from eli.runtime.self_improvement import _dotted_module_for_path, _smoke_import_module
            _ff_dotted = _dotted_module_for_path(pp)
            if _ff_dotted:
                _ff_pre_ok, _ = _smoke_import_module(_ff_dotted)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    pp.write_text(fixed_code, encoding="utf-8")

    # Behavioural verification — a fix can compile and still break the
    # module at import time. Reuses self_improvement.py's smoke-import +
    # targeted-test + rollback functions rather than a second implementation.
    if pp.suffix.lower() == ".py" and _ff_dotted and _ff_pre_ok:
        try:
            from eli.runtime.self_improvement import _smoke_import_module, _run_targeted_tests
            _imp_ok, _imp_detail = _smoke_import_module(_ff_dotted)
            if not _imp_ok:
                pp.write_text(original, encoding="utf-8")
                msg = f"Fix broke module import (reverted to original): {_imp_detail}"
                return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg,
                        "backup": str(backup)}
            if os.environ.get("ELI_SELFPATCH_VERIFY_TESTS", "1").strip().lower() not in ("0", "false", "no", "off"):
                _t_ran, _t_passed, _t_detail = _run_targeted_tests(pp)
                if _t_ran and not _t_passed:
                    pp.write_text(original, encoding="utf-8")
                    msg = f"Fix broke targeted tests (reverted to original): {_t_detail[:300]}"
                    return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg,
                            "backup": str(backup)}
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    evt = {
        "event": "artifact_generated",
        "kind": "script",
        "path": str(pp),
        "filename": pp.name,
        "language": lang,
        "fixed": True,
        "backup": str(backup) if backup else None,
        "opened": True,
        "can_run": True,
    }
    evt_json = json.dumps(evt, ensure_ascii=False, default=str)
    return {
        "ok": True,
        "action": "FIX_FILE",
        "code": fixed_code,
        "script_path": str(pp),
        "path": str(pp),
        "filename": pp.name,
        "content": evt_json,
        "response": evt_json,
        "open_in_ide": True,
    }



def _h_code_changes(a, args):
    try:
        from eli.runtime.awareness_boot import get_awareness, boot_awareness
        state = get_awareness()
        if state is None:
            state = boot_awareness(quiet=True)
        if state.code_report_has_changes:
            msg = state.code_report_briefing
        else:
            msg = "No code changes detected since last check."
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_file_audit(a, args):
    try:
        import os as _os
        from pathlib import Path as _Path

        # Determine scan root: default to the ELI package directory
        _project_root = _Path(__file__).resolve().parents[3]
        _eli_root = _project_root / "eli"
        _scope = (args or {}).get("scope", "eli")

        if not _eli_root.exists():
            _eli_root = _project_root

        # Walk the directory tree and collect stats
        _dir_stats: dict = {}
        _total_files = 0
        _total_py = 0
        _skipped = {"__pycache__", ".git", ".venv", "venv", "node_modules"}

        for _dirpath, _dirnames, _filenames in _os.walk(str(_eli_root)):
            # Prune skip dirs in-place
            _dirnames[:] = [d for d in _dirnames if d not in _skipped]
            _rel = str(_Path(_dirpath).relative_to(_project_root))
            _py_files = [f for f in _filenames if f.endswith(".py")]
            _non_py = [f for f in _filenames if not f.endswith(".py") and not f.endswith(".pyc")]
            if _filenames:
                _dir_stats[_rel] = {
                    "py": len(_py_files),
                    "other": len(_non_py),
                    "total": len(_filenames),
                }
                _total_files += len(_filenames)
                _total_py += len(_py_files)

        # Build human-readable report
        _lines = [
            f"File Audit: {_eli_root}",
            f"Directories scanned: {len(_dir_stats)}",
            f"Total files: {_total_files}  (Python: {_total_py}  Other: {_total_files - _total_py})",
            "",
            "Directory breakdown (non-empty dirs, sorted):",
        ]
        for _rel_dir in sorted(_dir_stats.keys()):
            _ds = _dir_stats[_rel_dir]
            _lines.append(
                f"  {_rel_dir}/  "
                f"[{_ds['total']} files: {_ds['py']} .py, {_ds['other']} other]"
            )

        # Also list top-level .py module names in eli/ directly
        _top_py = sorted(
            f.name for f in _eli_root.iterdir()
            if f.is_file() and f.suffix == ".py"
        )
        if _top_py:
            _lines.append("")
            _lines.append(f"eli/ root-level modules: {', '.join(_top_py)}")

        _msg = "\n".join(_lines)
        # Fire task_completed to keep world state clean
        try:
            from eli.world.world_event_bus import fire_world_event as _fwe_fa
            _fwe_fa(
                "task_completed",
                "file_audit",
                f"FILE_AUDIT completed: {_total_files} files in {len(_dir_stats)} dirs.",
                {"total_files": _total_files, "total_py": _total_py},
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return {"ok": True, "action": a, "content": _msg, "response": _msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_cancel_code_fix(a, args):
    try:
        from eli.runtime import code_examiner as _ce
        _ce.clear_pending_fix()
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    msg = "Okay — I won't change any code. The examination findings are cleared."
    return {'ok': True, 'action': a, 'content': msg, 'response': msg}


def _h_confirm_code_fix(a, args):
    try:
        from eli.runtime import code_examiner as _ce
        from eli.runtime.self_improvement import get_self_improvement
    except Exception as _imp_exc:
        msg = f"Fix engine unavailable: {_imp_exc}"
        return {'ok': False, 'action': a, 'error': str(_imp_exc), 'content': msg, 'response': msg}

    pending = _ce.get_pending_fix()
    if not pending:
        msg = ("There's no pending examination to fix. Run 'examine <file> for "
               "errors' first.")
        return {'ok': True, 'action': a, 'content': msg, 'response': msg}

    # Tier-3 (logic) findings only get patched if the user explicitly opted in.
    _msg_low = str((args or {}).get('message') or '').lower()
    include_tier3 = bool(re.search(r"\b(logic|tier ?3|all|everything|including)\b", _msg_low))

    findings = pending.get('findings') or []
    confirmed = [f for f in findings
                 if (not f.get('needs_confirmation')) or include_tier3]
    skipped_tier3 = [f for f in findings if f.get('needs_confirmation') and not include_tier3]

    engine = get_self_improvement()
    steps = []
    for f in confirmed:
        target = f.get('file')
        patch = _ce.generate_fix_patch(f)
        if not patch.get('ok'):
            steps.append({'file': target, 'status': 'skipped',
                          'message': patch.get('error', 'no safe patch generated')})
            continue
        applied = engine.apply_code_patch(patch, verify=True)
        status = 'applied' if applied.get('applied') else (
            'reverted' if 'revert' in str(applied.get('message', '')).lower() else 'skipped')
        steps.append({'file': target, 'status': status,
                      'message': applied.get('message', '')})

    _ce.clear_pending_fix()

    lines = ["Code-fix cycle complete."]
    n_applied = len([s for s in steps if s['status'] == 'applied'])
    lines.append(f"- patches_applied: {n_applied}/{len(confirmed)}")
    for s in steps:
        lines.append(f"  - [{s['status']}] {s['file']}: {s['message'][:160]}")
    if skipped_tier3:
        lines.append(f"- left {len(skipped_tier3)} low-confidence (Tier-3) finding(s) "
                     "untouched (say 'fix the logic ones too' to include them).")
    if n_applied:
        lines.append("Each applied patch was syntax-checked and import-verified; "
                     "any that broke its module was auto-reverted.")
    msg = "\n".join(lines)
    return {'ok': True, 'action': a, 'content': msg, 'response': msg,
            'evidence_source': 'code_examiner_fix'}



def _h_run_tests(a, args):
    try:
        import subprocess as _ts_sp, sys as _ts_sys
        from pathlib import Path as _TP
        from eli.core.paths import project_root as _pr
        repo = _pr()  # location-independent (was __file__.parents[2], wrong after the move)
        target = str(args.get("target") or "tests/claims/test_structural_claims.py")
        r = _ts_sp.run([_ts_sys.executable, "tools/run_test_report.py", target],
                       cwd=str(repo), capture_output=True, text=True, timeout=900)
        # run_test_report.py writes to $ELI_ARTIFACTS_DIR when set and falls back
        # to <repo>/artifacts otherwise. Reading only the fallback meant that with
        # the override in play the report was written to one place and looked for
        # in another, so this silently degraded to the stdout tail.
        try:
            from pathlib import Path as _RepPath

            from eli.core.paths import data_dir as _dd
            rep = _RepPath(_dd()) / "test_report.md"
            if not rep.exists():
                rep = repo / "artifacts" / "test_report.md"
        except Exception:
            rep = repo / "artifacts" / "test_report.md"
        body = rep.read_text(encoding="utf-8")[:4000] if rep.exists() else \
            (r.stdout or r.stderr or "")[-2000:]
        return {"ok": True, "action": a, "content": body, "response": body,
                "report_path": str(rep), "evidence_source": "pytest_test_report"}
    except Exception as e:
        msg = f"RUN_TESTS failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_test_review(a, args):
    try:
        from eli.runtime.test_review import run_and_review
        target = str(args.get("target") or "tests/claims/test_structural_claims.py")
        res = run_and_review(target)
        if res.get("error"):
            msg = f"Test review could not run: {res['error']}"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        t = res.get("totals", {})
        lines = [f"Test review of `{target}`: {t.get('passed', 0)} passed, "
                 f"{t.get('failed', 0)} failed, {t.get('errored', 0)} errored, "
                 f"{t.get('xfailed', 0)} known-gap (xfail) of {t.get('total', 0)}."]
        if res.get("failures"):
            lines.append("Failing tests (possible errors):")
            for f in res["failures"][:10]:
                lines.append(f"  - {f['node']}: {(f.get('message') or '')[:120]}")
        fa = res.get("fix_analysis") or {}
        if fa.get("proposals"):
            lines.append("Concrete issues found in failing modules (code examiner):")
            for p in fa["proposals"][:5]:
                for fn in (p.get("findings") or [])[:3]:
                    loc = f":{fn.get('line')}" if fn.get("line") else ""
                    lines.append(f"  - {p['module']}{loc} [{fn.get('kind')}] {fn.get('message')}")
        if res.get("error_file"):
            lines.append(f"Errors written to: {res['error_file']}")
        if res.get("backup_path"):
            lines.append(f"Previous report backed up to: {res['backup_path']}")
        if res.get("options"):
            lines.append("What would you like to do next?")
            for i, o in enumerate(res["options"], 1):
                lines.append(f"  {i}. {o['label']}  — say: \"{o['command']}\"")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "test_review", "result": res}
    except Exception as e:
        msg = f"TEST_REVIEW failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_lora_train(a, args):
    try:
        from eli.learning.lora_pipeline import run_pipeline
        target = str(args.get("target") or "eli_phi")
        do_execute = bool(args.get("execute", False))
        res = run_pipeline(target, execute=do_execute,
                           max_steps=int(args.get("max_steps", 1) or 1))
        detail = "\n".join(
            f"- {s['stage']}: {'ok' if s['ok'] else 'FAILED'} — {s.get('detail')}"
            for s in res.get("stages", []))
        msg = (res.get("summary", "") + "\n" + detail).strip()
        return {"ok": bool(res.get("ok")), "action": a, "content": msg,
                "response": msg, "evidence_source": "lora_pipeline", "result": res}
    except Exception as e:
        msg = f"LORA_TRAIN failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CODE_SOLVE": _h_code_solve,
    "GENERATE_SCRIPT": _h_generate_script,
    "GENERATE_PROJECT": _h_generate_project,
    "GENERATE_TESTS": _h_generate_tests,
    "EXAMINE_CODE": _h_examine_code,
    "FIX_FILE": _h_fix_file,
    "CODE_CHANGES": _h_code_changes,
    "FILE_AUDIT": _h_file_audit,
    "CANCEL_CODE_FIX": _h_cancel_code_fix,
    "CONFIRM_CODE_FIX": _h_confirm_code_fix,
}.items():
    _register(_action, _fn)

_register("RUN_TESTS", _h_run_tests)
_register("TEST_REVIEW", _h_test_review)
_register("LORA_TRAIN", _h_lora_train)

def _maybe_background_code_work(*a, **k):
    from eli.execution.executor_enhanced import _maybe_background_code_work as _f
    return _f(*a, **k)
