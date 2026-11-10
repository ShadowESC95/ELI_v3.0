"""Settings model swap must update ELI_GGUF_MODEL_PATH, not reload the old GGUF."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest


@pytest.fixture()
def settings_home(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    old_model = models / "old-model.gguf"
    new_model = models / "new-model.gguf"
    old_model.write_bytes(b"GGUF")
    new_model.write_bytes(b"GGUF")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        '{"provider":"custom_gguf","model_path":"models/old-model.gguf",'
        '"custom_model_path":"models/old-model.gguf","n_ctx":2048,"n_gpu_layers":0,'
        '"batch_size":64,"n_threads":4}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ELI_ALLOW_EXTERNAL_MODEL_PATHS", "1")
    # Relative env pin (same shape the GUI/apply_env write) so the portability
    # guard does not treat it as a foreign-machine absolute path.
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", "models/old-model.gguf")

    from eli.core import runtime_settings as rs
    monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rs, "_eli_runtime_physical_project_root", lambda: tmp_path)
    monkeypatch.setattr(rs, "_eli_final_project_root", lambda: tmp_path)
    return tmp_path, old_model, new_model, settings_file


def test_save_settings_updates_gguf_model_path_env(settings_home):
    _tmp_path, _old_model, new_model, _settings_file = settings_home
    from eli.core import runtime_settings as rs
    from eli.core.paths import resolve_runtime_path

    rs.save_settings({
        "model_path": "models/new-model.gguf",
        "custom_model_path": "models/new-model.gguf",
        "provider": "custom_gguf",
    })
    env_raw = os.environ.get("ELI_GGUF_MODEL_PATH", "")
    assert "new-model.gguf" in env_raw.replace("\\", "/")
    # Prefer basename check — resolve may map through install root depending
    # on path helpers, but apply_env must have flipped away from old-model.
    assert "old-model.gguf" not in env_raw.replace("\\", "/")
    assert new_model.name in Path(env_raw).name


def test_load_settings_from_disk_ignores_stale_env(settings_home, monkeypatch):
    _tmp_path, _old_model, _new_model, settings_file = settings_home
    from eli.core import runtime_settings as rs

    settings_file.write_text(
        '{"provider":"custom_gguf","model_path":"models/new-model.gguf",'
        '"custom_model_path":"models/new-model.gguf"}\n',
        encoding="utf-8",
    )
    # Env still points at the old model (GUI pin).
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", "models/old-model.gguf")
    poisoned = rs.load_settings()
    assert "old-model" in str(poisoned.get("model_path") or "")
    disk = rs.load_settings_from_disk()
    assert "new-model" in str(disk.get("model_path") or "")


def test_phatic_how_are_you_this_evening():
    from eli.kernel.engine import _is_brief_phatic_prompt

    assert _is_brief_phatic_prompt("hey eli, how are you this evening?")
    assert _is_brief_phatic_prompt("how are you today")
    assert _is_brief_phatic_prompt("how are you this morning")


def test_phatic_speaking_in_tongues_meta():
    from eli.kernel.engine import _is_brief_phatic_prompt

    assert _is_brief_phatic_prompt("hey buddy, you still speaking in tounges?")
    assert _is_brief_phatic_prompt("you still speaking in tongues?")
    assert not _is_brief_phatic_prompt(
        "search for why the model is speaking in tongues and fix the router"
    )


def test_draft_only_gguf_preflight(tmp_path):
    from eli.cognition.model_load_diagnostics import (
        draft_only_load_message,
        is_draft_only_gguf,
        preflight_gguf_model,
    )

    p = tmp_path / "Kimi-K3-DSpark-Q8_0.gguf"
    p.write_bytes(b"GGUF")
    assert is_draft_only_gguf(p, "dflash-draft")
    msg = draft_only_load_message(p, "dflash-draft")
    assert "draft" in msg.lower()
    assert "chat" in msg.lower()
    # preflight needs a real readable header for arch; filename markers still work
    assert is_draft_only_gguf(p)


def test_a_model_outside_elis_folder_that_exists_is_kept(tmp_path, monkeypatch):
    """A browsed-to model (~/Downloads, another drive) was wiped from the settings at the next load:
    two guards each cleared a full path outside the install folder. Only a path that no longer exists
    is stale. Read through load_settings(), as the app reads it."""
    import json
    from eli.core import runtime_settings as rs
    for name in ("ELI_ALLOW_EXTERNAL_MODEL_PATHS", "ELI_SETTINGS_FILE", "ELI_SETTINGS_PATH", "ELI_GGUF_MODEL_PATH",
                 "ELI_MODEL_PATH", "ELI_CUSTOM_MODEL_PATH", "ELI_BUNDLED_MODEL_PATH", "ELI_GGUF_MODEL"):
        monkeypatch.delenv(name, raising=False)
    root, downloads = tmp_path / "ELI_v3", tmp_path / "Downloads"
    (root / "config").mkdir(parents=True)
    downloads.mkdir()
    model = downloads / "chosen.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setenv("ELI_CONFIG_DIR", str(root / "config"))
    for fn in ("_eli_runtime_physical_project_root", "_eli_final_project_root"):
        monkeypatch.setattr(rs, fn, lambda: root)
    (root / "config" / "settings.json").write_text(json.dumps(
        {"provider": "custom_gguf", "model_path": str(model), "custom_model_path": str(downloads / "gone.gguf"),
         "gguf_model_path": "../elsewhere.gguf"}), encoding="utf-8")
    loaded = rs.load_settings()
    assert loaded["model_path"] == str(model.resolve())
    assert loaded["custom_model_path"] == "" and loaded["gguf_model_path"] == ""


def test_a_path_set_while_running_stays_through_later_settings_reads(tmp_path, monkeypatch):
    """A test pointed ELI_CONFIG_DIR at its own folder, another thread read the settings, and the
    check for paths a copied install inherits took the variable away: the writes went to the project."""
    from eli.core import runtime_settings as rs
    rs.load_settings()
    monkeypatch.setenv("ELI_CONFIG_DIR", str(tmp_path / "cfg"))
    reader = threading.Thread(target=rs.load_settings)
    reader.start()
    reader.join()
    assert os.environ["ELI_CONFIG_DIR"] == str(tmp_path / "cfg")


def test_a_path_inherited_from_another_install_is_dropped_at_start(tmp_path, monkeypatch):
    from eli.core import runtime_settings as rs
    monkeypatch.setattr(rs, "_STALE_ENV_CHECKED", False)
    other = tmp_path / "another-install"                     # an ELI install: it holds the eli package
    (other / "eli" / "cognition").mkdir(parents=True)
    (other / "eli" / "gui").mkdir()
    monkeypatch.setenv("ELI_CONFIG_DIR", str(other / "config"))
    rs.load_settings()
    assert "ELI_CONFIG_DIR" not in os.environ


@pytest.fixture()
def product_settings(tmp_path, monkeypatch):
    """Settings read the way the installed app reads them: no ELI_SETTINGS_FILE, so every guard the
    app's settings pass through runs (the test sandbox's ELI_SETTINGS_FILE switches them off)."""
    import json
    from eli.core import runtime_settings as rs
    for name in ("ELI_ALLOW_EXTERNAL_MODEL_PATHS", "ELI_SETTINGS_FILE", "ELI_SETTINGS_PATH", "ELI_GGUF_MODEL_PATH",
                 "ELI_MODEL_PATH", "ELI_CUSTOM_MODEL_PATH", "ELI_BUNDLED_MODEL_PATH", "ELI_GGUF_MODEL",
                 "ELI_MODEL_DIRS", "ELI_N_CTX", "ELI_GGUF_N_CTX"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "ELI_v3"
    (root / "config").mkdir(parents=True)
    monkeypatch.setenv("ELI_CONFIG_DIR", str(root / "config"))
    for fn in ("_eli_runtime_physical_project_root", "_eli_final_project_root"):
        monkeypatch.setattr(rs, fn, lambda: root)
    monkeypatch.setattr(rs, "PROJECT_ROOT", root)
    path = root / "config" / "settings.json"

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def read():
        return json.loads(path.read_text(encoding="utf-8"))

    return root, write, read


def test_several_model_folders_and_other_folders_of_the_users_survive_a_settings_read(product_settings, tmp_path):
    """Every key whose name held 'model', 'dir', 'file'... was read as one path: the model folder
    list (folders joined with the path separator) and a folder chosen outside the install came back
    blank, and the next save wrote the blank."""
    from eli.core import runtime_settings as rs
    root, write, read = product_settings
    one, two = tmp_path / "models-a", tmp_path / "models-b"
    one.mkdir()
    two.mkdir()
    projects = tmp_path / "Pictures" / "projects"
    profiles = tmp_path / "gpu_profiles.json"
    joined = os.pathsep.join([str(one), str(two)])
    write({"model_dirs": joined, "image_default_project_path": str(projects),
           "gpu_profiles_file": str(profiles)})
    loaded = rs.load_settings()
    assert loaded["model_dirs"] == joined
    assert loaded["image_default_project_path"] == str(projects)
    assert loaded["gpu_profiles_file"] == str(profiles)
    from eli.core import model_folders
    third = tmp_path / "models-c"
    third.mkdir()
    assert model_folders.add_model_folder(third) is True
    assert read()["model_dirs"] == os.pathsep.join([str(one), str(two), str(third)])


def test_a_database_path_from_a_copied_install_still_moves_onto_this_one(product_settings, tmp_path):
    """Moved by the last of the install's own folders on the way: a folder named like one of them
    above the old install (here 'eli') is not where the install's part starts."""
    from eli.core import runtime_settings as rs
    root, write, _read = product_settings
    write({"user_db": str(tmp_path / "eli" / "old-machine" / "ELI" / "artifacts" / "db" / "user.sqlite3")})
    assert rs.load_settings()["user_db"] == str(root / "artifacts" / "db" / "user.sqlite3")


