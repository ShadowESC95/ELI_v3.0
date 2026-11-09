"""Self-improvement effector — analyze/improve/patch/test/report."""
from __future__ import annotations

import json
import re

from eli.utils.log import get_logger

log = get_logger(__name__)

def _runtime_status_report(*a, **k):
    from eli.execution.executor_enhanced import _runtime_status_report as _f
    return _f(*a, **k)

def _self_improvement_log_report(*a, **k):
    from eli.execution.executor_enhanced import _self_improvement_log_report as _f
    return _f(*a, **k)

def self_test(*a, **k):
    from eli.execution.executor_enhanced import self_test as _f
    return _f(*a, **k)


def _h_self_analyze(a, args):
    try:
        from eli.runtime.self_improvement import get_self_improvement
        from eli.runtime.self_maintenance_config import DEFAULT_ANALYSIS_DAYS
        engine = get_self_improvement()
        _window_days = int((args or {}).get("days") or DEFAULT_ANALYSIS_DAYS)
        failures = engine.analyze_failures(limit=10, days=_window_days,
                                           min_cluster_size=1)
        # State the lookback window. Without it "0 recent issues" reads as "nothing is wrong"
        # while SELF_IMPROVE, with a different window, reports failures in the same breath.
        lines = [f"Self-Analysis Report ({len(failures)} open issue(s) "
                 f"in the last {_window_days} day(s)):"]

        def _short(value, limit=220):
            text = " ".join(str(value or "").split())
            return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."

        _rc_llm_budget = [3]  # cap LLM root-cause inferences per report (bounds latency)

        def _llm_root_cause(ui, err):
            # Infer a real cause for failures the hardcoded signatures don't cover —
            # reuses the inference broker (which auto-yields on background threads).
            low = f"{ui}\n{err}".lower()
            if any(x in low for x in (
                "missing app name", "missing topic", "give the voice a name",
                "couldn't tell which voice", "specify app",
            )):
                return None
            if _rc_llm_budget[0] <= 0:
                return None
            try:
                from eli.cognition.inference_broker import get_broker
                from eli.cognition.gguf_inference import force_no_think
                broker = get_broker()
            except Exception:
                return None
            _rc_llm_budget[0] -= 1
            prompt = ("State the single most likely ROOT CAUSE of this failure in one "
                      "technical sentence — no preamble, no fix, just the cause.\n\n"
                      "Request: " + (ui or "")[:300] + "\nError: " + (err or "")[:700])
            try:
                with force_no_think():
                    out = broker.infer(prompt, max_tokens=90, temperature=0.1)
                out = " ".join(str(out or "").split()).strip()
                return out[:300] or None
            except Exception:
                return None

        def _descriptive_cause(err):
            # Non-LLM fallback that still DIFFERS per failure (was an identical string).
            err = (err or "").strip()
            first = err.splitlines()[0][:160] if err else ""
            m = re.search(r"\b([A-Za-z_]+Error|Exception)\b", err)
            if m and first and m.group(1) not in first[:40]:
                return f"{m.group(1)} — {first}"
            return first or (m.group(1) if m else "No deterministic signature; needs manual review.")

        def _root_cause_for(failure):
            ui = str(failure.get("user_input") or "")
            err = str(failure.get("error") or "")
            low = f"{ui}\n{err}".lower()
            if err.startswith(("anomaly (", "background job failed")):
                return err      # what the pipeline recorded, not a guess at it
            if "missing topic for document generation" in low:
                return (
                    "CREATE_DOCUMENT was called without topic/name/target — "
                    "fixed in current builds via alias fallbacks."
                )
            if "missing app name" in low:
                return (
                    "CLOSE_APP ran with empty args; the app name must be supplied "
                    "by the user or router."
                )
            if "give the voice a name" in low or "couldn't tell which voice" in low:
                return _descriptive_cause(err)
            if "no such file or directory" in low and re.search(r"['\"]engine['\"]", low):
                return (
                    "SHELL_EXEC tried to run a command not on PATH — likely a user "
                    "typo, not an ELI code defect."
                )
            if "path not found" in low and "artifacts/scripts" in low:
                return (
                    "Path resolution selected a project-root/basename target before the "
                    "artifact path. The requested file existed under artifacts/scripts, "
                    "so the first repair request hit the wrong location."
                )
            if "path not found" in low and "create_a_python_script_to_generate" in low:
                return (
                    "The repair request targeted the project root copy of the generated "
                    "script, but generated scripts are stored under artifacts/scripts. "
                    "The executor needed artifact-aware path recovery before reporting failure."
                )
            if "path not found" in low:
                return (
                    "File operation failed before repair because the resolved target path "
                    "did not exist. The executor needs artifact-aware path recovery before "
                    "declaring a file missing."
                )
            if "cannot create children for a parent that is in a different thread" in low:
                return (
                    "Qt widget/document state was touched from a worker thread. GUI updates "
                    "must be marshalled back to the main Qt thread via signals."
                )
            if "segmentation fault" in low and "qtextdocument" in low:
                return (
                    "The crash signature is consistent with the Qt thread-affinity violation "
                    "above, not a normal Python exception."
                )
            if "unsupported executor action" in low:
                return (
                    "The router produced an action that the executor did not implement, "
                    "so the engine fell back into chat/synthesis instead of returning grounded output."
                )
            # No hardcoded signature — infer the cause (LLM, capped) or describe it.
            return _llm_root_cause(ui, err) or _descriptive_cause(err)

        for f in failures[:10]:
            lines.append(
                f"- input: {_short(f.get('user_input'), 120)}\n"
                f"  error: {_short(f.get('error'), 220)}\n"
                f"  occurrences: {int(f.get('occurrence_count') or 1)}\n"
                f"  actual_root_cause: {_root_cause_for(f)}"
            )
        _older = {}
        try:
            _older = engine.failures_outside_window(days=_window_days) or {}
        except Exception as _ow_err:
            log.debug(f"[SELF_ANALYZE] older-failure count unavailable: {_ow_err}")
        _older_n = int(_older.get("count") or 0)

        if not failures:
            if _older_n:
                lines.append(
                    f"  Nothing in the last {_window_days} day(s) — but "
                    f"{_older_n} open failure(s) are older than that "
                    f"(oldest {_older.get('oldest_days')} days). "
                    f"Ask for a longer window to see them, e.g. "
                    f"\"analyse my failures over 30 days\"."
                )
            else:
                lines.append(
                    f"  No open failures in the last {_window_days} day(s), "
                    f"and none older either.")
        elif _older_n:
            lines.append(
                f"  ({_older_n} further open failure(s) predate this window, "
                f"oldest {_older.get('oldest_days')} days.)")
        if (args or {}).get("suggest"):
            result = engine.analyze_and_improve()
            imps = result.get("improvements", [])
            if imps:
                lines.append(f"\nSuggested improvements ({len(imps)}):")
                for imp in imps[:5]:
                    lines.append(f"  - {imp.get('description', '')}")
        # The live self-model block (awareness.context_block()) is private prompt context; it
        # leaked into this report as a raw "[Live self-model: ...]" dump. Don't append it here;
        # the report stays a clean readable list.

        msg = "\n".join(lines)
        # A review is not a repair, but without easing repair_pressure it accumulates and
        # triggers an endless proactive SELF_ANALYZE loop.
        try:
            from eli.world.world_event_bus import fire_world_event as _fwe_sa
            _fwe_sa(
                "review_completed",
                "self_analyze",
                f"SELF_ANALYZE completed: reviewed {len(failures)} failure(s).",
                {"failures_reviewed": len(failures)},
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_self_improve(a, args):
    mode = (args or {}).get("mode", "analyze")
    dry_run = (args or {}).get("dry_run", False)
    _raw_si = str((args or {}).get("_raw_user_text") or "").lower()
    if mode == "analyze" and any(k in _raw_si for k in (
            "propose", "verified fix", "fix the fail", "coding agent", "fix the failing")):
        mode = "propose"
    if mode == "analyze" and any(k in _raw_si for k in ("self fix", "apply patch", "patch cycle")):
        mode = "patch"
    if mode == "patch":
        # the patch cycle proposes and waits for the owner's yes, as "fix your own code" does
        return _h_self_patch(a, args)
    if mode == "propose":
        # The coding agent is tens of sequential model calls; inline it froze the GUI for hours.
        from eli.execution.effectors.coding import _maybe_background_code_work
        _bg = _maybe_background_code_work(a, dict(args or {}, mode="propose"))
        if _bg is not None:
            return _bg
    try:
        from eli.runtime.self_maintenance import (
            fire_maintenance_world_event,
            run_maintenance_cycle,
        )
        from eli.runtime.self_maintenance_config import DEFAULT_ANALYSIS_DAYS
        cycle_kwargs = dict(
            mode=str(mode),
            days=int((args or {}).get("days")) if (args or {}).get("days") else DEFAULT_ANALYSIS_DAYS,
            max_patches=int((args or {}).get("max_patches", 3) or 3),
            max_proposals=int((args or {}).get("max_items", 3) or 3),
            dry_run=bool(dry_run),
        )
        cycle = run_maintenance_cycle(**cycle_kwargs)
        msg = str(cycle.get("content") or cycle.get("summary") or "Improvement cycle complete.")
        fire_maintenance_world_event(cycle)
        _si_result = {
            "ok": True,
            "action": a,
            "effect": cycle.get("effect"),
            "content": msg,
            "response": msg,
            "result": cycle,
        }
        if mode == "propose":
            _si_result["evidence_source"] = "coding_agent"
        return _si_result
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_self_patch(a, args):
    """"Fix your own code": the patch cycle proposes. Each fix it makes is kept whole as a candidate
    and listed with its change; nothing is applied until the owner says yes, and the yes is to those
    very candidates (APPROVE_CANDIDATE, which checks each on a copy of the tree and applies it through
    the one gated applier). It ran the cycle with writes on, unconfirmed."""
    max_patches = int((args or {}).get("max_patches", 3))
    try:
        from eli.runtime.self_improvement import candidate_lines, get_self_improvement
        from eli.runtime.self_maintenance import (
            fire_maintenance_world_event,
            run_maintenance_cycle,
        )
        from eli.runtime.self_maintenance_config import DEFAULT_ANALYSIS_DAYS
        cycle = run_maintenance_cycle(
            mode="patch",
            max_patches=max_patches,
            dry_run=True,
            days=int((args or {}).get("days")) if (args or {}).get("days") else DEFAULT_ANALYSIS_DAYS,
        )
        fire_maintenance_world_event(cycle)
        ids = [int(d["candidate_id"]) for d in ((cycle.get("patch_cycle") or {}).get("details") or [])
               if d.get("candidate_id")]
        shown = [c for c in get_self_improvement().waiting_candidates() if c["id"] in ids]
        lines = [str(cycle.get("summary") or "Patch cycle complete.")]
        if shown:
            lines.append(f"Proposed, not applied: {len(shown)} fix candidate(s).")
            lines.extend(candidate_lines(shown))
            lines.append("A yes applies them, each checked on a copy of the tree first; "
                         "'decline candidate N' discards one.")
            from eli.runtime.pending_proposal import set_pending_proposal
            set_pending_proposal("approve candidates " + " ".join(str(c["id"]) for c in shown),
                                 summary="apply the proposed fixes")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "effect": bool(shown), "content": msg, "response": msg,
                "result": cycle, "candidates": [c["id"] for c in shown]}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _candidate_ids(args) -> list:
    raw = (args or {}).get("candidate_ids")
    if raw is None and (args or {}).get("candidate_id") is not None:
        raw = [(args or {}).get("candidate_id")]
    out = []
    for value in (raw if isinstance(raw, (list, tuple)) else [raw]) if raw is not None else []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _h_approve_candidate(a, args):
    """The owner's yes or no to fix candidates (self_improvement.waiting_candidates): a yes checks each
    on a copy of the tree and applies it through the one gated applier (approve_candidate); a no keeps
    it in the archive, declined. Named by number, or the one waiting; with several waiting and none
    named, ELI asks which."""
    from eli.runtime.self_improvement import candidate_lines, get_self_improvement
    si = get_self_improvement()
    declining = str((args or {}).get("decision") or "").lower() in ("decline", "reject", "discard", "no")
    ids = _candidate_ids(args)
    if not ids:
        waiting = si.waiting_candidates()
        if not waiting:
            msg = "No fix candidate is waiting for a yes or no."
            return {"ok": False, "action": a, "error": "nothing_waiting", "content": msg, "response": msg}
        if len(waiting) > 1:
            verb = "decline" if declining else "approve"
            msg = "These fix candidates are waiting; which one?\n" + "\n".join(candidate_lines(waiting))
            return {"ok": True, "action": a, "asked": True, "content": msg, "response": msg,
                    "awaiting": {"command": f"{verb} candidate {{answer}}", "action": a, "needs": "candidate"}}
        ids = [int(waiting[0]["id"])]
    results = []
    for cid in ids:
        res = si.decline_candidate(cid) if declining else si.approve_candidate(cid)
        results.append({"candidate_id": cid, **res})
    try:
        from eli.runtime.pending_proposal import clear_pending_proposal
        clear_pending_proposal()
    except Exception:
        log.debug("pending candidates not cleared", exc_info=True)
    done = [bool(r.get("declined") if declining else r.get("applied")) for r in results]
    msg = "\n".join(f"#{r['candidate_id']}: {r.get('message') or ('applied' if ok else 'not applied')}"
                    for r, ok in zip(results, done))
    return {"ok": all(done), "action": a, "results": results, "content": msg, "response": msg}



