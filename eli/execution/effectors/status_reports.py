"""Status-reports effector — self-contained diagnostic/status actions."""
from __future__ import annotations
import os

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
            # without psutil: the load average (Linux, macOS) or Windows' own processor load
            import subprocess as _sp
            from eli.utils import platform_compat as _pc_cpu
            # psutil is a required dependency: a reading that can't be taken is a failure, not an answer
            msg = ""
            try:
                if _pc_cpu.WINDOWS:
                    out = _sp.check_output(["powershell", "-NoProfile", "-Command",
                                            "(Get-CimInstance Win32_Processor | Measure-Object -Property "
                                            "LoadPercentage -Average).Average"], text=True, timeout=15).strip()
                    msg = f"CPU usage: {out}%" if out else ""
                else:
                    one, five, _ = os.getloadavg()
                    msg = f"CPU load average: {one:.2f} (1 min), {five:.2f} (5 min) over {os.cpu_count()} cores"
            except Exception:
                log.debug("CPU load fallback failed", exc_info=True)
            if not msg:
                msg = "CPU info unavailable: psutil, which ELI needs, isn't installed (pip install psutil)."
                return {"ok": False, "action": a, "error": "psutil missing", "content": msg, "response": msg}
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- RAM_USAGE ----


def _h_memory_stats(a, args):
        try:
            # the one count of what the stores hold (eli/memory/memory_truth), where they are: this
            # counted a table nothing writes ("conversations") and looked for the failures in the user's
            # store, where none are kept
            from eli.memory.memory_truth import memory_truth_report
            truth = memory_truth_report()
            user, agent = truth["databases"]["user"], truth["databases"]["agent"]
            counts = {"memories": user["counts"].get("memories", "N/A"),
                      "conversations": user.get("conversations") if user.get("conversations") is not None else "N/A",
                      "conversation_turns": user["counts"].get("conversation_turns", "N/A"),
                      "observations": user["counts"].get("observations", "N/A"),
                      "failures": agent["counts"].get("failures", "N/A")}
            msg = "Memory Stats:\n" + "\n".join(f"  {k}: {v}" for k, v in counts.items())
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
                result = apply_recommendation(rec) or {}
                # what was written, as apply_recommendation says: "Applied" was said whatever came of it,
                # a failed save and a model left as it was included
                if not result.get("ok"):
                    msg = f"The hardware recommendation was not saved: {result.get('error') or 'the settings could not be written'}."
                    return {"ok": False, "action": a, "error": str(result.get("error") or "not saved"),
                            "content": msg, "response": msg, "applied": result}
                tuned = (f"{rec.n_gpu_layers} GPU layers, ctx={rec.n_ctx}, batch={rec.batch_size}"
                         f" (cache {rec.cache_type_k}/{rec.cache_type_v})")
                if result.get("model_written"):
                    msg = f"Applied: {rec.model_name}, {tuned}.\nRestart Eli to use new settings."
                else:
                    msg = (f"Applied: {tuned}. The model was left as it was: the recommendation found no model "
                           f"file to choose.\nRestart Eli to use new settings.")
                return {"ok": True, "action": a, "content": msg, "response": msg, "applied": result}
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


def _h_ram_usage(a, args):
    try:
        import psutil
        from eli.core.mem_units import bytes_to_gib
        vm = psutil.virtual_memory()
        used_gb = bytes_to_gib(vm.used)
        total_gb = bytes_to_gib(vm.total)
        msg = f"RAM usage: {vm.percent:.1f}%  ({used_gb:.1f} GB used / {total_gb:.1f} GB total)"
        return {"ok": True, "action": a, "percent": vm.percent,
                "used_gb": round(used_gb, 2), "total_gb": round(total_gb, 2),
                "content": msg, "response": msg}
    except ImportError:
        msg = "RAM info unavailable: psutil, which ELI needs, isn't installed (pip install psutil)."
        return {"ok": False, "action": a, "error": "psutil missing", "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_system_stats(a, args):
    try:
        import psutil, platform as _platform
        from eli.core.mem_units import bytes_to_gib
        cpu_pct = psutil.cpu_percent(interval=0.5)
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        used_ram = bytes_to_gib(vm.used)
        total_ram = bytes_to_gib(vm.total)
        used_disk = bytes_to_gib(disk.used)
        total_disk = bytes_to_gib(disk.total)
        lines = [
            f"System: {_platform.system()} {_platform.release()}",
            f"CPU:    {cpu_pct:.1f}%  ({psutil.cpu_count()} cores)",
            f"RAM:    {vm.percent:.1f}%  ({used_ram:.1f}/{total_ram:.1f} GB)",
            f"Disk:   {disk.percent:.1f}%  ({used_disk:.1f}/{total_disk:.1f} GB)",
        ]
        try:
            bat = psutil.sensors_battery()
            if bat:
                lines.append(f"Battery: {bat.percent:.0f}%  ({'charging' if bat.power_plugged else 'on battery'})")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "cpu_percent": cpu_pct, "ram_percent": vm.percent, "disk_percent": disk.percent}
    except ImportError:
        msg = "System stats unavailable: psutil, which ELI needs, isn't installed (pip install psutil)."
        return {"ok": False, "action": a, "error": "psutil missing", "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


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

_register("RAM_USAGE", _h_ram_usage)
_register("SYSTEM_STATS", _h_system_stats)
