"""Where ELI looks for models (eli/core/model_folders.py): one list every scanner reads, which the
settings page shows and the user adds to, and which a model chosen from elsewhere joins."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from eli.core import model_folders as mf


@pytest.fixture
def settings(monkeypatch):
    """A settings store of this test's own, read and written the way the module does."""
    store: dict = {}
    import eli.core.runtime_settings as rs
    monkeypatch.setattr(rs, "load_settings", lambda *a, **k: dict(store))
    monkeypatch.setattr(rs, "save_settings", lambda delta: store.update(delta))
    monkeypatch.delenv(mf.ENV, raising=False)
    return store


@pytest.fixture
def own(tmp_path, monkeypatch):
    """ELI's own folders and no model loaded."""
    import eli.core.paths as paths
    mine = tmp_path / "mine"
    mine.mkdir()
    monkeypatch.setattr(paths, "models_dir", lambda: mine)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(paths, "project_root", lambda: tmp_path / "project")
    import eli.cognition.gguf_inference as gi
    monkeypatch.setattr(gi, "get_model_path", lambda: None)
    return mine


def test_the_folders_come_in_order_each_once_and_only_those_that_exist(own, settings, tmp_path, monkeypatch):
    kept = tmp_path / "kept"
    kept.mkdir()
    named = tmp_path / "named"
    named.mkdir()
    settings["model_path"] = str(kept / "Chosen.gguf")
    settings["vision_model_path"] = str(kept / "Seer.gguf")          # the same folder, once
    settings["image_model_path"] = str(tmp_path / "gone" / "sd")     # a folder that is not there
    settings[mf.SETTING] = os.pathsep.join([str(named), str(own)])    # ELI's own folder named again
    folders = mf.model_folders()
    assert folders == [own, kept, named]


def test_a_folder_named_in_the_environment_comes_before_the_setting_s(own, settings, tmp_path, monkeypatch):
    env_named = tmp_path / "env"
    env_named.mkdir()
    set_named = tmp_path / "set"
    set_named.mkdir()
    monkeypatch.setenv(mf.ENV, str(env_named))
    settings[mf.SETTING] = str(set_named)
    assert mf.named_model_folders() == [env_named, set_named]
    assert mf.model_folders() == [own, env_named, set_named]


def test_a_folder_is_added_once_and_forgotten_again(own, settings, tmp_path):
    new = tmp_path / "new"
    new.mkdir()
    assert mf.add_model_folder(new) is True
    assert mf.add_model_folder(new) is False
    assert settings[mf.SETTING] == str(new)
    assert mf.remove_model_folder(new) is True
    assert settings[mf.SETTING] == ""
    assert mf.remove_model_folder(new) is False
    with pytest.raises(NotADirectoryError):
        mf.add_model_folder(tmp_path / "missing")


def test_a_folder_named_by_the_environment_cannot_be_forgotten_here(own, settings, tmp_path, monkeypatch):
    env_named = tmp_path / "env"
    env_named.mkdir()
    monkeypatch.setenv(mf.ENV, str(env_named))
    assert mf.remove_model_folder(env_named) is False
    assert mf.add_model_folder(env_named) is False, "already named"


def test_a_model_chosen_from_an_unknown_folder_makes_its_folder_known(own, settings, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    model = elsewhere / "Other.gguf"
    model.write_bytes(b"x")
    assert mf.remember_folder_of(model) is True
    assert elsewhere in mf.model_folders()
    assert mf.remember_folder_of(model) is False, "known now"
    assert mf.remember_folder_of(own / "Mine.gguf") is False, "ELI's own folder was always known"


def test_chat_models_come_from_every_folder_told_apart_by_folder_and_include_the_loaded_one(
        own, settings, tmp_path, monkeypatch):
    import eli.core.hardware_profile as hardware
    named = tmp_path / "named"
    named.mkdir()
    settings[mf.SETTING] = str(named)
    for folder, name in ((own, "Small.gguf"), (own, "Big.gguf"), (named, "Small.gguf")):
        (folder / name).write_bytes(b"x")
    monkeypatch.setattr(hardware, "discover_models", lambda folder=None, **kw: [
        {"path": str(f)} for f in sorted(Path(folder).glob("*.gguf"))] if folder else [])
    loaded = tmp_path / "loaded" / "Current.gguf"
    loaded.parent.mkdir()
    loaded.write_bytes(b"x")
    found = mf.chat_models(current=loaded)
    assert found["Big.gguf"] == own / "Big.gguf"
    assert found["Small.gguf"] == own / "Small.gguf" and found["Small.gguf (named)"] == named / "Small.gguf"
    assert found["Current.gguf"] == loaded


def test_every_scanner_reads_the_one_list(own, settings, tmp_path, monkeypatch):
    """Vision, the GUI's scanner and the image engine look in the model folders too."""
    named = tmp_path / "named"
    named.mkdir()
    settings[mf.SETTING] = str(named)
    from eli.perception import vision
    assert named.resolve() in [p.resolve() for p in vision._candidate_model_dirs()]
    from eli.tools.image_engine.gui_bridge import discover_local_image_models
    weights = named / "image" / "sd"
    weights.mkdir(parents=True)
    (weights / "model_index.json").write_text("{}")
    assert weights.resolve() in [p.resolve() for p in discover_local_image_models()]


def test_the_api_lists_chat_models_from_every_folder(own, settings, tmp_path, monkeypatch):
    import eli.core.hardware_profile as hardware
    named = tmp_path / "named"
    named.mkdir()
    settings[mf.SETTING] = str(named)
    (named / "Other.gguf").write_bytes(b"x" * 10)
    monkeypatch.setattr(hardware, "discover_models", lambda folder=None, **kw: [
        {"path": str(f)} for f in sorted(Path(folder).glob("*.gguf"))] if folder else [])
    from api.server import _list_chat_models
    listed = {m["name"]: m for m in _list_chat_models()}
    assert listed["Other.gguf"]["path"] == str(named / "Other.gguf") and listed["Other.gguf"]["size_gb"] == 0.0
