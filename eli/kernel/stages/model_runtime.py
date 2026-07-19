"""Model-runtime access seam shared by the engine and the generation stage.

Soft-imports the local inference module (eli.cognition.gguf_inference) and the
inference-broker factory. Both are optional — a headless or test environment may
have neither installed/loadable — so every caller gates on
`gguf_inference is not None` / `_get_inference_broker is not None`.

Bound once at import and never reassigned, so `from ... import gguf_inference` in
the engine and in the generation mixin both resolve to the same module object;
there is no divergence to keep in sync. Kept as its own leaf module so the
generation stage reaches the model without importing back into engine.
"""
try:
    from eli.cognition import gguf_inference
except Exception:  # pragma: no cover - inference stack optional
    gguf_inference = None

try:
    from eli.cognition.inference_broker import get_broker as _get_inference_broker
except ImportError:  # pragma: no cover - broker optional
    _get_inference_broker = None

__all__ = ["gguf_inference", "_get_inference_broker"]
