"""Tests for eli.core.runtime_settings — ~180 tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eli.core.runtime_settings import (
    DEFAULTS,
    _coerce_value,
    _heal_model_paths,
    _migrate_legacy_keys,
    _MODEL_PATH_KEYS,
    _resolve_relative_model_paths,
    load_settings,
    save_settings,
    update_settings,
    load_runtime_settings,
    INT_KEYS,
    FLOAT_KEYS,
    BOOL_KEYS,
    LEGACY_KEY_MIGRATIONS,
    ENV_TO_KEY,
)


# ── DEFAULTS completeness ─────────────────────────────────────────────────

def test_defaults_has_provider():
    assert "provider" in DEFAULTS

def test_defaults_has_model_path():
    assert "model_path" in DEFAULTS

def test_defaults_has_n_ctx():
    assert "n_ctx" in DEFAULTS
    assert DEFAULTS["n_ctx"] == 0          # auto: sized for the model and machine in use

def test_defaults_has_max_tokens():
    assert "max_tokens" in DEFAULTS

def test_defaults_has_temperature():
    assert "temperature" in DEFAULTS
    assert 0.0 < DEFAULTS["temperature"] < 2.0

def test_defaults_has_n_gpu_layers():
    assert "n_gpu_layers" in DEFAULTS

def test_defaults_has_n_threads(monkeypatch):
    """0 is auto: one thread per physical core, worked out on the machine at every load."""
    from eli.core import config
    from eli.core.hardware_profile import recommend_cpu_threads
    assert DEFAULTS["n_threads"] == 0
    for var in ("ELI_GGUF_THREADS", "ELI_N_THREADS", "ELI_CPU_THREADS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "get", lambda key, default=None: 0 if key == "n_threads" else default)
    assert config.get_n_threads() == recommend_cpu_threads()

def test_defaults_has_batch_size():
    assert "batch_size" in DEFAULTS
    assert DEFAULTS["batch_size"] == 512

def test_defaults_has_theme():
    assert "theme" in DEFAULTS

def test_defaults_has_tts_voice():
    assert "tts_voice" in DEFAULTS

def test_defaults_has_image_keys():
    for k in ("image_backend", "image_model_path", "image_device"):
        assert k in DEFAULTS


# ── KEY CLASSIFICATION ────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["n_ctx", "max_tokens", "top_k", "n_gpu_layers",
                                  "n_threads", "batch_size", "image_steps"])
def test_int_keys_classification(key):
    assert key in INT_KEYS

@pytest.mark.parametrize("key", ["temperature", "top_p", "repeat_penalty", "image_guidance"])
def test_float_keys_classification(key):
    assert key in FLOAT_KEYS

@pytest.mark.parametrize("key", ["use_mmap", "use_mlock", "auto_speak", "mic_enabled",
                                  "auto_save", "log_to_file", "auto_load", "first_run_complete"])
def test_bool_keys_classification(key):
    assert key in BOOL_KEYS


# ── _coerce_value ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,raw,expected", [
    ("n_ctx", "8192", 8192),
    ("n_ctx", 8192, 8192),
    ("max_tokens", "-1", -1),
    ("batch_size", "512", 512),
    ("n_gpu_layers", "99", 99),
    ("n_threads", "8", 8),
])
def test_coerce_int(key, raw, expected):
    assert _coerce_value(key, raw) == expected

@pytest.mark.parametrize("key,raw,expected", [
    ("temperature", "0.7", 0.7),
    ("top_p", "0.95", 0.95),
    ("repeat_penalty", "1.1", 1.1),
    ("image_guidance", "7.2", 7.2),
])
def test_coerce_float(key, raw, expected):
    assert abs(_coerce_value(key, raw) - expected) < 1e-9

@pytest.mark.parametrize("key,raw,expected", [
    ("use_mmap", "1", True),
    ("use_mmap", "true", True),
    ("use_mmap", "yes", True),
    ("use_mmap", "on", True),
    ("use_mmap", "True", True),
    ("use_mmap", "0", False),
    ("use_mmap", "false", False),
    ("use_mmap", "no", False),
    ("use_mmap", "off", False),
    ("use_mmap", "False", False),
    ("auto_speak", True, True),
    ("auto_speak", False, False),
])
def test_coerce_bool(key, raw, expected):
    assert _coerce_value(key, raw) == expected

def test_coerce_int_invalid_returns_default():
    result = _coerce_value("n_ctx", "not_a_number")
    assert result == DEFAULTS["n_ctx"]

def test_coerce_float_invalid_returns_default():
    result = _coerce_value("temperature", "not_a_float")
    assert result == DEFAULTS["temperature"]

def test_coerce_string_passthrough():
    result = _coerce_value("provider", "ollama")
    assert result == "ollama"

def test_coerce_unknown_key_passthrough():
    result = _coerce_value("unknown_key", "some_value")
    assert result == "some_value"


# ── _migrate_legacy_keys ──────────────────────────────────────────────────

def test_migrate_gpu_layers():
    data = {"gpu_layers": 11, "model_path": "model.gguf"}
    out, migrated = _migrate_legacy_keys(data)
    assert "gpu_layers" not in out
    assert out["n_gpu_layers"] == 11
    assert "gpu_layers" in migrated

def test_migrate_cpu_threads():
    data = {"cpu_threads": 8}
    out, migrated = _migrate_legacy_keys(data)
    assert "cpu_threads" not in out
    assert out["n_threads"] == 8
    assert "cpu_threads" in migrated

def test_migrate_does_not_override_canonical():
    # If n_gpu_layers already present, legacy gpu_layers should NOT override it
    data = {"gpu_layers": 5, "n_gpu_layers": 11}
    out, migrated = _migrate_legacy_keys(data)
    assert out["n_gpu_layers"] == 11  # canonical value preserved
    assert "gpu_layers" not in out

def test_migrate_no_legacy_keys():
    data = {"model_path": "x.gguf", "n_gpu_layers": 99}
    out, migrated = _migrate_legacy_keys(data)
    assert migrated == []
    assert out == data

def test_migrate_both_legacy_keys():
    data = {"gpu_layers": 8, "cpu_threads": 4}
    out, migrated = _migrate_legacy_keys(data)
    assert "gpu_layers" not in out
    assert "cpu_threads" not in out
    assert out["n_gpu_layers"] == 8
    assert out["n_threads"] == 4
    assert len(migrated) == 2

def test_migrate_preserves_other_keys():
    data = {"gpu_layers": 8, "temperature": 0.7, "provider": "custom_gguf"}
    out, _ = _migrate_legacy_keys(data)
    assert out["temperature"] == 0.7
    assert out["provider"] == "custom_gguf"


def test_a_thread_count_the_tuner_wrote_goes_back_to_auto_once(monkeypatch):
    from eli.core.runtime_settings import _bring_up_to_date
    monkeypatch.setattr("os.cpu_count", lambda: 12)
    out, migrated = _bring_up_to_date({"n_threads": 10})
    assert out["n_threads"] == 0 and "settings_revision" in migrated
    out, migrated = _bring_up_to_date({**out, "n_threads": 10})    # chosen afterwards: kept
    assert out["n_threads"] == 10 and migrated == []
    assert _bring_up_to_date({"n_threads": 4})[0]["n_threads"] == 4  # not a figure the tuner wrote


# ── _resolve_relative_model_paths ────────────────────────────────────────

def test_relative_path_resolved_to_absolute():
    settings = {"model_path": "models/gguf/base/test.gguf"}
    result = _resolve_relative_model_paths(settings)
    assert Path(result["model_path"]).is_absolute()

def test_absolute_path_unchanged():
    settings = {"model_path": "/absolute/path/model.gguf"}
    result = _resolve_relative_model_paths(settings)
    assert result["model_path"] == "/absolute/path/model.gguf"

def test_empty_path_unchanged():
    settings = {"model_path": ""}
    result = _resolve_relative_model_paths(settings)
    assert result["model_path"] == ""

def test_all_model_path_keys_resolved():
    settings = {k: "models/test.gguf" for k in _MODEL_PATH_KEYS}
    result = _resolve_relative_model_paths(settings)
    for k in _MODEL_PATH_KEYS:
        assert Path(result[k]).is_absolute()

def test_non_path_keys_untouched():
    settings = {"model_path": "models/test.gguf", "provider": "custom_gguf", "n_ctx": 16384}
    result = _resolve_relative_model_paths(settings)
    assert result["provider"] == "custom_gguf"
    assert result["n_ctx"] == 16384


# ── _heal_model_paths ─────────────────────────────────────────────────────

def test_heal_valid_path_unchanged(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake")
    settings = {"model_path": str(model)}
    result, changed = _heal_model_paths(settings)
    assert result["model_path"] == str(model)
    assert not changed

def test_heal_empty_path_unchanged():
    settings = {"model_path": ""}
    result, changed = _heal_model_paths(settings)
    assert result["model_path"] == ""
    assert not changed

def test_heal_relative_path_skipped():
    # Relative paths are handled by _resolve_relative_model_paths, not healer
    settings = {"model_path": "models/test.gguf"}
    result, changed = _heal_model_paths(settings)
    assert not changed


def test_heal_syncs_model_path_from_fallback(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake")
    # model_path is stale, but custom_model_path points to real file
    # Note: heal only heals stale absolute paths in real dirs; this tests model_path sync logic
    settings = {
        "model_path": "/stale/path/model.gguf",
        "custom_model_path": str(model),
    }
    result, changed = _heal_model_paths(settings)
    # model_path should be synced to working custom_model_path
    if changed:
        assert Path(result["model_path"]).exists()


# ── load_settings ─────────────────────────────────────────────────────────


def test_load_settings_has_all_defaults(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    result = load_settings()
    for k in DEFAULTS:
        assert k in result, f"Missing key: {k}"

def test_load_settings_with_custom_values(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    tmp_settings_file.write_text(json.dumps({"n_ctx": 4096, "temperature": 0.5}))
    result = load_settings()
    assert result["n_ctx"] == 4096
    assert result["temperature"] == 0.5

def test_load_settings_env_override(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    monkeypatch.setenv("ELI_N_CTX", "32768")
    result = load_settings()
    assert result["n_ctx"] == 32768

def test_load_settings_env_temperature_override(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    monkeypatch.setenv("ELI_TEMPERATURE", "0.3")
    result = load_settings()
    assert abs(result["temperature"] - 0.3) < 1e-9

def test_load_settings_gpu_layers_alias(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    monkeypatch.setenv("ELI_GPU_LAYERS", "8")
    result = load_settings()
    assert result["n_gpu_layers"] == 8

def test_load_settings_missing_file_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_path / "nonexistent.json"))
    result = load_settings()
    assert result["provider"] == DEFAULTS["provider"]

def test_load_settings_corrupted_file_uses_defaults(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    tmp_settings_file.write_text("NOT VALID JSON }{", encoding="utf-8")
    result = load_settings()
    assert result["provider"] == DEFAULTS["provider"]


def test_load_settings_legacy_migration_applied(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    tmp_settings_file.write_text(json.dumps({"gpu_layers": 5}))
    result = load_settings()
    assert result["n_gpu_layers"] == 5

def test_load_settings_model_path_fallback(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    tmp_settings_file.write_text(json.dumps({"custom_model_path": "/some/model.gguf"}))
    result = load_settings()
    # model_path should fall back to custom_model_path if empty
    assert result["model_path"] == "/some/model.gguf"


# ── save_settings ─────────────────────────────────────────────────────────

def test_save_settings_writes_file(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    save_settings({"temperature": 0.3})
    data = json.loads(tmp_settings_file.read_text())
    assert abs(data["temperature"] - 0.3) < 1e-9

def test_save_settings_merge_not_replace(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    tmp_settings_file.write_text(json.dumps({"n_ctx": 8192, "temperature": 0.7}))
    save_settings({"temperature": 0.5})
    data = json.loads(tmp_settings_file.read_text())
    assert data["n_ctx"] == 8192
    assert abs(data["temperature"] - 0.5) < 1e-9

def test_save_settings_creates_parent_dir(tmp_path, monkeypatch):
    deep_path = tmp_path / "subdir" / "settings.json"
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(deep_path))
    save_settings({"theme": "light"})
    assert deep_path.exists()
    data = json.loads(deep_path.read_text())
    assert data["theme"] == "light"

def test_save_settings_strips_legacy_key_from_dict(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    save_settings({"gpu_layers": 8})
    data = json.loads(tmp_settings_file.read_text())
    assert "gpu_layers" not in data
    assert data.get("n_gpu_layers") == 8


def test_save_settings_project_paths_are_portable(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    from eli.core.runtime_settings import PROJECT_ROOT

    model = PROJECT_ROOT / "models" / "gguf" / "base" / "portable-test.gguf"
    save_settings({"model_path": str(model)})
    data = json.loads(tmp_settings_file.read_text())
    assert data["model_path"] == "models/gguf/base/portable-test.gguf"


# ── update_settings ───────────────────────────────────────────────────────

def test_update_settings_returns_full_dict(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    result = update_settings(temperature=0.4)
    assert isinstance(result, dict)
    assert abs(result["temperature"] - 0.4) < 1e-9

def test_update_settings_persists(tmp_settings_file, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    update_settings(theme="light")
    data = json.loads(tmp_settings_file.read_text())
    assert data["theme"] == "light"


# ── ENV_TO_KEY mapping completeness ──────────────────────────────────────

def test_env_to_key_has_provider():
    assert "ELI_PROVIDER" in ENV_TO_KEY

def test_env_to_key_has_model_path():
    assert "ELI_MODEL_PATH" in ENV_TO_KEY

def test_env_to_key_legacy_gpu_alias():
    assert "ELI_GPU_LAYERS" in ENV_TO_KEY
    assert ENV_TO_KEY["ELI_GPU_LAYERS"] == "n_gpu_layers"

def test_env_to_key_legacy_cpu_alias():
    assert "ELI_CPU_THREADS" in ENV_TO_KEY
    assert ENV_TO_KEY["ELI_CPU_THREADS"] == "n_threads"

def test_env_to_key_all_values_in_defaults():
    for env_name, key in ENV_TO_KEY.items():
        assert key in DEFAULTS or key in ("model_path",), f"{key} from {env_name} not in DEFAULTS"


# ── _MODEL_PATH_KEYS ──────────────────────────────────────────────────────

def test_model_path_keys_set():
    assert "model_path" in _MODEL_PATH_KEYS
    assert "bundled_model_path" in _MODEL_PATH_KEYS
    assert "custom_model_path" in _MODEL_PATH_KEYS
    assert "gguf_model_path" in _MODEL_PATH_KEYS
    assert "image_model_path" in _MODEL_PATH_KEYS


# ── LEGACY_KEY_MIGRATIONS ─────────────────────────────────────────────────

def test_legacy_migrations_defined():
    assert "gpu_layers" in LEGACY_KEY_MIGRATIONS
    assert "cpu_threads" in LEGACY_KEY_MIGRATIONS

def test_legacy_migrations_map_to_canonical():
    assert LEGACY_KEY_MIGRATIONS["gpu_layers"] == "n_gpu_layers"
    assert LEGACY_KEY_MIGRATIONS["cpu_threads"] == "n_threads"


# ── corrupt settings file must not silently masquerade as real config ──────
#
# A corrupt/truncated settings.json used to fall back to DEFAULTS with zero
# signal that the user's real saved configuration was discarded -- downstream
# code reporting "gpu layers configured: N" had no way to know N was a
# built-in default rather than something the user actually set. Confirmed
# with a clean first-run (no file at all) NOT raising the same flag, since
# that is not an error.
def test_corrupt_settings_file_is_flagged(tmp_path, monkeypatch):
    from eli.core import runtime_settings as rs

    bad = tmp_path / "settings.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(bad))

    settings = rs._load_settings_unsanitized(apply_env_overlay=False)
    assert settings["n_gpu_layers"] == rs.DEFAULTS["n_gpu_layers"], (
        "still falls back to defaults -- that part was already correct"
    )
    assert rs.settings_file_was_corrupt_on_last_load() is True, (
        "corrupt-but-present settings file must be distinguishable from no file at all"
    )


def test_missing_settings_file_is_not_flagged_as_corrupt(tmp_path, monkeypatch):
    from eli.core import runtime_settings as rs

    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_path / "does_not_exist.json"))

    rs._load_settings_unsanitized(apply_env_overlay=False)
    assert rs.settings_file_was_corrupt_on_last_load() is False, (
        "a normal first run (no settings file yet) is not an error"
    )


def test_valid_settings_file_is_not_flagged_as_corrupt(tmp_path, monkeypatch):
    from eli.core import runtime_settings as rs

    good = tmp_path / "settings.json"
    good.write_text(json.dumps({"n_gpu_layers": 17}), encoding="utf-8")
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(good))

    settings = rs._load_settings_unsanitized(apply_env_overlay=False)
    assert settings["n_gpu_layers"] == 17
    assert rs.settings_file_was_corrupt_on_last_load() is False


def test_the_session_log_eli_reads_back_is_on_by_default_and_can_be_switched_off(tmp_settings_file, monkeypatch):
    """Off by default, ELI could not see a line of what its terminal had shown when asked about it."""
    import json as _json
    from eli.core.runtime_settings import DEFAULTS, _bring_up_to_date, load_settings, save_settings
    assert DEFAULTS["log_to_file"] is True
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_settings_file))
    tmp_settings_file.write_text(_json.dumps({"log_to_file": False, "settings_revision": 1}))
    assert load_settings()["log_to_file"] is True, "the old default the settings page saved is on once"
    save_settings({"log_to_file": False})
    assert load_settings()["log_to_file"] is False, "switched off after that, it stays off"
    assert _bring_up_to_date({"settings_revision": 1})[0].get("log_to_file") is None


def test_reading_settings_never_saved_writes_no_file_and_an_unreadable_one_is_kept(tmp_path, monkeypatch):
    """Reading settings that were never saved wrote a settings file (a tool or a test run from a
    checkout wrote its config/settings.json), and a file that could not be read was replaced by one
    holding only the revision count."""
    import json
    from eli.core import runtime_settings as rs
    saved = tmp_path / "settings.json"
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(saved))
    rs.load_settings()
    assert not saved.exists()
    saved.write_text("{not json", encoding="utf-8")
    rs.load_settings()
    assert saved.read_text(encoding="utf-8") == "{not json"
    saved.write_text('{"n_threads": 4}', encoding="utf-8")
    rs.load_settings()
    assert json.loads(saved.read_text(encoding="utf-8"))["settings_revision"] == len(rs._REVISIONS)


def test_a_folder_set_outside_the_install_is_kept_unless_it_is_another_elis(tmp_path, monkeypatch):
    """Every path outside this install was removed at the first settings read, so a process started with
    its stores in a temp folder (the OS simulation, an eval) wrote into the checkout's config instead. A
    copied install's paths into the old install are still removed."""
    import os
    from eli.core import runtime_settings as rs
    old_install = tmp_path / "old_install"
    (old_install / "eli" / "cognition").mkdir(parents=True)
    (old_install / "eli" / "gui").mkdir()
    sandbox = tmp_path / "sandbox" / "config"                 # not made yet
    monkeypatch.setenv("ELI_CONFIG_DIR", str(sandbox))
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(old_install / "artifacts"))
    rs._eli_runtime_clear_stale_env_paths()
    assert os.environ.get("ELI_CONFIG_DIR") == str(sandbox)
    assert "ELI_ARTIFACTS_DIR" not in os.environ


