"""Unified user-visible response pipeline — single contract for all surfaces.

Collapses the former split between:
  - eli.runtime.user_visible_response_surface.coerce_user_visible
  - eli.runtime.visible_text.to_user_visible_text
  - eli.runtime.personal_memory_* renderers (via coerce_user_visible on their output)

Engine, GUI, and TTS should call `to_user_visible()` at the consumer boundary.
Engine finalization may call `finalize_response_text()` to guarantee clean text
before the result dict is returned.
"""
from __future__ import annotations

from typing import Any

from eli.runtime.user_visible_response_surface import coerce_user_visible


def to_user_visible(
    result: Any,
    *,
    user_input: str = "",
    mode: str = "",
) -> str:
    """Convert any engine/executor result into GUI-safe visible text."""
    return coerce_user_visible(result, user_input=user_input, mode=mode).strip()


def finalize_response_text(
    text: str,
    *,
    user_input: str = "",
    mode: str = "",
    action: str = "",
) -> str:
    """Last-mile coercion for raw response strings before they ship in result dicts."""
    if not text:
        return ""
    raw = str(text)
    # JSON surface packets must be passed as raw strings so coerce_user_visible parses them.
    _probe = raw.strip()
    if _probe.startswith("{") and '"surface":' in _probe[:800]:
        return coerce_user_visible(_probe, user_input=user_input, mode=mode).strip()
    payload: dict[str, Any] = {"response": raw, "content": raw}
    if action:
        payload["action"] = action
    return coerce_user_visible(payload, user_input=user_input, mode=mode).strip()
