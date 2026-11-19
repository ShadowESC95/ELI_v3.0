from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from eli.utils.jsonio import read_json_dict as _read_json

_ATTRS = (
    "reasoning_mode",
    "active_reasoning_mode",
    "current_reasoning_mode",
    "selected_reasoning_mode",
    "_reasoning_mode",
    "_active_reasoning_mode",
    "_current_reasoning_mode",
    "_selected_reasoning_mode",
    "_last_reasoning_mode",
    "_trace_reasoning_mode",
)

_SETTING_KEYS = (
    "reasoning_mode",
    "active_reasoning_mode",
    "current_reasoning_mode",
    "default_reasoning_mode",
)

def _norm(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("⚡", "").replace("🔗", "").replace("🔄", "").replace("🌳", "").replace("⚖️", "").strip()
    low = raw.lower().replace("-", "_").replace(" ", "_")

    if "tree" in low or low in {"tot"}:
        return "tree_of_thoughts"
    if "constitutional" in low or "const" in low:
        return "constitutional_ai"
    if "self" in low and "consistency" in low:
        return "self_consistency"
    if "chain" in low or low in {"cot", "co_t"}:
        return "chain_of_thought"
    if "quick" in low or "fast" in low:
        return "quick"

    return low

def _label(key: str) -> str:
    """The public label (Quick/Normal/Advanced/Research/Expert): reasoning_modes.mode_display's."""
    from eli.cognition.reasoning_modes import mode_display
    return str(mode_display(_norm(key) or "quick"))

def _from_settings() -> str:
    from eli.core.paths import project_root
    data = _read_json(Path(project_root()) / "config" / "settings.json")
    for key in _SETTING_KEYS:
        n = _norm(data.get(key))
        if n:
            return n
    modes = data.get("reasoning_modes")
    if isinstance(modes, dict):
        n = _norm(modes.get("active") or modes.get("default"))
        if n:
            return n
    return ""

def _from_engine(engine: Any) -> str:
    if engine is None:
        return ""

    for attr in _ATTRS:
        try:
            n = _norm(getattr(engine, attr, None))
        except Exception:
            n = ""
        if n:
            return n

    try:
        d = vars(engine)
    except Exception:
        d = {}

    for k, v in d.items():
        lk = str(k).lower()
        if "reason" in lk and "mode" in lk:
            n = _norm(v)
            if n:
                return n

    return ""

def current_reasoning_mode(engine: Any = None, override: Any = None) -> str:
    n = _norm(override)
    if n:
        return n

    n = _from_engine(engine)
    if n:
        return n

    for env_key in ("ELI_REASONING_MODE", "ELI_ACTIVE_REASONING_MODE", "ELI_CURRENT_REASONING_MODE"):
        n = _norm(os.environ.get(env_key))
        if n:
            return n

    n = _from_settings()
    if n:
        return n

    return "quick"

def current_reasoning_mode_label(engine: Any = None, override: Any = None) -> str:
    return _label(current_reasoning_mode(engine, override=override))

def current_reasoning_mode_text(engine: Any = None, override: Any = None, explain: bool = True) -> str:
    # Reasoning-mode text description (ELI_REASONING_MODE_TEXT_DESCRIPTION_V1): pull each mode's
    # description from cognition/reasoning_modes.mode_description() so the four private modes get
    # distinct, code-grounded explanations and not one shared paragraph.
    key = current_reasoning_mode(engine, override=override)
    label = _label(key)
    if not explain:
        return f"Current reasoning mode: {label}"
    try:
        from eli.cognition.reasoning_modes import mode_description
        desc = mode_description(key)
    except Exception:
        desc = ""
    if desc:
        return f"Current reasoning mode: {label}\n\n{desc}"
    return f"Current reasoning mode: {label}"

__all__ = [
    "current_reasoning_mode",
    "current_reasoning_mode_label",
    "current_reasoning_mode_text",
]
