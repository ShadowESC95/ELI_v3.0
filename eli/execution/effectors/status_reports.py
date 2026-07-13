"""Status-reports effector — self-contained diagnostic/status actions."""
from __future__ import annotations

from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_get_status(a, args):
    from eli.execution import executor_enhanced as _e
    return _e.get_status()


def _h_habit_status(a, args):
    try:
        from eli.memory.memory import get_memory as _hm
        _mem = _hm()
        rules = _mem.get_habit_rules(enabled_only=False) if hasattr(_mem, 'get_habit_rules') else []
        detected = _mem.get_detected_habits(min_count=3, limit=8) if hasattr(_mem, 'get_detected_habits') else []
        usage = _mem.get_requested_action_log(min_count=1, limit=10) if hasattr(_mem, 'get_requested_action_log') else []
        if not rules and not detected and not usage:
            msg = "No habits detected yet. Use ELI regularly and habits will emerge from your patterns."
            return {"ok": True, "action": a, "content": msg, "response": msg}
        lines = []
        # Behavioural patterns ELI has actually noticed (the `habits` table). This was
        # previously dark — only the scheduled habit_rules table (empty) was read.
        if detected:
            lines.append(f"Behavioural patterns I've picked up on ({len(detected)}):")
            for d in detected:
                _nm = str(d.get("name", "?")).replace("_", " ").lower()
                lines.append(f"  • {_nm} — seen {int(d.get('count', 0))}×")
        # The raw usage log: actions the user has asked ELI to perform, most-used first.
        # This is the `habits` tally framed as a usage history, distinct from scheduled
        # rules and from the filtered behavioural-habit view above.
        if usage:
            if lines:
                lines.append("")
            lines.append(f"Actions you've asked me to perform ({len(usage)}, most-used first):")
            for u in usage:
                _an = str(u.get("action", "?")).replace("_", " ").lower()
                lines.append(f"  • {_an} — {int(u.get('count', 0))}×")
        if rules:
            lines.append(f"Scheduled habit rules ({len(rules)}):")
        for r in rules:
            if not isinstance(r, dict):
                try: r = dict(r)
                except: continue
            name = r.get("name", "?")
            cmd = r.get("command", "?")
            hour = r.get("hour", 0)
            minute = r.get("minute", 0)
            enabled = r.get("enabled", True)
            status = "enabled" if enabled else "disabled"
            lines.append(f"  [{status}] {name} — runs '{cmd}' at {hour:02d}:{minute:02d}")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg, "rules": rules, "detected": detected, "usage": usage}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_lora_status(a, args):
        try:
            from eli.learning.training_preflight import preflight_all
            pf = preflight_all()
            lines = ["LoRA training readiness:"]
            for rep in pf.get("reports", []):
                probs = rep.get("problems") or []
                lines.append(f"- {rep.get('target')}: "
                             f"{'READY' if rep.get('can_train') else 'NOT READY'}"
                             + (f" — {'; '.join(probs)}" if probs else ""))
            msg = "\n".join(lines)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "evidence_source": "lora_preflight", "result": pf}
        except Exception as e:
            msg = f"LORA_STATUS failed: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    # ---- LORA_TRAIN — run the LoRA pipeline DAG (DRY-RUN by default) ----
    # From chat this is always a dry-run readiness pass (preflight→build→eval); real
    # training (execute=True) only via the scheduled `lora` task / explicit GUI.


def _h_awareness_status(a, args):
        try:
            from eli.runtime.awareness_boot import get_awareness, boot_awareness
            state = get_awareness()
            if state is None:
                state = boot_awareness(quiet=True)
            msg = state.full_briefing()
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "capability_count": state.capability_count}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- FRONTIER_STATUS ----


def _h_frontier_status(a, args):
        try:
            from eli.runtime.frontier_status import (
                build_frontier_status_report,
                format_frontier_status_report,
            )

            report = build_frontier_status_report(str(args.get("question") or args.get("query") or ""))
            text = format_frontier_status_report(report)
            return {
                "ok": bool(report.get("ok", True)),
                "action": a,
                "report": report,
                "content": text,
                "response": text,
                "evidence_source": "frontier_status_local_runtime_matrix_v1",
            }
        except Exception as e:
            msg = f"Frontier status failed: {type(e).__name__}: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    # ---- ELI_IDENTITY_AUDIT ----


