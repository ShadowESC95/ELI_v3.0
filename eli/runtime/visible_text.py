from __future__ import annotations

from typing import Any

# Re-export unified pipeline — single consumer-boundary contract.
from eli.runtime.response_pipeline import to_user_visible as _pipeline_to_user_visible


def to_user_visible_text(result: Any) -> str:
    """
    Convert CognitiveEngine/process outputs into GUI-safe visible text.

    Delegates to the unified response pipeline (response_pipeline.to_user_visible).
    """
    if result is None:
        return ""

    if hasattr(result, "__next__"):
        # one stream consumer for every surface: it also drops the end-of-reply marker
        from eli.runtime.user_visible_response_surface import _consume_generator
        return _consume_generator(result)

    return _pipeline_to_user_visible(result)
