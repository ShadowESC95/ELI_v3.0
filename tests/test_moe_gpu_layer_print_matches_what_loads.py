"""The startup dialog printed 'GPU-layer load parameter: 7' and, further down the same log,
'selected=smart-fit (ctx=... gpu_layers=41 batch=...)' for the same load — two different numbers
for what looked like the same thing, with no explanation, because the early print used the
pre-MoE layer count. A line now follows it, once the MoE plan is known, explaining the bump.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_moe_plan_explains_its_own_layer_bump():
    src = (ROOT / "eli/gui/eli_pro_audio_gui_v2_0.py").read_text(encoding="utf-8")
    assert "GPU-layer load parameter" in src
    i = src.index("_moe_gui = None")
    around = src[i:i + 1100]
    assert "will raise this to all" in around
