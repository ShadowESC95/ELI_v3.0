from __future__ import annotations

from typing import Any

# Re-export unified pipeline — single consumer-boundary contract.
from eli.runtime.response_pipeline import to_user_visible as _pipeline_to_user_visible


def _consume_generator(gen: Any) -> str:
    parts: list[str] = []
    for chunk in gen:
        if chunk is None:
            continue

        if isinstance(chunk, str):
            parts.append(chunk)
            continue

        if isinstance(chunk, dict):
            for key in ("response", "content", "message", "text", "delta", "result", "answer", "output"):
                value = chunk.get(key)
                if value is not None and str(value).strip():
                    parts.append(str(value))
                    break
            continue

        parts.append(str(chunk))

    return "".join(parts).strip()


def to_user_visible_text(result: Any) -> str:
    """
    Convert CognitiveEngine/process outputs into GUI-safe visible text.

    Delegates to the unified response pipeline (response_pipeline.to_user_visible).
    """
    if result is None:
        return ""

    if hasattr(result, "__next__"):
        return _consume_generator(result)

    return _pipeline_to_user_visible(result)
