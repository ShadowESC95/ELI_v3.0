"""The engine's own model init used a name its module never defined.

When the engine was split into stages, _init_gguf moved into the lifecycle stage but kept reading a
module-level `gguf_inference` that only engine.py had. Every call raised NameError inside its own
try, so the engine reported "no model" whatever had loaded. Every test built the engine with
auto_init_gguf=False, so none ran it.
"""
import types

import eli.kernel.stages.lifecycle as lifecycle
from eli.kernel.engine import CognitiveEngine


def _engine(monkeypatch, fake):
    monkeypatch.setattr(lifecycle, "gguf_inference", fake)
    monkeypatch.setattr(CognitiveEngine, "_runtime_n_ctx", lambda self: 8192)
    return CognitiveEngine(auto_init_gguf=False)


def test_the_engine_sees_a_model_that_loaded(monkeypatch, tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    eng = _engine(monkeypatch, types.SimpleNamespace(
        get_model_path=lambda: model, load_model=lambda: object(), get_last_error=lambda: ""))
    eng._init_gguf()
    assert eng._gguf_available is True and eng._gguf_load_error is None
    assert eng._model_path == str(model) and eng._ctx == 8192        # what loaded, not a default


def test_a_load_that_failed_is_reported_with_its_reason(monkeypatch, tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    eng = _engine(monkeypatch, types.SimpleNamespace(
        get_model_path=lambda: model, load_model=lambda: None,
        get_last_error=lambda: "not enough memory for 40 layers"))
    eng._init_gguf()
    assert eng._gguf_available is False
    assert eng._gguf_load_error == "not enough memory for 40 layers"
