from pathlib import Path
import importlib
import pytest
from eli.learning.training_preflight import preflight_target

def _spec_present(mod: str) -> bool:
    # find_spec raises ValueError if a partially-initialised module has __spec__=None
    # (seen with `accelerate` in the venv) — treat that as "present but quirky".
    try:
        return importlib.util.find_spec(mod) is not None
    except (ValueError, ImportError, AttributeError):
        try:
            return importlib.import_module(mod) is not None
        except Exception:
            return False


_TRAINING_MODULES = ("peft", "accelerate", "datasets")
_training_deps_available = all(_spec_present(m) for m in _TRAINING_MODULES)


def test_preflight_blocks_missing_phi_base_when_isolated(tmp_path):
    report = preflight_target(
        "eli_phi",
        base_model_path=tmp_path / "missing-phi-base",
        allow_default_candidates=False,
    )

    assert report["can_train"] is False
    assert "trainable Phi-3 base model unresolved" in report["problems"]
    assert report["base_model_resolution"]["ok"] is False


def test_preflight_keeps_phi_ultra_target_scope():
    report = preflight_target("eli_phi_ultra")

    assert report["target"] == "eli_phi_ultra"
    assert report["guard_plan"]["target"] == "eli_phi_ultra"


PHI_BASE = Path(__file__).resolve().parents[1] / "models" / "hf" / "Phi-3-mini-4k-instruct"
_needs_phi_base = pytest.mark.skipif(
    not PHI_BASE.exists(),
    reason="Phi-3 base model not downloaded in this install — these validate the downloaded-base path",
)


@pytest.mark.skipif(
    not _training_deps_available,
    reason="peft/accelerate/datasets not installed — training pipeline unavailable",
)
@_needs_phi_base
def test_preflight_can_train_after_phi_base_download():
    report = preflight_target("eli_phi")

    assert report["base_model_resolution"]["ok"] is True
    assert report["can_train"] is True
    assert report["problems"] == []


@_needs_phi_base
def test_preflight_report_does_not_leak_weak_base_warning():
    report = preflight_target("eli_phi")

    blob = str(report)
    assert "base model path missing:" not in blob
    assert "trainable Phi-3 base model unresolved" not in report.get("problems", [])
