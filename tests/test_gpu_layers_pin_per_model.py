"""A pinned GPU-layer count belongs to the model it was chosen for.

n_gpu_layers is an ABSOLUTE layer count stored under one global key, so it meant
something different on every model. Live: 7 layers -- correct for a 15.66GB 27B
on 8GB of VRAM -- carried over to a 4GB model whose 32 layers all fit in VRAM,
stranding 25 of them on the CPU. The tuner measured 32, reported "all layers on
GPU (free VRAM sufficient)", and stood down because the user's value wins. What
was missing was any record of WHICH model the pin was for.
"""

import eli.core.runtime_settings as rs


def test_the_provenance_key_exists_and_defaults_to_unknown():
    assert "n_gpu_layers_model" in rs.DEFAULTS
    assert rs.DEFAULTS["n_gpu_layers_model"] == ""


def test_the_key_survives_a_settings_load():
    """A key the schema drops would make the whole fix a no-op."""
    loaded = rs.load_settings() or {}
    assert "n_gpu_layers_model" in loaded


def test_all_layers_is_a_policy_and_survives_a_model_swap():
    """-1 means "offload everything" -- correct on EVERY model.

    Treating it as stale would quietly downgrade "try them all, then fall back
    to the measured fit" into "just use the fallback", which is a different and
    worse behaviour on capable hardware. The loader already reduces to fit.
    """
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf", {"n_gpu_layers": -1, "n_gpu_layers_model": "some-other-27b.gguf"})
    assert got == -1, "'all layers' was discarded on a model swap"


def test_the_99_earlier_releases_wrote_for_every_layer_is_read_as_every_layer(tmp_path, monkeypatch):
    """99 stood for every layer and fell short on a model of more than 99 layers; a settings file
    carrying it is read as llama.cpp's -1."""
    import json
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"n_gpu_layers": 99, "vision_n_gpu_layers": 99, "vision_fast_n_gpu_layers": 99}))
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(f))
    for var in ("ELI_GGUF_N_GPU_LAYERS", "ELI_N_GPU_LAYERS", "ELI_GPU_LAYERS"):
        monkeypatch.delenv(var, raising=False)
    s = rs._load_settings_unsanitized()
    assert (s["n_gpu_layers"], s["vision_n_gpu_layers"], s["vision_fast_n_gpu_layers"]) == (-1, -1, -1)


def test_a_specific_count_from_another_model_is_dropped():
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "qwen-27b.gguf"})
    assert got is None


def test_a_specific_count_for_this_model_is_honoured():
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "phi3.gguf"})
    assert got == 7


def test_saving_settings_does_not_turn_the_setting_into_a_global_override(tmp_path, monkeypatch):
    """apply_env, run after every save, copied n_gpu_layers into ELI_GGUF_N_GPU_LAYERS, which the
    loader reads first: an unpinned 0 became "processor only" and one model's pin applied to all."""
    from eli.cognition.gguf_inference import load_request
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_path / "settings.json"))
    for var in ("ELI_GGUF_N_GPU_LAYERS", "ELI_N_GPU_LAYERS", "ELI_GPU_LAYERS"):
        monkeypatch.delenv(var, raising=False)
    rs.save_settings({"model_path": "/m/a.gguf"})
    assert load_request("/m/a.gguf").n_gpu_layers is None
    rs.save_settings({"n_gpu_layers": 7, "n_gpu_layers_model": "a.gguf", "n_gpu_layers_source": "user"})
    assert load_request("/m/a.gguf").n_gpu_layers == 7
    assert load_request("/m/b.gguf").n_gpu_layers is None


def test_model_identity_is_case_insensitive():
    """NTFS and default macOS filesystems are case-insensitive.

    "Phi3.gguf" and "phi3.gguf" are the same file to those users; comparing
    exactly discarded a pin they had just set, on every load.
    """
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "Phi3.GGUF"})
    assert got == 7


def test_windows_paths_resolve_on_any_host():
    """A backslash path must resolve even when a POSIX host reads it."""
    got = rs.pinned_gpu_layers_for_model(
        r"C:\models\phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "phi3.gguf"})
    assert got == 7
    assert rs.model_identity_key(r"C:\models\Phi3.gguf") == "phi3.gguf"
    assert rs.model_identity_key("/home/u/models/Phi3.gguf") == "phi3.gguf"


def test_a_different_model_is_still_rejected_after_normalisation():
    """Normalising must not make everything match."""
    assert rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "qwen-27b.gguf"}) is None


# ── ctx-awareness (port of v2.4.56) ─────────────────────────────────────────
# A pinned layer count is only as valid as the ctx it was measured at: KV
# cache for every layer lives on GPU regardless of offload, so it scales with
# ctx directly. Live report (v2): gpu_layers=7 was measured (and correctly
# pinned) for THIS model at ctx=2048; the operator then typed ctx=12200 and
# loaded -- the model-identity check alone still said the pin applied (same
# model!), so 7 was enforced as a hard ceiling at a ctx it was never
# validated for, and the loader crushed batch and ctx far harder than
# necessary trying to squeeze under it. Same failure shape as the cross-model
# case above, one dimension over.
def test_a_pin_from_a_much_smaller_ctx_is_dropped():
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "phi3.gguf", "n_gpu_layers_ctx": 2048},
        current_ctx=12200)
    assert got is None, "a pin measured at ctx=2048 must not cap a load at ctx=12200"


def test_a_pin_at_the_same_ctx_is_still_honoured():
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "phi3.gguf", "n_gpu_layers_ctx": 2048},
        current_ctx=2048)
    assert got == 7


def test_ctx_is_ignored_when_the_caller_does_not_check_it():
    """Backward compatible: a caller that never passes current_ctx keeps the
    old (model-only) behaviour, e.g. a settings inspector with no load ctx."""
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "phi3.gguf", "n_gpu_layers_ctx": 2048})
    assert got == 7


def test_a_pin_saved_before_ctx_was_ever_stamped_is_not_spuriously_invalidated():
    """settings.json written before this fix has no n_gpu_layers_ctx key at
    all -- absence must not be treated as "definitely a different ctx"."""
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": 7, "n_gpu_layers_model": "phi3.gguf"},
        current_ctx=12200)
    assert got == 7


def test_all_layers_pin_survives_a_ctx_change_too():
    """-1 is a policy, correct at every ctx as well as every model."""
    got = rs.pinned_gpu_layers_for_model(
        "/models/phi3.gguf",
        {"n_gpu_layers": -1, "n_gpu_layers_model": "phi3.gguf", "n_gpu_layers_ctx": 2048},
        current_ctx=12200)
    assert got == -1