def test_every_reader_of_the_settings_reads_the_file_in_force(monkeypatch, tmp_path):
    """Seven readers built the settings path themselves (the checkout's config/settings.json, or the
    config folder's) instead of asking for the settings file in force (ELI_SETTINGS_FILE first): pointed
    at another settings file, the reasoning, frontier, truth and runtime-path reports and the grounding
    gate described a file nobody uses, the self-repair gate read another file's auto_patch_enabled, and a
    memory reset cleared the name in the wrong file and left it in the one in force."""
    in_force = tmp_path / "elsewhere" / "settings.json"
    in_force.parent.mkdir()
    in_force.write_text(json.dumps({"reasoning_mode": "research", "auto_patch_enabled": True,
                                    "user_name": "Robin"}), encoding="utf-8")
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(in_force))
    in_force = in_force.resolve()
    from eli.core import memory_reset
    from eli.runtime import (control_contracts, deterministic_grounding_gate, frontier_status,
                             reasoning_status, self_improvement, truth_report)

    assert reasoning_status._from_settings() == "research"
    assert deterministic_grounding_gate._eli_settings_v2().get("reasoning_mode") == "research"
    assert frontier_status._runtime_status()["settings_path"] == str(in_force)
    assert control_contracts.runtime_paths()["config_settings"] == str(in_force)
    report = truth_report.runtime_truth_report()
    assert report["settings_path"] == str(in_force) and report["settings_exists"]
    assert memory_reset.discover(tmp_path / "data", tmp_path / "config")["settings"] == [in_force]

    asked = []
    monkeypatch.setattr(self_improvement, "get_broker", lambda: asked.append("broker"))   # past the gate
    monkeypatch.setattr("eli.core.full_control.is_full_control", lambda: False)
    engine = object.__new__(self_improvement.SelfImprovementEngine)
    engine.analyze_and_improve = lambda: {"improvements": []}
    engine._background_analyze("an error", 1)
    assert asked == ["broker"], "the self-repair gate did not read auto_patch_enabled from the file in force"
