import types

from eli.core.hardware_profile import detect_hardware, recommend


def test_detect_hardware_counts_every_nvidia_card(quiet_gpu_sources, monkeypatch):
    hp = quiet_gpu_sources
    monkeypatch.setattr(hp, "nvidia_smi_path", lambda: "/usr/bin/nvidia-smi")
    rows = ("NVIDIA GeForce RTX 3090, 24576, 1000, 23576, 0, 40, 30.0, 350.0, 595.99\n"
            "NVIDIA GeForce RTX 3060, 12288, 500, 11788, 0, 38, 20.0, 170.0, 595.99\n")
    monkeypatch.setattr(hp.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=rows))
    hw = detect_hardware(force=True)
    assert hw.has_gpu and hw.gpu_vendor == "nvidia"
    assert hw.gpu_name == "NVIDIA GeForce RTX 3090 + NVIDIA GeForce RTX 3060"
    assert (hw.total_vram_mb, hw.free_vram_mb) == (36864, 35364)
    assert [g["free_mb"] for g in hw.gpus] == [23576, 11788]

def test_detect_hardware_no_gpu(quiet_gpu_sources):
    # Simulate a genuinely GPU-less machine: no nvidia-smi AND no loaded kernel
    # driver (the driver-loaded fallback must also see nothing). Otherwise this
    # test detects the real GPU on a developer's NVIDIA box via /proc//sys.
    hw = quiet_gpu_sources.detect_hardware(force=True)
    assert hw.has_gpu is False

def test_recommend_no_models():
    rec = recommend(detect_hardware(), [])
    # When no models, model_path is empty string, not None
    assert rec.model_path == ""

def test_recommend_with_model():
    models = [{"name": "test.gguf", "path": "/fake/test.gguf", "size_bytes": 4e9, "size_gb": 4.0}]
    rec = recommend(detect_hardware(), models)
    assert rec.model_path == "/fake/test.gguf"
    assert rec.n_ctx > 0


def test_recommend_matches_the_load_for_a_large_model_on_a_small_card(gpu_offload_runtime):
    """Large model on 8 GB GPU: the recommendation must equal what the loader does.

    This test used to assert `rec.n_gpu_layers == _gpu_layers_for_model(...)` and
    `>= 10`, which bound it to the SECOND fit calculation recommend() used to
    carry — layers first, context cut to pay for them, with a floor of 10 layers.
    The loader (smart_fit_config) does the opposite: it keeps the context and
    sheds layers. So the recommendation promised 10 layers while the load
    delivered 9, and the Hardware Tuning tab showed both.

    recommend() now runs the loader's own fit, so the original intent — a layer
    count that is never stale after ctx changes — holds by construction, and the
    figure shown to the operator is the figure they will get.

    NOTE the deliberate behaviour change: the "at least 10 GPU layers, trim ctx
    to get there" preference is gone. On this configuration the recommendation is
    now 9 layers at the full context rather than 10 at a reduced one — which is
    what the loader was going to do regardless.
    """
    from eli.core.hardware_profile import (HardwareProfile, unified_fit_config,
                                           _layers_for_size, vram_reserve_mb)
    hw = HardwareProfile(
        has_gpu=True,
        gpu_name="NVIDIA GeForce RTX 2060 SUPER",
        free_vram_mb=6635,
        total_vram_mb=8192,
        vram_gb=6.5,
        cpu_threads=12,
        ram_gb=32.0,
        available_ram_gb=16.0,
    )
    models = [{
        "name": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "path": "/fake/big.gguf",
        "size_bytes": int(20.61e9),
        "size_gb": 20.61,
    }]
    rec = recommend(hw, models)
    total = _layers_for_size(20.61)
    from eli.core.hardware_profile import auto_ctx_target
    kvq = bool(rec.cache_type_k)            # the KV type recommend() chose for this model
    _target_ctx = auto_ctx_target(
        "/fake/big.gguf", 20.61, free_vram_mb=hw.free_vram_mb,
        available_ram_gb=hw.available_ram_gb, use_gpu=True, kv_quantized=kvq)
    _ctx, _layers, _ = unified_fit_config(
        20.61, 6635, hw.available_ram_gb,
        user_ctx=int(_target_ctx), user_batch=128,
        reserve_mb=vram_reserve_mb(), kv_quantized=kvq,
        total_layers=total, min_batch=128,
        gpu_integrated=False,
    )
    expected_layers = total if int(_layers) >= 99 else int(_layers)
    assert (rec.n_ctx, rec.n_gpu_layers) == (_ctx, expected_layers)
    assert rec.n_gpu_layers > 0, "a 6.6GB card must still get GPU offload"


def _saving(monkeypatch):
    saved = []
    import eli.core.runtime_settings as rs
    monkeypatch.setattr(rs, "save_settings", lambda d: saved.append(dict(d)))
    monkeypatch.setattr(rs, "load_settings", lambda *a, **k: {
        "model_path": "/kept/Chosen.gguf", "temperature": 0.3, "max_tokens": -1,
        "provider": "ollama", "mode_presets": {"quick": {"max_tokens": 9}}})
    return saved


def test_applying_a_profile_with_no_model_found_keeps_the_users_model_and_own_settings(monkeypatch):
    """With no model in ELI's folder the recommendation's path is empty: applied, it wiped the
    model, the temperature, the reply ceiling, the mode budgets and the provider."""
    from eli.core.hardware_profile import ModelRecommendation, apply_recommendation
    saved = _saving(monkeypatch)
    rec = ModelRecommendation()
    rec.n_ctx, rec.n_gpu_layers, rec.batch_size = 4096, 10, 256
    result = apply_recommendation(rec)
    assert result["ok"] is True and result["model_written"] is False
    assert len(saved) == 1
    written = saved[0]
    assert not {"model_path", "custom_model_path", "bundled_model_path", "gguf_model_path",
                "temperature", "max_tokens", "mode_presets", "provider"} & set(written)
    assert written["hw_profile_n_ctx"] == 4096 and result["written"] == written


def test_applying_a_profile_that_found_a_model_writes_that_model(monkeypatch, tmp_path):
    from eli.core.hardware_profile import ModelRecommendation, apply_recommendation
    saved = _saving(monkeypatch)
    model = tmp_path / "Found.gguf"
    model.write_bytes(b"GGUF")
    rec = ModelRecommendation()
    rec.model_path = str(model)
    result = apply_recommendation(rec)
    assert result["model_written"] is True and saved[0]["model_path"] == str(model)
    assert "temperature" not in saved[0]


def test_a_failed_save_is_reported_as_a_failure(monkeypatch):
    from eli.core.hardware_profile import ModelRecommendation, apply_recommendation
    import eli.core.runtime_settings as rs
    monkeypatch.setattr(rs, "save_settings", lambda d: (_ for _ in ()).throw(OSError("read-only")))
    result = apply_recommendation(ModelRecommendation())
    assert result["ok"] is False and "read-only" in result["error"]


def test_models_are_found_in_every_model_folder_not_only_elis_own(monkeypatch, tmp_path):
    import eli.core.hardware_profile as hp
    import eli.core.model_folders as mf
    named = tmp_path / "named"
    named.mkdir()
    (named / "Kept.gguf").write_bytes(b"GGUF")
    monkeypatch.setattr(mf, "model_folders", lambda *a, **k: [named])
    monkeypatch.setattr("eli.cognition.model_load_diagnostics.not_a_chat_model", lambda p: "")
    found = hp.discover_models()
    assert [m["name"] for m in found] == ["Kept.gguf"]
