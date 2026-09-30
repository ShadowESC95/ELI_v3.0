"""The startup dialog printed 'GPU-layer load parameter: 7' and, further down the same log,
'selected=smart-fit (ctx=... gpu_layers=41 batch=...)' for the same load — two different numbers
for what looked like the same thing, with no explanation, because the early print asserted an
outcome ("will raise this to all N layers") before the fit had even run.

The early print now only states what's KNOWN at that point (MoE offload detected, experts will
stay in RAM) without asserting a layer count — the real, measured fitted/total layer count is
reported separately, right after the (now MoE-aware) smart-fit actually runs.

eli/gui/eli_pro_audio_gui_v2_0.py is DEAD in v3 (see eli/gui/app.py's main(), which launches
eli.gui.gui_eli_original -> eli.gui.main_window, never this file) — a prior version of this test
checked the dead file and silently passed regardless of what the real loader printed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "eli/gui/main_window/models.py"


def test_the_early_print_no_longer_asserts_an_unverified_outcome():
    src = MODELS.read_text(encoding="utf-8")
    i = src.index("GPU-layer load parameter")
    around = src[max(0, i - 400):i + 400]
    assert "_moe_gui" in around
    assert "will raise this to all" not in around


def test_the_early_print_says_the_fit_is_measured_below():
    src = MODELS.read_text(encoding="utf-8")
    i = src.index("GPU-layer load parameter")
    around = src[i:i + 400]
    assert "measured below" in around


def test_a_separate_post_fit_report_states_the_real_outcome():
    """The number that actually loads is reported once the fit has run, sourced from the same
    _sf_layers the load ladder itself uses — not recomputed independently."""
    src = MODELS.read_text(encoding="utf-8")
    i = src.index('_sf_ctx, _sf_layers, _sf_batch = _sf_fit(')
    j = src.index('_add_attempt("smart-fit", _sf_ctx, _sf_layers, _sf_batch)', i)
    block = src[i:j]
    assert "MoE fit:" in block
    assert "_sf_layers_real" in block
