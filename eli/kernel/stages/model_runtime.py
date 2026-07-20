"""Model-runtime access seam shared by the engine and the generation stage.

Soft-imports the local inference module (eli.cognition.gguf_inference) and the
inference-broker factory. Both are optional — a headless or test environment may
have neither installed/loadable — so every caller gates on
`gguf_inference is not None` / `_get_inference_broker is not None`.

Bound once at import and never reassigned, so `from ... import gguf_inference` in
the engine and in the generation mixin both resolve to the same module object;
there is no divergence to keep in sync. Kept as its own leaf module so the
generation stage reaches the model without importing back into engine.

If the import fails the ROOT CAUSE is preserved in `gguf_import_error` (and logged
with a traceback), not discarded — a broken GPU backend (llama-cpp built for a
CUDA/ROCm/Metal runtime that isn't loadable) surfaces here as an ImportError, and
the caller needs to know *why* inference is unavailable, not just *that* it is.
"""
from eli.utils.log import get_logger

log = get_logger(__name__)

gguf_import_error = None
try:
    from eli.cognition import gguf_inference
except Exception as _e:  # pragma: no cover - inference stack optional
    gguf_inference = None
    gguf_import_error = f"{type(_e).__name__}: {_e}"
    log.debug("gguf_inference unavailable — inference stack not loadable "
              "(missing llama-cpp, or a GPU backend .so that won't load)", exc_info=True)

try:
    from eli.cognition.inference_broker import get_broker as _get_inference_broker
except ImportError:  # pragma: no cover - broker optional
    _get_inference_broker = None

__all__ = ["gguf_inference", "gguf_import_error", "_get_inference_broker"]
