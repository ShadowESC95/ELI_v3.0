"""Room on a GPU for a model that sits beside the language model (speech in, the GPU voice).

A support model goes on a card only when the card still holds it after the language model takes
every layer it can: it must never cost the language model layers. What the support model needs is
its footprint measured on that card (weights and working buffers, recorded after its first load), or
its weights until measured; what the language model needs is its load plan (eli/core/load_plan.py),
or, once loaded, what it holds (already gone from the card's free memory). Unknown either way: no
room, so the language model keeps the GPU.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)


def nvidia_cards() -> List[Dict[str, Any]]:
    """NVIDIA cards in bus order, CUDA's numbering (eli/__init__.py sets CUDA_DEVICE_ORDER=PCI_BUS_ID):
    the n-th is cuda:n. [] when there is none or the reader fails."""
    try:
        from eli.core.hardware_profile import get_live_gpu_telemetry
        return [d for d in (get_live_gpu_telemetry().get("devices") or []) if d.get("vendor") == "nvidia"]
    except Exception:
        log.debug("GPU cards unknown", exc_info=True)
        return []


def room_mb(card: Dict[str, Any], position: int = 0) -> Optional[float]:
    """MiB a support model may take on `card` (CUDA device `position`), after the reserve and what
    the language model's plan puts there; None when the language model already wants more than the
    cards hold, or anything needed to tell is unknown."""
    if not card or not card.get("free_mb"):
        return None
    try:
        import sys
        from eli.core.hardware_profile import vram_reserve_mb
        room = float(card["free_mb"]) - float(vram_reserve_mb())
        gi = sys.modules.get("eli.cognition.gguf_inference")
        if gi is None or getattr(gi, "_llm", None) is None:
            # not loaded yet: what its plan puts on this card
            from eli.core import config, load_plan
            from eli.cognition.gguf_inference import load_request
            plan = load_plan.plan(load_request(config.get_gguf_model_path()))
            if plan is None or plan.cpu_only:
                return None
            if plan.n_gpu_layers < plan.blocks + 1 or plan.experts_in_ram:
                return None                       # the language model already wants more than the cards hold
            same = [d.index for d in plan.devices if d.name.lower() == f"cuda{int(position)}"] or \
                [d.index for d in plan.devices if d.description == card.get("name")]
            room -= float(plan.use_mb.get(same[0], 0.0)) if same else max(plan.use_mb.values(), default=0.0)
        return room
    except Exception:
        log.debug("GPU room unknown", exc_info=True)
        return None


def _table(name: str) -> Path:
    from eli.core.paths import data_dir
    return Path(data_dir()) / "runtime" / f"{name}.json"


def _key(card: Dict[str, Any], model: str) -> str:
    return f"{card.get('name')}|{int(card.get('total_mb') or 0)}|{model}"


def measured_footprint_mb(table: str, card: Dict[str, Any], model: str) -> Optional[float]:
    """What `model` was measured holding on this card, from the named table; None until measured."""
    try:
        return float(json.loads(_table(table).read_text(encoding="utf-8"))[_key(card, model)])
    except Exception:
        return None


def record_footprint(table: str, card: Dict[str, Any], model: str, mb: float) -> None:
    path = _table(table)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data[_key(card, model)] = round(float(mb), 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        log.debug("GPU footprint not recorded", exc_info=True)