def _h_cpu_usage(a, args):
        try:
            import psutil
            pct = psutil.cpu_percent(interval=0.5)
            count = psutil.cpu_count()
            freq = psutil.cpu_freq()
            freq_str = f" @ {freq.current:.0f} MHz" if freq else ""
            # Real CPU temperature (package/Tctl) so "cpu temperature" is grounded,
            # not confabulated. None when the platform exposes no thermal sensor.
            _cpu_temp = None
            try:
                _temps = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
                for _k in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                    _arr = _temps.get(_k)
                    if not _arr:
                        continue
                    _pick = next((e for e in _arr if any(
                        x in (getattr(e, "label", "") or "").lower()
                        for x in ("package", "tctl", "tdie"))), _arr[0])
                    if getattr(_pick, "current", None):
                        _cpu_temp = int(round(_pick.current))
                        break
            except Exception:
                _cpu_temp = None
            _temp_str = f", {_cpu_temp}°C" if _cpu_temp is not None else ""
            msg = f"CPU usage: {pct:.1f}%  ({count} cores{freq_str}{_temp_str})"
            return {"ok": True, "action": a, "percent": pct, "cores": count,
                    "temperature_c": _cpu_temp, "content": msg, "response": msg}
        except ImportError:
            import subprocess as _sp
            try:
                out = _sp.check_output(["top", "-bn1"], text=True, timeout=5)
                cpu_line = next((l for l in out.splitlines() if "Cpu(s)" in l or "%Cpu" in l), "")
                msg = f"CPU: {cpu_line.strip()}" if cpu_line else "CPU info unavailable (install psutil)"
            except Exception:
                msg = "CPU info unavailable (install psutil: pip install psutil)"
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- RAM_USAGE ----


def _h_memory_stats(a, args):
        try:
            from eli.memory import get_memory
            mem = get_memory()
            conn = mem._get_connection()
            try:
                counts = {}
                for table in ["memories", "conversations", "observations", "failures"]:
                    try:
                        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                        counts[table] = row[0] if row else 0
                    except Exception:
                        counts[table] = "N/A"
                msg = "Memory Stats:\n" + "\n".join(f"  {k}: {v}" for k, v in counts.items())
            finally:
                conn.close()
            # Append conversation log stats from the rotation module
            try:
                from eli.perception.log_rotation import convlog_stats
                log_info = convlog_stats()
                log_lines = [
                    f"  log_files: {log_info.get('file_count', 'N/A')}",
                    f"  log_total_mb: {log_info.get('total_mb', 'N/A')}",
                ]
                msg += "\nConversation Logs:\n" + "\n".join(log_lines)
                counts["log_stats"] = log_info
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            return {"ok": True, "action": a, "content": msg, "response": msg, "counts": counts}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


    # ---- Window management (screen control) ────────────────────────────
    # Tile / arrange windows so all are visible side-by-side. Linux-first.


def _h_hardware_profile(a, args):
        try:
            from eli.core.hardware_profile import run_benchmark, apply_recommendation, recommend
            if args.get("apply"):
                rec = recommend()
                result = apply_recommendation(rec)
                msg = f"Applied: {rec.model_name}, {rec.n_gpu_layers} GPU layers, ctx={rec.n_ctx}, batch={rec.batch_size}, threads={rec.n_threads}.\nRestart Eli to use new settings."
                return {"ok": True, "action": a, "content": msg, "response": msg}
            bench = run_benchmark()
            hw = bench["hardware"]
            rec = bench["recommendation"]
            lines = [
                f"Hardware: {hw['cpu_threads']} threads, {hw['ram_gb']}GB RAM, "
                f"{hw['gpu_name'] or 'no GPU'} ({hw['vram_gb']}GB VRAM)",
                "",
                "Recommendation:",
            ]
            for line in rec.get("reasoning", []):
                lines.append(f"  {line}")
            lines.append(f"\n  → {rec['model_name']}, {rec['n_gpu_layers']} GPU layers, ctx={rec['n_ctx']}, batch={rec['batch_size']}")
            lines.append("\nSay 'apply hardware recommendation' to update settings.")
            msg = "\n".join(lines)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "hardware": hw, "recommendation": rec}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- AWARENESS_STATUS ----


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "HABIT_STATUS": _h_habit_status,
    "LORA_STATUS": _h_lora_status,
    "AWARENESS_STATUS": _h_awareness_status,
    "FRONTIER_STATUS": _h_frontier_status,
    "CPU_USAGE": _h_cpu_usage,
    "MEMORY_STATS": _h_memory_stats,
    "HARDWARE_PROFILE": _h_hardware_profile,
    "GET_STATUS": _h_get_status,
}.items():
    _register(_action, _fn)
