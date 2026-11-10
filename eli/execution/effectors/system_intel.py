"""System-intel effector: what a GPU reading means, as facts (gpu_use_facts).

The runtime, cognition and memory reports and their formatters are the executor's
(executor_enhanced.py), and only there: this module held word-for-word copies of them and shims
back to them, which nothing called, and the copies drifted from the ones that ran.
"""
from __future__ import annotations

from typing import Any, Dict, List

from eli.utils.log import get_logger

log = get_logger(__name__)


def _mib(value) -> str:
    return f"{float(value):.0f} MiB" if value is not None else "unknown"


def gpu_use_facts(report: Dict[str, Any]) -> Dict[str, Any]:
    """What the GPU reading means, as facts: each card on its own (a "1%" was the busiest of two cards,
    read while ELI was idle), whether ELI was generating when it was read, what the cards did during
    ELI's last generation where that was measured, and how the model is split between the cards and
    the CPU. {"lines": [...], "devices": [...], "generating": bool, "offload": {...}}; the words a
    person reads are the model's (or this list, shown as it is, for a bare "gpu status")."""
    telem = dict((report or {}).get("gpu") or {})
    snap = dict((report or {}).get("runtime_snapshot") or {})
    lines: List[str] = []
    devices = [dict(d) for d in (telem.get("devices") or [])]
    if devices:
        lines.append(f"Each GPU, read now ({len(devices)} card{'s' if len(devices) != 1 else ''}):")
        for i, d in enumerate(devices):
            bits = [f"use {d['util_pct']:.0f}%" if d.get("util_pct") is not None else "use not reported"]
            if d.get("total_mb") is not None:
                bits.append(f"VRAM {_mib(d.get('used_mb'))} of {_mib(d.get('total_mb'))}")
            if d.get("temp_c") is not None:
                bits.append(f"{d['temp_c']:.0f} C")
            if d.get("power_w") is not None:
                bits.append(f"{float(d['power_w']):.0f} W")
            lines.append(f"- GPU {d.get('index', i)} {d.get('name') or ''}: " + ", ".join(bits))

    generating = False
    try:
        from eli.cognition import inference_broker as _broker
        generating = bool(_broker.foreground_in_progress())
        took = float(getattr(_broker, "_last_foreground_duration", 0.0) or 0.0)
    except Exception:
        log.debug("generation state unread", exc_info=True)
        took = 0.0
    lines.append("When read: ELI was " + ("generating a reply." if generating else
                 "not generating anything, so this is the cards at rest, not the cards at work."))
    during = None
    try:
        from eli.core import hardware_profile as _hp
        _record = getattr(_hp, "gpu_use_during_last_generation", None)
        during = _record() if callable(_record) else None
    except Exception:
        log.debug("last generation's GPU use unread", exc_info=True)
    if during and during.get("devices"):
        lines.append(f"During ELI's last generation ({float(during.get('seconds') or took):.0f} s, measured):")
        for d in during["devices"]:
            lines.append(f"- GPU {d.get('index')}: average use {float(d.get('util_avg') or 0):.0f}%, "
                         f"peak {float(d.get('util_max') or 0):.0f}% over {int(d.get('samples') or 0)} readings")
    else:
        lines.append("During ELI's last generation: no reading of the cards was taken"
                     + (f" (it ran {took:.0f} s)." if took else "."))

    offload: Dict[str, Any] = {}
    eff = dict(snap.get("effective") or {})
    on_gpu = eff.get("n_gpu_layers", snap.get("n_gpu_layers"))
    try:
        from eli.core.gguf_sizes import weights
        w = weights(snap.get("model_path")) if snap.get("model_path") else None
    except Exception:
        log.debug("model layers unread", exc_info=True)
        w = None
    if w is not None and on_gpu is not None:
        n = int(on_gpu)
        layers_gpu = min(n, w.blocks)
        experts_in_ram = bool(snap.get("moe_expert_offload"))
        gpu_bytes = w.gpu_bytes(n, experts_in_ram=experts_in_ram)
        all_bytes = sum(w.block_bytes) + sum(w.expert_bytes) + w.output_bytes
        offload = {"layers_on_gpu": layers_gpu, "layers": w.blocks, "layers_on_cpu": w.blocks - layers_gpu,
                   "weights_on_gpu_mb": gpu_bytes / 2 ** 20, "weights_mb": all_bytes / 2 ** 20}
        lines.append(f"The model: {layers_gpu} of its {w.blocks} layers on the GPUs, {w.blocks - layers_gpu} on "
                     f"the CPU; {gpu_bytes / 2 ** 30:.1f} GB of its {all_bytes / 2 ** 30:.1f} GB of weights on the "
                     f"GPUs" + (" (its expert weights stay in system RAM)" if experts_in_ram else "") + ".")
    split = snap.get("tensor_split")
    if isinstance(split, (list, tuple)) and len(split) > 1 and any(float(x or 0) for x in split):
        total = sum(float(x or 0) for x in split)
        share = ", ".join(f"GPU {i} {float(x) / total:.0%}" for i, x in enumerate(split))
        offload["tensor_split"] = list(split)
        lines.append(f"Split between the cards (tensor_split {list(split)}): {share} of the GPU layers.")
    return {"lines": lines, "devices": devices, "generating": generating, "during": during, "offload": offload}


__all__ = ['gpu_use_facts']
