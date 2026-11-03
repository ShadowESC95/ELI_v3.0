"""ELI's own models (eli/world/models.py): brought in from a file on this computer or fetched by
what they are from a library, kept in a store of their own, listed for a body, and placed in the
rooms. The library and the network are stand-ins; the store is a temporary folder."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eli.world import models

LIBRARY = {
    "pine_tree_01": {"name": "Pine Tree 01", "tags": ["tree", "conifer", "forest"], "categories": ["nature", "trees"],
                     "dimensions": [3200.0, 3100.0, 9800.0], "download_count": 40000, "authors": {"A Person": "All"}},
    "rock_moss_set_01": {"name": "Rock Moss Set 01", "tags": ["rock", "moss", "stone"], "categories": ["nature", "rocks"],
                         "dimensions": [1200.0, 900.0, 600.0], "download_count": 90000},
    "wooden_bench_02": {"name": "Wooden Bench 02", "tags": ["bench", "seat", "wood"], "categories": ["furniture", "seating"],
                        "dimensions": [1800.0, 600.0, 900.0], "download_count": 5000},
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "store", lambda: tmp_path / "models")
    return tmp_path / "models"


def _gltf(folder: Path, name: str = "lamp") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "textures").mkdir(exist_ok=True)
    (folder / f"{name}.bin").write_bytes(b"\0" * 8)
    (folder / "textures" / f"{name}_diff.png").write_bytes(b"\x89PNG")
    path = folder / f"{name}.gltf"
    path.write_text(json.dumps({"buffers": [{"uri": f"{name}.bin"}],
                                "images": [{"uri": f"textures/{name}_diff.png"}, {"uri": "data:image/png;base64,AA=="},
                                           {"uri": "../outside.png"}]}))
    return path


def test_a_file_on_this_computer_is_brought_in_with_the_files_it_names(store, tmp_path):
    got = models.import_file(str(_gltf(tmp_path / "things")), name="Desk lamp")
    assert got["id"] == "Desk_lamp" and got["name"] == "desk lamp"
    assert sorted(got["files"]) == ["lamp.bin", "lamp.gltf", "textures/lamp_diff.png"], "data uris and ../ are left out"
    assert (store / "Desk_lamp" / "textures" / "lamp_diff.png").read_bytes() == b"\x89PNG"
    assert models.listed()[0]["size"] == [1, 1, 1] and not got["measured"], "a body measures it"
    assert models.measured("Desk_lamp", [0.3, 0.52, 0.3]) and models.listed()[0]["size"] == [0.3, 0.52, 0.3]
    assert models.file_path("Desk_lamp", "textures/lamp_diff.png") is not None
    assert models.file_path("Desk_lamp", "../models.json") is None, "only listed files are served"
    assert models.file_path("Desk_lamp", "lamp.gltf.import") is None
    second = models.import_file(str(_gltf(tmp_path / "more")), name="Desk lamp")
    assert second["id"] == "Desk_lamp_2", "two of a name do not collide"


def test_a_file_that_is_not_a_model_or_lacks_what_it_names_is_refused(store, tmp_path):
    with pytest.raises(ValueError):
        models.import_file(str(tmp_path / "notes.txt"))
    path = _gltf(tmp_path / "broken")
    (tmp_path / "broken" / "lamp.bin").unlink()
    with pytest.raises(FileNotFoundError):
        models.import_file(str(path))


def test_the_librarys_best_match_is_by_every_word_then_by_how_wanted_it_is():
    assert models.pick("a pine tree", LIBRARY)[0] == "pine_tree_01"
    assert models.pick("some rocks", LIBRARY)[0] == "rock_moss_set_01"
    assert models.pick("a bench", LIBRARY)[0] == "wooden_bench_02"
    assert models.pick("a stone bench", LIBRARY) is None, "every word must match one thing"
    assert models.pick("a model of a", LIBRARY) is None


def test_a_thing_is_fetched_by_what_it_is_sized_from_the_library_and_then_had(store, monkeypatch):
    urls = []

    def fake_get(url, max_bytes=0):
        urls.append(url)
        if url.endswith("/assets?t=models"):
            return json.dumps(LIBRARY).encode()
        if url.endswith("/files/pine_tree_01"):
            return json.dumps({"gltf": {"1k": {"gltf": {"url": "https://dl.example/pine_tree_01_1k.gltf", "include": {
                "pine_tree_01.bin": {"url": "https://dl.example/pine_tree_01.bin"},
                "textures/pine_tree_01_diff_1k.jpg": {"url": "https://dl.example/t.jpg"},
                "../escape.bin": {"url": "https://dl.example/escape.bin"}}}}}}).encode()
        return b"bytes of " + url.encode()

    monkeypatch.setattr(models, "_get", fake_get)
    monkeypatch.setattr("eli.core.netguard.should_block_network", lambda: False)
    got = models.fetch("a pine tree")
    assert got["ok"] and got["id"] == "pine_tree_01" and got["name"] == "pine tree" and not got["had"]
    assert got["size"] == [3.2, 9.8, 3.1], "millimetres with z up become metres [wide, tall, deep]"
    files = models.listed()[0]["files"]
    assert files == ["pine_tree_01_1k.gltf", "pine_tree_01.bin", "textures/pine_tree_01_diff_1k.jpg"]
    assert not (store / "pine_tree_01" / ".." / "escape.bin").exists() and "escape" not in " ".join(urls)
    assert models.listed()[0]["licence"] == "CC0 1.0" and models.listed()[0]["authors"] == ["A Person"]
    again = models.fetch("pine tree")
    assert again["ok"] and again["had"], "the second time it is the one there is"
    assert len([u for u in urls if "dl.example" in u]) == 3, "fetched once"
    assert models.props()[0] == {"id": "pine_tree_01", "name": "pine tree", "size": [3.2, 9.8, 3.1]}


def test_with_the_net_off_nothing_is_fetched_and_it_says_so(store, monkeypatch):
    monkeypatch.setattr("eli.core.netguard.should_block_network", lambda: True)
    got = models.fetch("a pine tree")
    assert not got["ok"] and got["offline"] and "Net is off" in got["reason"]
    assert models.listed() == []


def test_nothing_the_library_lacks_is_fetched(store, monkeypatch):
    monkeypatch.setattr(models, "_get", lambda url, max_bytes=0: json.dumps(LIBRARY).encode())
    monkeypatch.setattr("eli.core.netguard.should_block_network", lambda: False)
    got = models.fetch("a spaceship")
    assert not got["ok"] and "nothing called" in got["reason"]


def test_a_sentence_asking_for_a_model_is_routed():
    from eli.execution.routing.intents.world_models import route_world_models
    for said, action, key, want in (
        ("import the model at ~/things/lamp.glb", "IMPORT_MODEL", "path", "things/lamp.glb"),
        ("import ~/things/chair.gltf as a garden chair", "IMPORT_MODEL", "name", "garden chair"),
        ("fetch a model of a pine tree", "FETCH_MODEL", "query", "pine tree"),
        ("get me a rocking chair model for your room", "FETCH_MODEL", "query", "rocking chair"),
        ("can you download a 3d model of an old radio", "FETCH_MODEL", "query", "old radio"),
    ):
        got = route_world_models(said, said, said.lower(), said.lower())
        assert got and got["action"] == action, said
        assert want in str(got["args"][key]), (said, got["args"])
    for said in ("fetch the news", "get me a coffee", "import the csv"):
        assert route_world_models(said, said, said.lower(), said.lower()) is None, said


def test_the_actions_bring_a_model_in_and_say_so(store, tmp_path, monkeypatch):
    from eli.execution.effectors.world_models import _h_fetch_model, _h_import_model
    got = _h_import_model("IMPORT_MODEL", {"path": str(_gltf(tmp_path / "things"))})
    assert got["ok"] and got["model"] == "lamp" and "Imported lamp" in got["response"]
    bad = _h_import_model("IMPORT_MODEL", {"path": str(tmp_path / "gone.glb")})
    assert not bad["ok"] and "couldn't import" in bad["response"]
    monkeypatch.setattr(models, "fetch", lambda want: {"ok": False, "offline": True, "reason": "the Net is off"})
    off = _h_fetch_model("FETCH_MODEL", {"query": "a pine tree"})
    assert not off["ok"] and off["offline"] and "network access is off" in off["response"]
    monkeypatch.setattr(models, "fetch", lambda want: {"ok": True, "id": "pine_tree_01", "name": "pine tree",
                                                        "size": [3.2, 9.8, 3.1], "had": False})
    on = _h_fetch_model("FETCH_MODEL", {"query": "a pine tree"})
    assert on["ok"] and "Fetched pine tree (pine_tree_01, 9.8 m tall, CC0 1.0 from Poly Haven)" in on["response"]


def test_the_grammar_and_prompt_offer_fetching_only_with_the_net_on():
    from eli.world import composer
    with_net = composer.grammar_text(True, ("create",), (), fetch=True)
    assert "brought ::=" in with_net and '\\"fetch\\":' in with_net
    assert "brought" not in composer.grammar_text(True, ("create",), (), fetch=False)
    assert "library of free models" in composer.build_prompt(None, {}, {}, "own_room", ("create",), None, False, (), [], True)
    assert "library of free models" not in composer.build_prompt(None, {}, {}, "own_room", ("create",), None, False, (), [], False)


def test_a_create_that_fetches_is_placed_as_the_model_brought_in(store, monkeypatch):
    from eli.world import building
    clean, refused = building.normalise([{"op": "create", "name": "a tree", "fetch": "pine tree", "at": [1, 2],
                                          "meaning": "shade"}])
    assert not refused and clean[0]["fetch"] == "pine tree" and clean[0]["shape"] == building.PROP
    monkeypatch.setattr(models, "fetch", lambda want: {"ok": True, "id": "pine_tree_01", "name": "pine tree",
                                                        "size": [3.2, 9.8, 3.1], "had": False})
    from eli.world.core.schemas import WorldEvent
    from eli.world.persistence.storage import EliWorldStorage
    state = EliWorldStorage(state_path=store.parent / "world.json").load()
    event = WorldEvent(event_type="world_building", source="test", summary="a tree", payload={"actor": "eli"})
    done = building._carry_out(state, dict(clean[0]), event)
    assert done["ok"] and state.objects[done["id"]].metadata["form"]["prop"] == "pine_tree_01"
    monkeypatch.setattr(models, "fetch", lambda want: {"ok": False, "reason": "the Net is off"})
    refused_edit = building._carry_out(state, dict(clean[0]), event)
    assert not refused_edit["ok"] and "could not bring in 'pine tree'" in refused_edit["reason"]
