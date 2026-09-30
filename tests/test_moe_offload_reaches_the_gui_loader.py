from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# eli/gui/eli_pro_audio_gui_v2_0.py is DEAD in v3 — the real startup-dialog model loader is
# eli.gui.main_window.models.LocalModelManager (see eli/gui/gui_eli_original.py's re-export and
# eli/gui/app.py's main(), which launches eli.gui.gui_eli_original, not the dead file). A prior
# version of this test checked the dead file and silently passed regardless of what the real
# loader did.
MODELS = ROOT / "eli/gui/main_window/models.py"


def test_the_gui_model_loader_also_computes_a_moe_plan():
    # models.py builds Llama(...) itself — it does not go through gguf_inference.load_model() —
    # so the MoE offload hook has to be wired in here separately, or the startup dialog's model
    # load silently ignores mixture-of-experts models.
    src = MODELS.read_text(encoding="utf-8")
    assert "import moe_offload as _moe_offload_gui" in src and "_moe_offload_gui.plan_for_load" in src
    assert "_build_llama" in src and "expert_offload_params" in src
    assert '_applied["moe_expert_offload"] = bool(_moe_gui)' in src


def test_the_fit_call_is_moe_aware():
    src = MODELS.read_text(encoding="utf-8")
    assert 'moe_resident_gb=(_moe_gui["resident_gb"] if _moe_gui else None)' in src


def test_no_unconditional_layer_override_remains():
    """Every candidate reaching the Llama() build must carry its own real number — the
    operator's own request, or the VRAM-verified smart-fit result — not get forced to the
    full MoE layer count regardless of what it actually measured."""
    src = MODELS.read_text(encoding="utf-8")
    assert 'int(_moe_gui["layers"]) + 1 if _moe_gui["layers"] else 999' not in src
