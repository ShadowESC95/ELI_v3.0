"""What the GPU actually holds for a mixture-of-experts model.

llama.cpp counts the output layer as a layer: n_gpu_layers=40 on a 40-block model put the output
layer and 39 blocks on the GPU and left block 0's attention on the CPU, while ELI reported 40/40.
A full expert-offload request asks for every layer (41). The tuner's whole-layer fit line ("only
8 layers") is the dense calculation and says so for an MoE model.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_a_full_expert_offload_asks_llamacpp_for_every_layer():
    from eli.core import moe_offload
    with patch.object(moe_offload, "profile", return_value=SimpleNamespace(block_count=40)):
        assert moe_offload.full_offload_layers(40, "m.gguf") == 41  # 40 gave 39 blocks + output
        assert moe_offload.full_offload_layers(30, "m.gguf") == 30
        assert moe_offload.full_offload_layers(99, "m.gguf") == 99


def test_every_moe_loader_asks_for_the_full_count():
    for rel in ("eli/cognition/gguf_inference.py", "eli/core/load_probe.py",
                "eli/gui/main_window/models.py"):
        assert "full_offload_layers(" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_the_whole_layer_fit_line_is_marked_unused_for_moe():
    src = (ROOT / "eli/core/hardware_profile.py").read_text(encoding="utf-8")
    i = src.index("if _moe_plan:\n        if _dense_fit_line is not None:")
    assert "NOT used for this mixture-of-experts" in src[i:i + 600]
