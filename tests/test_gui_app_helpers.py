"""GUI launcher (`eli/gui/app.py`) pure helpers.

KV-cache math, runtime auto-tune (the loader's own plan, eli/core/load_plan.py) and config round-trip.
These are the launcher's *logic* — no Qt widgets, no display — so they run in the normal suite even
though they live under eli/gui.
"""
from __future__ import annotations

import pytest

from eli.gui import app


def test_kv_cache_mb_math_without_a_model_file():
    # only when no model file can be read: 2048 ctx * 32 layers * 6000 B / 1 MiB = 375 MB
    from eli.core.hardware_profile import _kv_cache_mb
    assert round(_kv_cache_mb(2048, 32), 1) == 375.0
    # the quantized cache ELI loads is q8_0: 34 bytes for 32 values, against fp16's 64
    assert _kv_cache_mb(2048, 32, quant=True) == _kv_cache_mb(2048, 32) * 34 / 64


# --------------------------------------------------------------------------- #
def _plan(**kw):
    from eli.core import load_plan
    base = dict(model_path="m.gguf", n_ctx=16384, n_gpu_layers=29, n_batch=256, n_threads=6,
                cache_type_k="q8_0", cache_type_v="q8_0", flash_attn=True, split_mode=1, main_gpu=0,
                tensor_split=[10, 19], experts_in_ram=None, blocks=28)
    base.update(kw)
    return load_plan.Plan(**base)


def test_auto_tune_shows_the_plan_the_loader_will_use(tmp_path, monkeypatch):
    from eli.core import load_plan
    model = tmp_path / "fake.gguf"
    model.write_bytes(b"\0" * 4096)
    monkeypatch.setattr(load_plan, "plan", lambda req: _plan())
    params = app._auto_tune(model)
    assert (params["n_ctx"], params["n_gpu_layers"], params["batch_size"], params["n_threads"]) == (16384, 29, 256, 6)
    assert params["cache_type_k"] == "q8_0" and params["max_tokens"] == -1


def test_auto_tune_of_an_unreadable_file_is_the_request_as_given(tmp_path, monkeypatch):
    from eli.core import load_plan
    model = tmp_path / "fake.gguf"
    model.write_bytes(b"\0" * 4096)
    monkeypatch.setattr(load_plan, "plan", lambda req: None)
    params = app._auto_tune(model)
    assert params["n_ctx"] >= 2048 and params["n_threads"] >= 1


# --------------------------------------------------------------------------- #
def test_config_round_trip(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(app, "save_settings", lambda c: store.clear() or store.update(c))
    monkeypatch.setattr(app, "load_settings", lambda: dict(store))
    # _save_config strips legacy keys before persisting.
    app._save_config({"temperature": 0.5, "gpu_layers": 99, "context_size": 4096})
    assert "gpu_layers" not in store and "context_size" not in store
    assert store["temperature"] == 0.5
    assert app._load_config()["temperature"] == 0.5


def test_the_window_keeps_its_console_log_once_its_folders_are_set(monkeypatch, tmp_path):
    """"Write session log to file" did nothing for the window: the `eli` and `eli-gui` scripts enter
    eli.gui.app.main, and only eli.__main__ kept the console."""
    import sys
    import eli.utils.log as L
    order = []

    class _Stop(BaseException):
        pass

    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))   # pins no root
    monkeypatch.setattr("eli.licensing.launch_gate.ensure_license_or_exit",
                        lambda **k: order.append("licence"))
    monkeypatch.setattr(L, "keep_console_log", lambda *a, **k: order.append("console") or None)

    def _bootstrap():
        order.append("bootstrap")
        raise _Stop()

    monkeypatch.setattr("eli.core.init_data.bootstrap_once", _bootstrap)
    with pytest.raises(_Stop):
        app.main()
    assert order == ["licence", "console", "bootstrap"]


def test_a_returning_launch_writes_nothing_and_setup_writes_only_what_it_chose(monkeypatch, tmp_path):
    """The whole settings view was handed back to save_settings: every default and every one-session
    environment override (ELI_N_CTX, ELI_MODEL_PATH...) went into the file as if chosen."""
    import sys
    model = tmp_path / "models" / "a-model.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF")
    saved = []

    class _Stop(BaseException):
        pass

    def _stop(*a, **k):
        raise _Stop()

    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))   # pins no root
    monkeypatch.setattr("eli.licensing.launch_gate.ensure_license_or_exit", lambda **k: None)
    monkeypatch.setattr("eli.utils.log.keep_console_log", lambda *a, **k: None)
    monkeypatch.setattr("eli.core.init_data.bootstrap_once", lambda: None)
    monkeypatch.setattr("eli.core.init_data.store_blocker", lambda: None)
    monkeypatch.setattr("eli.bundles.chooser.target_at_startup", lambda argv: None)
    monkeypatch.setattr("eli.core.gpu_pack_runtime.try_activate_gpu_pack", lambda **k: None)
    monkeypatch.setattr("eli.cognition.gguf_inference.load_request", _stop)
    view = {"model_path": str(model), "first_run_complete": True, "show_startup_model_picker": False,
            "n_ctx": 12288, "theme": "a-default", "ram_budget_percent": 61}
    monkeypatch.setattr(app, "_load_config", lambda: dict(view))
    monkeypatch.setattr(app, "save_settings", lambda c: saved.append(dict(c)))
    monkeypatch.setattr(app, "MODELS_DIR", model.parent)
    tuned = {"n_ctx": 4096, "n_gpu_layers": 0, "n_threads": 2, "batch_size": 64, "max_tokens": -1,
             "temperature": 0.7, "use_mmap": True, "use_mlock": False, "cache_type_k": "f16", "cache_type_v": "f16"}
    monkeypatch.setattr(app, "_auto_tune", lambda p: dict(tuned))
    monkeypatch.setattr(sys, "argv", ["eli-gui"])
    with pytest.raises(_Stop):
        app.main()
    assert saved == [], "a returning launch chose nothing"
    monkeypatch.setattr(sys, "argv", ["eli-gui", "--setup"])
    monkeypatch.setattr(app, "_print_header", lambda: None)
    monkeypatch.setattr(app, "_pick_model", lambda models: model)
    monkeypatch.setattr(app, "_confirm_params", lambda m, p: p)
    with pytest.raises(_Stop):
        app.main()
    assert len(saved) == 1
    assert set(saved[0]) == set(tuned) | {"model_path", "bundled_model_path", "custom_model_path",
                                         "gguf_model_path", "first_run_complete"}
    assert saved[0]["n_ctx"] == 4096 and "theme" not in saved[0] and "ram_budget_percent" not in saved[0]
