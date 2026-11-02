from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_setting_has_a_default():
    from eli.core.runtime_settings import DEFAULTS
    assert DEFAULTS["moe_expert_offload"] == "auto"
