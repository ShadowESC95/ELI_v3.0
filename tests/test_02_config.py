import json
import pytest
from unittest.mock import patch
from eli.core.runtime_settings import load_settings, save_settings
import eli.core.runtime_settings as rs

def test_save_load_settings(tmp_path):
    def fake_settings_file():
        return tmp_path / "settings.json"
    with patch.object(rs, "_settings_file", fake_settings_file):
        save_settings({"test": 123})
        loaded = load_settings()
        assert loaded.get("test") == 123


def test_sampler_getters_fall_back_to_the_one_defaults_table(monkeypatch):
    """With no readable settings the getters gave figures of their own (0.55, 0.9, 1.15, 2048) that
    differed from runtime_settings.DEFAULTS, the table every normal load starts from."""
    from eli.core import config

    def unreadable():
        raise OSError("settings cannot be read")
    monkeypatch.setattr(config, "_rs_load", unreadable)
    for name in ("ELI_TEMP", "ELI_TEMPERATURE", "ELI_NUM_PREDICT", "ELI_MAX_TOKENS",
                 "ELI_GGUF_N_BATCH", "ELI_BATCH_SIZE"):
        monkeypatch.delenv(name, raising=False)
    assert config.get_temperature() == rs.DEFAULTS["temperature"]
    assert config.get_top_p() == rs.DEFAULTS["top_p"]
    assert config.get_top_k() == rs.DEFAULTS["top_k"]
    assert config.get_repeat_penalty() == rs.DEFAULTS["repeat_penalty"]
    assert config.get_num_predict() == rs.DEFAULTS["max_tokens"]
    assert config.get_gguf_n_batch() == rs.DEFAULTS["batch_size"]
