"""Self-improvement effector — analyze/improve/patch/test/report."""
from __future__ import annotations

import json
import re

from eli.utils.log import get_logger

log = get_logger(__name__)

def _runtime_status_report(*a, **k):
    from eli.execution.executor_enhanced import _runtime_status_report as _f
    return _f(*a, **k)

def self_test(*a, **k):
    from eli.execution.executor_enhanced import self_test as _f
    return _f(*a, **k)


def _h_self_analyze(a, args):
    try:
        from eli.runtime.self_improvement import get_self_improvement
        engine = get_self_improvement()
        failures = engine.analyze_failures(limit=10, days=7, min_cluster_size=1)
        lines = [f"Self-Analysis Report ({len(failures)} recent issues):"]

        def _short(value, limit=220):
            text = " ".join(str(value or "").split())
            return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."

        _rc_llm_budget = [3]  # cap LLM root-cause inferences per report (bounds latency)

        def _llm_root_cause(ui, err):
            # Infer a real cause for failures the hardcoded signatures don't cover —
            # reuses the inference broker (which auto-yields on background threads).
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
        if not failures:
            lines.append("  No recent failures found.")
        if (args or {}).get("suggest"):
            result = engine.analyze_and_improve()
            imps = result.get("improvements", [])
            if imps:
                lines.append(f"\nSuggested improvements ({len(imps)}):")
                for imp in imps[:5]:
                    lines.append(f"  - {imp.get('description', '')}")
        # NOTE (2026-06-09): the live self-model block (awareness.context_block())
        # is PRIVATE prompt context — it was leaking into this user-facing report as a
        # raw "[Live self-model: …]" data dump (user called it out). Do not append it
        # here; the report stays a clean human-readable list.

        msg = "\n".join(lines)
        # Fire repair_completed so the world engine decreases repair_pressure.
        # Without this, repair_pressure accumulates permanently and triggers
        # an infinite proactive SELF_ANALYZE loop.
        try:
            from eli.world.world_event_bus import fire_world_event as _fwe_sa
            _fwe_sa(
                "repair_completed",
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
    try:
        from eli.runtime.self_improvement import get_self_improvement
        engine = get_self_improvement()
        if mode == "propose":
            # Route through the coding agent (decompose→solve→verify), propose-only.
            res = engine.propose_via_agent(max_items=int((args or {}).get("max_items", 3) or 3))
            props = res.get("proposals", [])
            msg_lines = [f"Coding-agent fix proposals (verified, not applied): {len(props)}"]
            for p in props:
                msg_lines.append(
                    f"  - {p.get('failure')}: "
                    f"{'VERIFIED' if p.get('verified') else 'best-effort'} "
                    f"(score {p.get('score')}) — {p.get('approach') or p.get('message')}")
            if not props:
                msg_lines.append(f"  ({res.get('reason') or res.get('error') or 'nothing to propose'})")
            msg_lines.append("Say \"apply self-improvement patch\" to apply fixes (gated).")
            msg = "\n".join(msg_lines)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "evidence_source": "coding_agent", "result": res}
        if mode == "patch":
            result = engine.run_patch_cycle(max_patches=3, dry_run=bool(dry_run))
            details = result.get("details", [])
            changed = [
                d for d in details
                if str(d.get("status", "")).lower() in {"applied", "patched", "changed"}
            ]
            msg_lines = [
                "Patch improvement cycle complete.",
                f"- code_changes_made: {len(changed)}",
                f"- failures_analyzed: {result.get('failures_analyzed', 0)}",
                f"- dry_run: {bool(dry_run)}",
                f"- summary: {result.get('summary', 'Patch cycle complete.')}",
            ]
            if details:
                msg_lines.append("- patch_details:")
                for d in details[:5]:
                    msg_lines.append(
                        f"  [{d.get('status','?')}] "
                        f"{str(d.get('failure',''))[:90]}"
                    )
            msg = "\n".join(msg_lines)
        else:
            failures = engine.analyze_failures(limit=10, days=14, min_cluster_size=1)
            result = engine.analyze_and_improve()
            imps = result.get("improvements", [])
            msg_lines = [
                "Improvement cycle complete.",
                "- code_changes_made: 0",
                f"- failures_inspected: {len(failures)}",
                f"- new_improvement_records: {len(imps)}",
                "- patch_cycle_run: false",
            ]
            if failures:
                last = failures[0]
                err = " ".join(str(last.get("error") or "").split())
                ui = " ".join(str(last.get("user_input") or "").split())
                msg_lines.append(f"- last_failure_error: {err[:220] or '-'}")
                msg_lines.append(f"- last_failure_input: {ui[:160] or '-'}")
            if imps:
                msg_lines.append("- logged_improvements:")
                msg_lines.extend(f"  - {i.get('description', '')}" for i in imps[:5])
            msg_lines.append("To modify code, run/apply self-improvement patches.")
            msg = "\n".join(msg_lines)
        _si_result = {"ok": True, "action": a, "content": msg, "response": msg}
        try:
            from eli.world.world_event_bus import fire_improvement_event as _wfie
            _wfie(proposal_count=len(imps), failure_count=len(failures))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return _si_result
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_self_patch(a, args):
    dry_run = (args or {}).get("dry_run", False)
    max_patches = int((args or {}).get("max_patches", 3))
    try:
        from eli.runtime.self_improvement import get_self_improvement
        result = get_self_improvement().run_patch_cycle(max_patches=max_patches, dry_run=bool(dry_run))
        msg = result.get("summary", "Patch cycle complete.")
        details = result.get("details", [])
        if details:
            msg += "\n" + "\n".join(
                f"  [{d.get('status','?')}] {d.get('failure','')[:70]}"
                for d in details[:5]
            )
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



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
        if _rec_ctx and _eff_ctx > _rec_ctx:
            _concerns.append(
                f"context {_eff_ctx} exceeds the hardware-recommended {_rec_ctx}; "
                f"the oversized KV cache crowds GPU layers out of VRAM"
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
    summary = (
        f"I'm ELI (Enhanced Learning Interface), running {model_name} locally "
        f"on GPU ({runtime_eff.get('n_gpu_layers', '?')} layers offloaded). "
        f"Context window: {runtime_eff.get('n_ctx', '?')} tokens. "
        f"All core systems nominal."
    )
    if _active_user:
        summary += f" You're {_active_user}."
    # content/response are user-facing — use the plain summary, never raw JSON.
    # Structured data stays in report + evidence for grounding/introspection.
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep,
            'evidence': ev, 'content': summary, 'response': summary}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "SELF_ANALYZE": _h_self_analyze,
    "SELF_IMPROVE": _h_self_improve,
    "SELF_PATCH": _h_self_patch,
    "SELF_TEST": _h_self_test,
    "SELF_REPORT": _h_self_report,
}.items():
    _register(_action, _fn)