def test_a_model_chosen_in_another_programs_models_folder_keeps_its_path_after_a_restart(product_settings, tmp_path):
    """Saved as 'models/<name>' and looked for in ELI's own folder, it was gone at the next start."""
    from eli.core import runtime_settings as rs
    root, _write, read = product_settings
    other = tmp_path / "OtherApp" / "models" / "org"
    other.mkdir(parents=True)
    model = other / "Chosen.gguf"
    model.write_bytes(b"GGUF")
    rs.save_settings({"model_path": str(model)})
    assert read()["model_path"] == str(model)
    rs._HEAL_MISSES.clear()
    assert rs.load_settings()["model_path"] == str(model)


def test_a_model_that_moved_into_a_named_model_folder_is_found_there(product_settings, tmp_path):
    from eli.core import runtime_settings as rs
    root, write, read = product_settings
    named = tmp_path / "my-models"
    named.mkdir()
    (named / "Moved.gguf").write_bytes(b"GGUF")
    write({"model_dirs": str(named), "model_path": str(tmp_path / "old-place" / "Moved.gguf")})
    rs._HEAL_MISSES.clear()
    assert rs.load_settings()["model_path"] == str(named / "Moved.gguf")
    assert read()["model_path"] == str(named / "Moved.gguf"), "healed once, kept"


def test_two_threads_saving_different_keys_keep_both(product_settings):
    from eli.core import runtime_settings as rs
    _root, write, read = product_settings
    write({})
    keys = [f"probe_key_{i}" for i in range(16)]
    threads = [threading.Thread(target=rs.save_settings, args=({k: i},)) for i, k in enumerate(keys)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    saved = read()
    assert all(saved.get(k) == i for i, k in enumerate(keys))