def _h_self_test(a, args):
    return self_test()



def _h_self_report(a, args):
    rep = _runtime_status_report()
    runtime_eff = rep.get('runtime') or {}
    settings_req = rep.get('settings') or {}
    # Active user's confirmed name belongs in identity evidence — without it
    # "who are you / who am I" answers say "name not provided" and then
    # confabulate a low-confidence story. This is the authoritative profile
    # name (same source USER_IDENTITY_SUMMARY uses).
    try:
        from eli.kernel.state import get_user_name as _gun_sr
        _active_user = str(_gun_sr("") or "").strip()
    except Exception:
        _active_user = ""
    ev = {
        "identity": {
            "name": "ELI",
            "expanded_name": "Enhanced Learning Interface",
            "active_user_name": _active_user or "unknown",
            "grounding_sources": [
                "persona",
                "memory",
                "runtime_state",
                "local_files",
                "loaded_model",
            ],
        },
        "runtime": {
            "model_path": runtime_eff.get('model_path') or rep.get('model_path', 'unknown'),
            "effective_context_size": runtime_eff.get('n_ctx', 'unknown'),
            "effective_gpu_layers": runtime_eff.get('n_gpu_layers', 'unknown'),
            "effective_batch_size": runtime_eff.get('n_batch', 'unknown'),
            "effective_cpu_threads": runtime_eff.get('n_threads', 'unknown'),
        },
        "requested_settings": {
            "n_ctx": settings_req.get('n_ctx', 'unknown'),
            "n_gpu_layers": settings_req.get('n_gpu_layers', 'unknown'),
            "n_threads": settings_req.get('n_threads', 'unknown'),
            "batch_size": settings_req.get('batch_size', 'unknown'),
        },
    }
    # Runtime HEALTH — surface recommended-vs-effective and an explicit
    # utilization assessment. Without this the evidence only shows
    # "requested == effective" (trivially consistent), so the model concludes
    # "no discrepancy" even when the GPU is barely used or ctx is oversized.
    # Giving it the recommendation + concerns lets it flag the problem instead
    # of confidently declaring all-clear (it has no grounding to invent one).
    try:
        _eff_layers = int(runtime_eff.get('n_gpu_layers') or 0)
        _eff_ctx = int(runtime_eff.get('n_ctx') or 0)
        _rec_ctx = int(settings_req.get('hw_profile_n_ctx') or 0)
        _rec_layers = int(settings_req.get('hw_profile_n_gpu_layers') or 0)
        _concerns = []
        # A larger context costs GPU layers only when fewer are on the card than the profile
        # found room for. It was stated whenever the context was larger, layers lost or not:
        # live, with 41 layers offloaded against 40 recommended, ELI reported that its
        # "oversized KV cache crowds GPU layers out of VRAM".
        if _rec_ctx and _eff_ctx > _rec_ctx and _rec_layers and _eff_layers < _rec_layers:
            _concerns.append(
                f"context {_eff_ctx} is above the hardware profile's {_rec_ctx} and {_eff_layers} of the "
                f"{_rec_layers} layers it found room for are on the GPU"
            )
        if _eff_layers <= 1:
            _concerns.append(
                f"only {_eff_layers} GPU layer(s) offloaded — inference is running "
                f"almost entirely on CPU, which is slow"
            )
        ev["runtime_health"] = {
            "recommended_n_ctx": _rec_ctx or "unknown",
            "recommended_n_gpu_layers": _rec_layers or "unknown",
            "concerns": _concerns or ["none — effective config is within recommended bounds"],
        }
    except Exception:
        log.debug("runtime_health assessment failed", exc_info=True)
    txt = json.dumps(ev, ensure_ascii=False, indent=2)
    model_name = (runtime_eff.get('model_path') or rep.get('model_path', '')).split('/')[-1]
    from eli.runtime.live_reads import measured_health as _measured_health
    summary = (
        f"I'm ELI (Enhanced Learning Interface), running {model_name} locally "
        f"on GPU ({runtime_eff.get('n_gpu_layers', '?')} layers offloaded). "
        f"Context window: {runtime_eff.get('n_ctx', '?')} tokens. "
        + _measured_health(ev)
    )
    if _active_user:
        summary += f" You're {_active_user}."
    # how its actions have fared here lately, from the record, the ones with failures first
    try:
        from eli.runtime.action_outcomes import report as _outcomes_report
        _rep = _outcomes_report(limit=3)
        if any(r["n"] > r["ok"] for r in _rep["actions"]):
            summary += " " + " ".join(_rep["lines"][:3])
        ev["actions"] = _rep["actions"]
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
        log.debug("action outcomes unavailable to the self report", exc_info=True)
    # content/response are user-facing — use the plain summary, never raw JSON.
    # Structured data stays in report + evidence for grounding/introspection.
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep,
            'evidence': ev, 'content': summary, 'response': summary}


