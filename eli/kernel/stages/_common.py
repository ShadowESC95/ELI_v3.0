"""Shared primitives for the decomposed engine stages.

Single source for the soft-imported dependencies the engine and its stage mixins
both need, so they agree on one availability flag instead of each re-deriving it
with its own try/except.
"""
from __future__ import annotations

# Response governance is optional (it may not be installed). The engine uses
# normalize_response (GGUF-artifact cleaning); the memory-commit stage uses
# should_store_as_memory. Both gate on the same _HAS_GOVERNANCE flag.
try:
    from eli.cognition.response_governance import (  # noqa: F401
        normalize_response,
        should_store_as_memory,
    )
    _HAS_GOVERNANCE = True
except ImportError:  # pragma: no cover - governance is optional
    normalize_response = None
    should_store_as_memory = None
    _HAS_GOVERNANCE = False

__all__ = ["_HAS_GOVERNANCE", "normalize_response", "should_store_as_memory"]