def _h_self_upgrade(a, args):
    request = (args or {}).get('request', '')
    try:
        from eli.kernel.self_upgrade import SelfUpgrader
        inst = SelfUpgrader()
        _req_low = str(request or "").strip().lower()
        if _req_low in ("generate_patch", "generate patch") or re.search(
            r"\b(generate|create)\s+patch\b", _req_low
        ):
            result = inst.generate_patch(request)
        else:
            result = inst.upgrade(request)
        # If the upgrade request also named files / asked to check/examine/
        # review, run the code examiner and append a tiered report + fix offer.
        try:
            from eli.runtime import code_examiner as _ce
            _low = str(request or "").lower()
            _wants_examine = bool(
                _ce._extract_named_paths(request)
                or re.search(r"\b(examine|review|inspect|check|scan|audit)\b", _low)
            )
            if _wants_examine:
                _paths = _ce.resolve_targets(request)
                if _paths:
                    _findings = _ce.examine(_paths)
                    # Same report-only policy: only offer/pend a fix for genuine breakage on
                    # explicitly-named files; a broad upgrade-audit is report-only.
                    _named = bool(_ce._extract_named_paths(request))
                    _fixable = [f for f in _findings if _ce.is_real_breakage(f)]
                    if _named and _fixable:
                        _ce.set_pending_fix(_fixable, _paths)
                    else:
                        _ce.clear_pending_fix()
                    result = result + "\n\n— Code examination —\n" + \
                        _ce.format_report(_paths, _findings,
                                          allow_fix=(_named and bool(_fixable)))
        except Exception as _ex_exc:
            log.debug(f"[SELF_UPGRADE] examine stage skipped: {_ex_exc}")
        # Report the REAL outcome. This used to be an unconditional ok=True,
        # so an upgrade that installed nothing still came back as a success.
        _state = getattr(inst, 'upgrade_state', 'failed')
        return {'ok': _state in ('upgraded', 'current'), 'action': a,
                'upgrade_state': _state, 'upgraded': bool(getattr(inst, 'upgraded', False)),
                'content': result, 'response': result}
    except Exception as _exc:
        return {'ok': False, 'action': a, 'error': str(_exc), 'content': str(_exc), 'response': str(_exc)}


def _h_self_improvement_log(a, args):
    limit = int((args or {}).get('limit') or 5) if isinstance(args, dict) else 5
    days = int((args or {}).get('days') or 30) if isinstance(args, dict) else 30
    rep = _self_improvement_log_report(limit=limit, days=days)
    return {'ok': bool(rep.get('ok', False)), 'action': a, 'report': rep, 'content': rep.get('content', ''), 'response': rep.get('response', rep.get('content', ''))}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "SELF_ANALYZE": _h_self_analyze,
    "SELF_IMPROVE": _h_self_improve,
    "SELF_PATCH": _h_self_patch,
    "SELF_TEST": _h_self_test,
    "SELF_REPORT": _h_self_report,
}.items():
    _register(_action, _fn)

_register("SELF_UPGRADE", _h_self_upgrade)
_register("SELF_IMPROVEMENT_LOG", _h_self_improvement_log)
_register("APPROVE_CANDIDATE", _h_approve_candidate)
