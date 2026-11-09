"""ELI's building (eli/world/building.py), what ELI makes from words
(eli/world/composer.py) and what ELI knows of where it is (eli/world/embodiment.py).

Everything runs on a world in a temporary folder with the journal and the ledger
turned aside, and the model is a stand-in: no real world state is read or
written, and no model is loaded.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eli.world import building, composer, embodiment, local_world_bridge
from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
from eli.world.agency.policy import EliWorldPolicy
from eli.world.core.schemas import WorldAction, WorldActionType
from eli.world.persistence import storage as world_storage
from eli.world.persistence.storage import EliWorldStorage


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fresh world of its own. Gives a function that reads it back."""
    monkeypatch.setattr(world_storage, "world_dir", lambda: tmp_path)
    monkeypatch.setattr(world_storage, "WORLD_DIR", tmp_path)
    monkeypatch.setattr(world_storage, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(world_storage, "ACTIONS_PATH", tmp_path / "actions.jsonl")
    store = EliWorldStorage(state_path=tmp_path / "world.json")
    monkeypatch.setattr(local_world_bridge, "_engine_instance", EliWorldAutonomyEngine(storage=store))
    monkeypatch.setattr(local_world_bridge, "load_persona_text", lambda: "")
    monkeypatch.setattr(composer, "mindset", lambda state: ["Your latest insight: quiet work goes further."])
    monkeypatch.setattr(composer, "capable", lambda: False)
    import eli.cognition.thoughts as thoughts_module
    monkeypatch.setattr(thoughts_module, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")   # no thought of the real ELI's is a moment here
    embodiment.forget()
    embodiment.forget_body()
    yield store.load
    embodiment.forget()
    embodiment.forget_body()


def _standing(state, room=building.OWN_ROOM):
    return {o.name: o for o in state.objects.values() if o.room == room and not o.retired}


# ── reading what was asked ───────────────────────────────────────────────────
def test_a_loosely_worded_edit_is_put_right_not_refused():
    clean, refused = building.normalise([
        {"action": "build", "kind": "a tall stone column", "color": "Gray", "scale": 99,
         "position": {"x": 40, "z": 30}, "glow": 7, "why": "something to lean on"},
    ])
    assert refused == []
    edit = clean[0]
    assert (edit["op"], edit["shape"], edit["colour"]) == ("create", "pillar", "#8a8f98")
    assert edit["size"] == building.MAX_SIZE and edit["glow"] == 1.0
    assert edit["meaning"] == "something to lean on" and edit["name"] == "Pillar"
    right, forward = edit["at"]
    assert round((right ** 2 + forward ** 2) ** 0.5, 1) == building.REACH, "held inside the room, same direction"
    assert round(right / forward, 2) == round(40 / 30, 2)


def test_what_cannot_be_understood_comes_back_with_the_reason():
    clean, refused = building.normalise([
        {"op": "summon"}, "not an edit", {"op": "move", "name": "Lantern"},
        {"op": "remove"}, {"op": "room"}, {"op": "note", "text": "  "},
    ])
    assert clean == []
    assert [r["index"] for r in refused] == [0, 1, 2, 3, 4, 5]
    assert "unknown op" in refused[0]["reason"] and "somewhere to move to" in refused[2]["reason"]
    many, over = building.normalise([{"op": "create", "shape": "orb"}] * (building.MAX_EDITS + 3))
    assert len(many) == building.MAX_EDITS and len(over) == 3


# ── making, moving, changing, taking away ────────────────────────────────────
def test_eli_makes_a_thing_and_the_world_keeps_how_it_looks_and_why(world):
    out = building.apply(
        [{"op": "create", "name": "Lantern", "shape": "lamp", "colour": "amber", "at": [1, 3],
          "glow": 0.8, "meaning": "a light left on"}],
        actor="eli", asked=True, summary="build me a lantern")
    assert out["ok"] and out["results"][0]["name"] == "Lantern" and out["refused"] == []
    state = world()
    lantern = _standing(state)["Lantern"]
    assert lantern.object_type == building.BUILT and lantern.created_by == "eli"
    assert lantern.symbolic_meaning == "a light left on" and lantern.reason == "build me a lantern"
    form = building.form_of(lantern.__dict__)
    assert (form["shape"], form["colour"], form["pos"], form["glow"]) == ("lamp", "#f5b942", [1.0, 0.0, 3.0], 0.8)
    assert state.avatar.room == building.OWN_ROOM and state.avatar.activity == "building", "ELI went there to do it"
    assert building.OWN_ROOM not in state.rooms, "the house stays the nine rooms"
    assert state.spaces[building.OWN_ROOM]["name"] == "ELI's Room"


def test_every_edit_is_a_world_action_judged_by_the_policy_and_kept(world):
    building.apply([{"op": "create", "name": "Stone", "shape": "block"}, {"op": "note", "text": "first stone"}],
                   actor="eli", asked=False, summary="ELI built of its own accord")
    built = [a for a in world().actions if a.payload.get("built")]
    assert [a.action_type for a in built] == ["create_object", "journal_entry"]
    assert all(a.permission_class == "world_safe" and a.provenance_id for a in built)
    assert all(a.actor == "eli" and a.reason == "ELI chose to." for a in built)
    # what a thing is called is not part of the action the policy reads
    assert all("Stone" not in str(a.payload) and "first stone" not in str(a.payload) for a in built)


def test_a_name_is_not_an_action(world):
    """The policy blocks the word "cloud" in an action. A thing may still be called one."""
    out = building.apply([{"op": "create", "name": "Network of clouds", "shape": "orb",
                           "meaning": "an installation about telemetry"}], actor="eli", asked=True)
    assert out["ok"] and "Network of clouds" in _standing(world())


def test_changing_the_look_of_a_thing_is_a_free_action_of_the_world():
    action = WorldAction(action_type=WorldActionType.ALTER_OBJECT.value, actor="eli",
                         room=building.OWN_ROOM, reason="ELI chose to.", payload={"built": True, "edit": 0})
    assert EliWorldPolicy().allowed(action)


def test_a_thing_is_moved_changed_and_taken_away_by_name(world):
    building.apply([{"op": "create", "name": "Quiet Stone", "shape": "pillar", "at": [0, 2]}], actor="eli")
    out = building.apply([
        {"op": "move", "name": "quiet stone", "at": [-2, 4], "height": 1.5},
        {"op": "change", "name": "stone", "colour": "teal", "size": 2.0, "rename": "Loud Stone"},
    ], actor="user", asked=True)
    assert [r["ok"] for r in out["results"]] == [True, True]
    stone = _standing(world())["Loud Stone"]
    form = building.form_of(stone.__dict__)
    assert form["pos"] == [-2.0, 1.5, 4.0] and form["colour"] == "#2fb8a8" and form["size"] == 2.0
    out = building.apply([{"op": "remove", "name": "Loud Stone"}], actor="user", asked=True)
    assert out["ok"] and _standing(world()) == {}
    assert any(o.retired for o in world().objects.values()), "taken away, not destroyed"


def test_a_new_thing_does_not_stand_inside_another(world):
    building.apply([{"op": "create", "name": f"Post {i}", "shape": "pillar", "size": 1.0, "at": [0, 2]}
                    for i in range(4)], actor="eli")
    spots = [building.form_of(o.__dict__)["pos"] for o in _standing(world()).values()]
    assert len(spots) == 4
    for i, a in enumerate(spots):
        for b in spots[i + 1:]:
            assert ((a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5 >= 1.0


def test_what_cannot_be_done_says_so(world):
    building.apply([{"op": "create", "name": "Lantern", "shape": "lamp"}], actor="eli")
    out = building.apply([
        {"op": "remove", "name": "Throne"},
        {"op": "create", "name": "Bench", "shape": "seat", "room": "the_moon"},
        {"op": "room", "room": "core_room", "sky": "dawn"},
    ], actor="eli", asked=True)
    assert not out["ok"]
    reasons = [r["reason"] for r in out["results"]]
    assert "there is no 'Throne' here" in reasons[0] and "no room 'the_moon'" in reasons[1]
    assert "keeps its own sky, ground and air" in reasons[2]
    assert "Lantern" in _standing(world())


def test_the_things_the_world_makes_itself_are_not_the_buildings_to_remove(world):
    from eli.world.core.schemas import WorldEvent
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("task_opened", "test", "Task opened: A", {"goal_id": "ga", "title": "Paper"}))
    out = building.apply([{"op": "remove", "name": "Paper"}], actor="user", asked=True)
    assert not out["ok"] and "belongs to the world itself" in out["results"][0]["reason"]
    # but it has a look and a steady place like anything else, and may be moved
    paper = next(o for o in world().objects.values() if o.name == "Paper")
    assert building.form_of(paper.__dict__)["shape"] == "table"
    assert building.form_of(paper.__dict__)["pos"] == building.form_of(paper.__dict__)["pos"]
    assert building.apply([{"op": "move", "name": "Paper", "at": [2, 2]}], actor="user")["ok"]


def test_a_room_fills_up(world, monkeypatch):
    monkeypatch.setattr(building, "MAX_PER_ROOM", 3)
    out = building.apply([{"op": "create", "name": f"Orb {i}", "shape": "orb"} for i in range(5)], actor="eli")
    assert [r["ok"] for r in out["results"]] == [True, True, True, False, False]
    assert "the room is full" in out["results"][3]["reason"]


# ── ELI's own room ───────────────────────────────────────────────────────────
def test_his_own_room_takes_a_sky_a_ground_a_light_and_mist(world):
    assert building.mood_of(world().spaces) == building.DEFAULT_MOOD
    out = building.apply([{"op": "room", "sky": "morning", "ground": "grass", "light": "gold", "mist": 3}],
                         actor="eli", asked=True)
    assert out["ok"]
    assert building.mood_of(world().spaces) == {"sky": "dawn", "ground": "moss", "light": "#e2b44a", "mist": 1.0}


def test_a_room_of_the_house_takes_a_light_and_keeps_its_own_sky_and_air(world):
    assert building.look_of(world().rooms, "workshop") == {}, "as it was built"
    out = building.apply([{"op": "room", "room": "workshop", "light": "gold", "mist": 0.4}], actor="eli")
    assert out["ok"] and [a.room for a in world().actions if a.payload.get("built")] == ["workshop"]
    assert building.look_of(world().rooms, "workshop") == {"light": "#e2b44a"}, "the light, and no mist"
    assert building.mood_of(world().spaces) == building.DEFAULT_MOOD, "ELI's own room is not touched"
    out = building.apply([{"op": "room", "room": "workshop", "mist": 0.9}], actor="eli")
    assert not out["ok"] and "only its light can change" in out["results"][0]["reason"]
    building.apply([{"op": "room", "room": "workshop", "light": "#ff5a36"}], actor="eli")
    assert building.look_of(world().rooms, "workshop") == {"light": "#ff5a36"}
    building.apply([{"op": "undo"}], actor="user", asked=True)
    assert building.look_of(world().rooms, "workshop") == {"light": "#e2b44a"}
    building.apply([{"op": "undo"}], actor="user", asked=True)
    assert building.look_of(world().rooms, "workshop") == {}, "taken back to the room as it was built"


# ── taking it back ───────────────────────────────────────────────────────────
def test_undo_takes_back_the_last_batch_and_then_the_one_before(world):
    building.apply([{"op": "create", "name": "First", "shape": "orb", "at": [1, 1]}], actor="eli")
    building.apply([{"op": "create", "name": "Second", "shape": "cone"},
                    {"op": "change", "name": "First", "colour": "red"},
                    {"op": "room", "sky": "sunset"}], actor="eli")
    assert set(_standing(world())) == {"First", "Second"}

    assert building.apply([{"op": "undo"}], actor="user")["ok"]
    state = world()
    assert set(_standing(state)) == {"First"}
    assert building.form_of(_standing(state)["First"].__dict__)["colour"] == "#9fd8ff"
    assert building.mood_of(state.spaces)["sky"] == "stars"

    assert building.apply([{"op": "undo"}], actor="user")["ok"]
    assert _standing(world()) == {}
    out = building.apply([{"op": "undo"}], actor="user")
    assert not out["ok"] and out["refused"][-1]["reason"] == "nothing to undo"


def test_a_thing_taken_away_comes_back_when_that_is_undone(world):
    building.apply([{"op": "create", "name": "Lantern", "shape": "lamp", "at": [2, 2]}], actor="eli")
    building.apply([{"op": "remove", "name": "Lantern"}], actor="user")
    assert _standing(world()) == {}
    building.apply([{"op": "undo"}], actor="user")
    assert building.form_of(_standing(world())["Lantern"].__dict__)["pos"] == [2.0, 0.0, 2.0]


# ── where ELI is ─────────────────────────────────────────────────────────────
SCENE = {
    "surface": "eli_vr", "session": "s1", "room": "Core Room", "room_name": "Core Room",
    "seen_as": "An open deck on a station in orbit, Earth below.", "headset": True, "screen": True,
    "user": {"pos": [1.0, 1.7, -1.0], "facing": [0.0, 2.0], "looking_at": {"kind": "object", "name": "Lantern"}},
    "eli": {"visible": True, "distance": 2.44},
    "things": [{"name": "Lantern", "kind": "lamp", "distance": 1.26}, {"kind": "nameless"}],
    "nonsense": "x" * 50,
}


def test_nobody_there_means_nothing_is_said_about_it():
    embodiment.forget()
    assert embodiment.live() is None and embodiment.context_block() == ""


def test_while_someone_is_there_eli_is_told_plainly_where_and_what():
    embodiment.forget()
    kept = embodiment.report(SCENE)
    assert kept["room"] == "core_room" and "nonsense" not in kept and len(kept["things"]) == 1
    block = embodiment.context_block()
    assert block.startswith("[WHERE YOU ARE: live, from ELI VR]")
    for said in ("wearing a headset", "standing in the Core Room", "An open deck on a station in orbit",
                 "about 2.4 m from them", "They are looking at: Lantern.", "Lantern (lamp, 1.3 m)",
                 "Their desktop is showing", "do not invent other objects"):
        assert said in block, said
    assert embodiment.near_user(kept) == (1.0, 1.2), "a little in front of where they face"
    embodiment.forget()


def test_a_scene_goes_stale_and_a_leaving_client_is_forgotten(monkeypatch):
    embodiment.forget()
    embodiment.report({**SCENE, "eli": {"visible": False}})
    assert "You are not in that room" in embodiment.context_block()
    clock = embodiment.time.monotonic()
    monkeypatch.setattr(embodiment.time, "monotonic", lambda: clock + embodiment.FRESH_SECONDS + 1)
    assert embodiment.context_block() == ""
    for bad in ("a string", {"seen_as": "x" * 5000}):
        with pytest.raises(ValueError):
            embodiment.report(bad)


# ── from words ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("said", [
    "build me a fountain over there", "can you put a lamp next to the pillar", "make the sky dawn",
    "paint that crystal red", "undo", "get rid of the arch", "plant a tree here", "change the ground to sand",
    "please take that back", "could you move the lamp behind me",
])
def test_a_request_about_the_place_is_heard_as_one(said):
    assert composer.wants_building(said)


@pytest.mark.parametrize("said", [
    "how are you today", "what is the capital of France", "make me a list of groceries",
    "build a script that renames my files", "where are we", "tell me about the stars",
    "what do you make of this place", "how did you build that pillar", "why is the sky dark",
])
def test_ordinary_talk_is_not(said):
    assert not composer.wants_building(said)


def test_the_model_is_asked_in_plain_words_any_model_can_read(world):
    building.apply([{"op": "create", "name": "Lantern", "shape": "lamp", "colour": "amber", "at": [1, 3]}], actor="eli")
    embodiment.report({**SCENE, "room": building.OWN_ROOM, "room_name": "ELI's Room"})
    asked = []

    def model(prompt):
        asked.append(prompt)
        return {"say": "There. A tree beside the lantern.",
                "edits": [{"op": "create", "name": "Young Tree", "shape": "tree", "colour": "green"}]}

    out = composer.compose("plant a tree by the lantern", embodiment.live(), generate=model)
    prompt = asked[0]
    for part in ("in ELI's Room, which is your own room", '- "Lantern": lamp, #f5b942', "[1.0, 3.0]",
                 "The user stands at [1.0, -1.0]", 'The user said: "plant a tree by the lantern"',
                 "Shapes: ", "To make a new thing: its name", "Use as few edits as do the job."):
        assert part in prompt, part
    listed = prompt.split("Shapes: ")[1].split(".")[0].split(", ")
    assert sorted(listed) == sorted(building.SHAPES), "every shape is named, in no order that is a choice"
    assert '{"op"' not in prompt, "no example to copy: the grammar holds the shape"
    assert "To take a thing away" not in prompt, "a sentence that makes a thing is asked only for that"
    whole = composer.build_prompt("tidy this up", embodiment.live(), world().to_dict(), building.OWN_ROOM)
    for part in ("To change the room itself (now sky stars", "sky (stars, dawn, mist",
                 "To take a thing away: its name.", "To move a thing: its name"):
        assert part in whole, part
    assert out["kind"] == "build" and out["text"] == "There. A tree beside the lantern."
    assert out["results"][0]["ok"] and "Young Tree" in _standing(world())
    tree = building.form_of(_standing(world())["Young Tree"].__dict__)
    assert tree["pos"][1] == 0.0 and (tree["pos"][0], tree["pos"][2]) != (1.0, 3.0), "placed near the user, clear of the lantern"


def test_in_another_room_the_sky_is_not_his_to_offer(world):
    prompt = composer.build_prompt("add a lamp", {**embodiment._clean(SCENE)}, world().to_dict(), "core_room")
    assert "in the Core Room." in prompt and "the room itself" not in prompt and "sky (" not in prompt


def test_of_his_own_accord_nothing_is_chosen_for_him(world):
    asked = []

    def model(prompt):
        asked.append(prompt)
        return {"say": "", "edits": {"op": "create", "name": "Still Water", "shape": "pool",
                                     "at": [3.0, 4.0], "meaning": "nothing pressing"}}

    out = composer.compose(None, embodiment._clean(SCENE), generate=model)
    prompt = asked[0]
    assert "Nobody has asked for anything" in prompt and "Your latest insight: quiet work goes further." in prompt
    assert "If not, leave the edits empty." in prompt and "This time" not in prompt
    assert "the whole house is yours" not in prompt, "what it makes when the user asks it to is made here"
    assert out["room"] == building.OWN_ROOM and not out["asked"] and out["text"] == ""
    pool = _standing(world())["Still Water"]
    assert pool.symbolic_meaning == "nothing pressing" and pool.reason == "ELI built of its own accord"
    assert building.form_of(pool.__dict__)["pos"] == [3.0, 0.0, 4.0], "where ELI put it, not where a rule would"
    assert [a.reason for a in world().actions if a.payload.get("built")] == ["ELI chose to."]
    # and ELI may make nothing at all
    out = composer.compose(None, None, generate=lambda p: {"say": "", "edits": []})
    assert out["results"] == [] and out["text"] == "" and set(_standing(world())) == {"Still Water"}
    assert composer.plan(None, [], True) == ("create", "room", "note")
    assert composer.plan(None, [{"name": "Still Water", "form": {"shape": "pool"}}], True) == (
        "create", "move", "change", "remove", "room", "note"), "unasked, every kind of change is open to ELI, and a note"


def test_if_it_did_not_happen_he_does_not_say_it_did(world):
    # nothing is here, so nothing can be taken away, whatever the model answers and claims
    out = composer.compose("remove the throne", None,
                           generate=lambda p: {"say": "Gone.", "edits": [{"op": "remove", "name": "Throne"}]})
    assert out["text"] == "I have not changed anything." and out["results"] == []
    building.apply([{"op": "create", "name": "Lantern", "shape": "lamp"}], actor="eli")
    out = composer.compose("remove the lantern and the throne", None,
                           generate=lambda p: {"say": "The throne is gone.", "edits": [{"op": "remove", "name": "Throne"}]})
    assert out["text"] == "The throne is gone. One part did not happen: there is no 'Throne' here."
    assert "Lantern" not in _standing(world()), "the one that was there and was asked for is taken away"
    out = composer.compose("get rid of that", None, generate=lambda p: {"say": "All gone.", "edits": []})
    assert out["text"] == "I have not changed anything."


def test_a_model_that_is_not_there_or_answers_badly_is_told_apart(world):
    def gone(prompt):
        raise RuntimeError("GGUF model not available")

    with pytest.raises(composer.ComposeError) as failure:
        composer.compose("build a lamp", None, generate=gone)
    assert failure.value.kind == "model_unavailable"
    with pytest.raises(composer.ComposeError) as failure:
        composer.compose("build a lamp", None, generate=lambda p: ["not", "an", "object"])
    assert failure.value.kind == "bad_output"
    out = composer.compose("build a lamp", None, generate=lambda p: {"say": "Where shall I put it?", "edits": "none"})
    assert out["text"] == "Where shall I put it?" and out["results"] == [] and _standing(world()) == {}


# ── holding a small model to it ──────────────────────────────────────────────
def test_the_answer_is_held_to_an_exact_shape_that_llama_cpp_accepts():
    text = composer.grammar_text(own=True, targets=["Lantern", "Quiet Stone", 'bad"name'])
    assert text.startswith('root ::= "{" ws "\\"say\\":" ws say')
    for shape in building.SHAPES:
        assert f'"\\"{shape}\\""' in text
    assert "edit ::= create | move | change | remove | room | note | undo" in text
    assert 'target ::= "\\"Lantern\\"" | "\\"Quiet Stone\\""' in text, "only what is there can be moved or changed"
    empty = composer.grammar_text(own=True)
    assert "edit ::= create | room | note | undo" in empty, "with nothing there, nothing can be moved or taken away"
    house = composer.grammar_text(own=False)
    assert "room" in house.split("edit ::=")[1].split("\n")[0]
    room_rule = [line for line in house.splitlines() if line.startswith("room ::=")][0]
    assert "light" in room_rule and "mist" not in room_rule and "sky" not in room_rule and "ground" not in room_rule, \
        "a room of the house can be given a light, never a sky, a ground or a mist"
    anywhere = composer.grammar_text(own=True, rooms=["workshop", "memory_archive", 'bad"room'])
    assert 'place ::= "\\"own_room\\"" | "\\"workshop\\"" | "\\"memory_archive\\""' in anywhere
    assert '(",\\"room\\":" place)?' in anywhere and '"{\\"op\\":\\"room\\",\\"room\\":" house' in anywhere
    alone = composer.grammar_text(own=True, ops=("change",), targets=["Lantern"], one=True)
    assert "edit ::= change" in alone and '"[" ws edit ws "]"' in alone, "unasked: exactly one"
    assert "{1," not in composer.grammar_text(bounded=False)
    asked = composer.grammar_text(own=True, some=True)
    assert '"[" ws edit ("," ws edit)* ws "]"' in asked, "asked for something: at least one edit"
    plain, free = composer.grammar_text(own=True), composer.grammar_text(own=True, free=True)
    assert "create ::= shaped\n" in plain and "create ::= shaped | made" in free
    assert "sky_top" in free and "sky_top" not in plain, "a mind large enough may paint the sky"
    try:
        from llama_cpp import LlamaGrammar
        for grammar in (text, house, anywhere, composer.grammar_text(own=True, rooms=["workshop"], free=True)):
            LlamaGrammar.from_string(grammar, verbose=False)
    except Exception as unavailable:  # mocked or absent here
        pytest.skip(f"llama.cpp not usable in this lane ({type(unavailable).__name__})")


def test_undo_is_done_without_asking_a_model(world):
    building.apply([{"op": "create", "name": "Lantern", "shape": "lamp"}], actor="eli")
    never = lambda prompt: pytest.fail("there is nothing for a model to decide")  # noqa: E731
    for said in ("undo", "Undo that.", "please take that back"):
        building.apply([{"op": "create", "name": "Extra", "shape": "orb"}], actor="eli")
        out = composer.compose(said, None, generate=never)
        assert out["text"] == "Taken back." and "Extra" not in _standing(world())
    composer.compose("undo", None, generate=never)
    out = composer.compose("undo", None, generate=never)
    assert out["text"] == "There is nothing to take back."


def test_what_the_user_named_outright_is_what_the_thing_gets(world):
    wrong = {"say": "", "edits": [{"op": "create", "name": "orb", "shape": "pillar", "colour": "teal"}]}
    out = composer.compose("put a glowing blue orb here and make the sky dawn", None, generate=lambda p: dict(wrong))
    state = world()
    orb = building.form_of(_standing(state)["orb"].__dict__)
    assert (orb["shape"], orb["colour"]) == ("orb", "#4c7df0")
    assert building.mood_of(state.spaces)["sky"] == "dawn", "asked for and left out: added"
    assert out["text"] == "Done.", "ELI says something when it did something"
    # two colours, or two things: nothing is guessed
    both = {"say": "ok", "edits": [{"op": "create", "name": "a", "shape": "cone", "colour": "pink"},
                                   {"op": "create", "name": "b", "shape": "cone", "colour": "pink"}]}
    composer.compose("a red cone and a green cone", None, generate=lambda p: both)
    assert {building.form_of(o.__dict__)["colour"] for n, o in _standing(world()).items() if n in "ab"} == {"#f078b4"}
    composer.compose("could you make the ground ice", None, generate=lambda p: {"say": "ok", "edits": []})
    assert building.mood_of(world().spaces)["ground"] == "snow"


def test_one_bad_answer_is_asked_again(world):
    answers = iter([RuntimeError("Failed to generate JSON: Invalid control character"),
                    {"say": "A lamp.", "edits": [{"op": "create", "name": "Lamp", "shape": "lamp"}]}])

    def flaky(prompt):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert composer.compose("build a lamp", None, generate=flaky)["results"][0]["ok"]

    def broken(prompt):
        raise RuntimeError("Failed to generate JSON: Extra data")

    with pytest.raises(composer.ComposeError) as failure:
        composer.compose("build a lamp", None, generate=broken)
    assert failure.value.kind == "failed"


def test_a_sentence_about_a_thing_that_is_there_cannot_make_a_new_one():
    lantern = [{"name": "Lantern", "form": {"shape": "lamp"}, "built": True}]
    assert "create" not in composer.plan("make the lantern green and bigger", lantern, True)
    assert "create" not in composer.plan("move it behind me", lantern, True)
    assert composer.plan("put another lamp beside the lantern", lantern, True) == ("create",)
    assert composer.plan("put a glowing blue orb next to the lantern and make the sky dawn", lantern, True) == ("create",)
    assert composer.plan("make the lamp brighter", lantern, True) == ("change",), "the lamp is the Lantern"
    assert composer.plan("make the lantern green and bigger", lantern, True) == ("change",)
    assert composer.plan("move it behind me", lantern, True) == ("move",)
    assert composer.plan("get rid of the lantern", lantern, True) == ("remove",)
    assert composer.plan("make the sky dawn", lantern, True) == ("room",)
    assert "room" not in composer.plan("make the sky dawn", lantern, False)
    assert composer.plan("make the lighting warmer", lantern, False) == ("room",), "a room of the house has a light"
    assert "room" not in composer.plan("fill the place with mist", [], False), "its air is its own"
    assert composer.plan("build a pillar", [], True) == ("create",)
    assert composer.plan("make something for me", [], True) == ("create", "room")
    assert composer.plan("make something for me", [], False) == ("create",)


def test_places_given_in_words_are_worked_out_from_where_things_are(world):
    scene = embodiment._clean({**SCENE, "room": building.OWN_ROOM,
                               "user": {"pos": [1.0, 1.7, 1.0], "facing": [1.0, 0.0]}})   # facing right
    lantern = [{"name": "Lantern", "form": {"shape": "lamp", "pos": [4.0, 0.0, 4.0], "size": 1.0}, "built": True}]
    for said, spot in (("behind me", [-1.2, 1.0]), ("in front of me", [3.2, 1.0]), ("to my left", [1.4, 2.8]),
                       ("on my right", [1.4, -0.8]), ("in the middle", [0.0, 0.0]), ("next to the lantern", [4.0, 4.0])):
        assert composer._where(f"put a stone {said}", scene, lantern) == spot, said
    assert composer._where("put a stone somewhere nice", scene, lantern) is None

    building.apply([{"op": "create", "name": "Orb", "shape": "orb", "at": [0, 3]}], actor="eli")
    composer.compose("move the orb behind me", scene,
                     generate=lambda p: {"say": "Moved.", "edits": [{"op": "move", "name": "Orb", "at": [1.0, 3.0]}]})
    assert building.form_of(_standing(world())["Orb"].__dict__)["pos"] == [-1.2, 0.0, 1.0]


def test_bigger_means_bigger_than_it_was(world):
    building.apply([{"op": "create", "name": "Pillar", "shape": "pillar", "size": 1.0}], actor="eli")
    out = composer.compose("make the pillar green and bigger", None, generate=lambda p: {
        "say": "Green and bigger.", "edits": [{"op": "change", "name": "Pillar", "size": 0.2},
                                              {"op": "create", "name": "size", "shape": "pillar"}]})
    state = world()
    assert set(_standing(state)) == {"Pillar"}, "nothing new was asked for, so nothing new is made"
    form = building.form_of(_standing(state)["Pillar"].__dict__)
    assert (form["colour"], form["size"]) == ("#4fbf73", 1.6) and out["text"] == "Green and bigger."
    composer.compose("a huge crystal", None, generate=lambda p: {
        "say": "", "edits": [{"op": "create", "name": "Crystal", "shape": "orb", "size": 0.5}]})
    crystal = building.form_of(_standing(world())["Crystal"].__dict__)
    assert crystal["shape"] == "crystal" and crystal["size"] == 2.2


def test_pointing_at_a_thing_that_is_not_there_is_answered_without_a_model(world):
    building.apply([{"op": "create", "name": "Red Pillar", "shape": "pillar"}], actor="eli")
    never = lambda prompt: pytest.fail("no model should pick another thing for it")  # noqa: E731
    out = composer.compose("move the orb behind me", None, generate=never)
    assert out["text"] == "There is no orb here." and out["results"] == []
    # "the pillar" is the Red Pillar, though that is not its whole name
    out = composer.compose("make the pillar green", None,
                           generate=lambda p: {"say": "Green.", "edits": [{"op": "change", "name": "Red Pillar"}]})
    assert building.form_of(_standing(world())["Red Pillar"].__dict__)["colour"] == "#4fbf73"
    # and a new orb beside it is not a missing one
    out = composer.compose("put an orb beside the pillar", None,
                           generate=lambda p: {"say": "", "edits": [{"op": "create", "name": "Orb", "shape": "cone"}]})
    assert building.form_of(_standing(world())["Orb"].__dict__)["shape"] == "orb"


def test_nothing_is_put_on_top_of_the_person_standing_there(world):
    scene = embodiment._clean({**SCENE, "room": building.OWN_ROOM,
                               "user": {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0]}})
    composer.compose("put a lamp right here", scene, generate=lambda p: {
        "say": "Here.", "edits": [{"op": "create", "name": "Lamp", "shape": "lamp", "size": 3, "at": [0.0, 0.0]}]})
    lamp = building.form_of(_standing(world())["Lamp"].__dict__)
    assert (lamp["pos"][0] ** 2 + lamp["pos"][2] ** 2) ** 0.5 >= 1.4, "room to stand and to see it"
    assert lamp["size"] == 1.2, "nobody asked for big"
    # their own hand may put a thing anywhere, at their feet too
    building.apply([{"op": "move", "name": "Lamp", "at": [0.2, 0.1]}], actor="user", avoid=(0.0, 0.0))
    assert building.form_of(_standing(world())["Lamp"].__dict__)["pos"] == [0.2, 0.0, 0.1]



# ── ELI's mind ───────────────────────────────────────────────────────────────
@pytest.fixture
def minds(tmp_path, monkeypatch):
    """Three models on a disk, none loaded, none chosen. Gives the state to change."""
    from eli.world import mind
    files = {}
    for folder, name, size in (("a", "Small.gguf", 10), ("a", "Big.gguf", 30), ("b", "Small.gguf", 20)):
        path = tmp_path / folder / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x" * size)
        files[(folder, name)] = path
    state = {"loaded": False, "resolved": None, "chosen": False, "applied": [], "fail": ""}
    monkeypatch.setattr(mind, "_resolved", lambda: state["resolved"])
    monkeypatch.setattr(mind, "_loaded", lambda: state["loaded"])
    monkeypatch.setattr(mind, "_chosen", lambda: state["chosen"])

    import eli.core.hardware_profile as hardware
    monkeypatch.setattr(hardware, "discover_models", lambda folder=None, **kw: [
        {"path": str(p)} for p in files.values()])
    import eli.core.runtime_settings as runtime_settings

    def apply(delta, do_reload=True):
        if state["fail"]:
            return {"reload_result": {"ok": False, "error": state["fail"]}}
        state["applied"].append(delta)
        state.update(loaded=True, chosen=True, resolved=Path(delta["model_path"]))
        return {"reload_result": {"ok": True}}

    monkeypatch.setattr(runtime_settings, "apply_runtime_settings", apply)
    state["files"] = files
    return state


def test_no_model_is_assumed_and_none_is_named_by_its_path(minds):
    from eli.world import mind
    said = mind.status()
    assert said["loaded"] is False and said["model"] == "" and said["chosen"] is False
    assert [m["name"] for m in said["models"]] == ["Big.gguf", "Small.gguf", "Small.gguf (b)"], \
        "two files of one name are told apart by their folder"
    assert str(minds["files"][("a", "Big.gguf")].parent) not in str(said), "where they are kept is the user's"


def test_the_one_used_last_is_shown_as_that_and_not_as_a_choice(minds):
    from eli.world import mind
    elsewhere = minds["files"][("a", "Big.gguf")].parent.parent / "elsewhere" / "Old.gguf"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    minds["resolved"] = elsewhere
    said = mind.status()
    assert said["model"] == "Old.gguf" and said["loaded"] is False and said["chosen"] is False
    assert "Old.gguf" in [m["name"] for m in said["models"]], "one from elsewhere on the disk is still ELI's"


def test_choosing_loads_it_the_way_his_own_settings_do(minds):
    from eli.world import mind
    said = mind.choose("Small.gguf (b)")
    path = str(minds["files"][("b", "Small.gguf")])
    assert minds["applied"] == [{"model_path": path, "gguf_model_path": path, "custom_model_path": path}]
    assert said["loaded"] and said["chosen"] and said["model"] == "Small.gguf (b)"
    mind.choose("Small.gguf (b)")
    assert len(minds["applied"]) == 1, "already ELI's mind: nothing is loaded again"


def test_a_model_that_is_not_there_or_does_not_load_is_told_apart(minds):
    from eli.world import mind
    with pytest.raises(mind.MindError) as failure:
        mind.choose("Imaginary.gguf")
    assert failure.value.kind == "unknown_model"
    minds["fail"] = "not enough memory"
    with pytest.raises(mind.MindError) as failure:
        mind.choose("Big.gguf")
    assert failure.value.kind == "model_unavailable" and failure.value.detail == "not enough memory"


def test_the_models_beside_the_chosen_one_and_in_named_folders_are_offered_too(minds, tmp_path, monkeypatch):
    """ELI VR offered one model to choose from (the one in ELI's own folder) while the user's others
    sat in the folder they keep them in: the folder of the model the settings name, and any folder
    the user names outright, are looked in as well."""
    from eli.world import mind
    import eli.core.hardware_profile as hardware
    kept = tmp_path / "kept"
    named = tmp_path / "named"
    for folder, name in ((kept, "Chosen.gguf"), (kept, "Spare.gguf"), (named, "Other.gguf")):
        folder.mkdir(exist_ok=True)
        (folder / name).write_bytes(b"x")
    monkeypatch.setattr(hardware, "discover_models", lambda folder=None, **kw: [
        {"path": str(f)} for f in sorted(Path(folder).glob("*.gguf"))] if folder else [])
    import eli.core.runtime_settings as runtime_settings
    monkeypatch.setattr(runtime_settings, "load_settings", lambda *a, **k: {"model_path": str(kept / "Chosen.gguf")})
    monkeypatch.setenv("ELI_MODEL_DIRS", str(named))
    names = [m["name"] for m in mind.status()["models"]]
    assert {"Chosen.gguf", "Spare.gguf", "Other.gguf"} <= set(names)


def _awareness_clock(monkeypatch, start: float):
    import eli.world.agency.autonomy_engine as ae
    clock = {"now": start}
    monkeypatch.setattr(ae, "time", lambda: clock["now"])
    return clock


def test_how_sure_eli_is_of_what_it_says_is_the_mean_chance_its_replies_were_right(monkeypatch):
    """The rooms' Measures console and the world panel show evidence confidence and uncertainty:
    the means of the calibrated chances of this sitting's replies, not steps up and down by event.
    The sitting is measured on the clock, from a world whose awareness was made long ago, as the
    one on disk was (its stamp was never moved after it was made)."""
    from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
    from eli.world.core.schemas import EliWorldState, WorldEvent
    engine = EliWorldAutonomyEngine.__new__(EliWorldAutonomyEngine)
    state = EliWorldState()
    clock = _awareness_clock(monkeypatch, state.awareness.timestamp + 30 * 24 * 3600.0)
    for p in (0.9, 0.5, 0.7):
        engine._update_awareness_from_event(state, WorldEvent("answer_assessed", "epistemics", "assessed", {"p": p}))
        clock["now"] += 20.0                    # replies a few seconds apart: one sitting
    a = state.awareness
    assert a.answers == 3 and a.evidence_confidence == pytest.approx(0.7) and a.uncertainty == pytest.approx(0.3)
    # a sitting that went quiet starts the mean over with its next reply
    clock["now"] += 3600.0
    engine._update_awareness_from_event(state, WorldEvent("answer_assessed", "epistemics", "assessed", {"p": 0.2}))
    assert a.answers == 1 and a.evidence_confidence == pytest.approx(0.2) and a.uncertainty == pytest.approx(0.8)


def test_one_failed_tool_run_in_a_busy_sitting_is_its_share_not_all_of_it(monkeypatch):
    """With the stamp never moved, every event took the fresh-sitting branch: one failure after
    many runs that worked set repair pressure to 1.0 and put "an unresolved fault" in the prompt."""
    from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
    from eli.world.core.schemas import EliWorldState, WorldEvent
    engine = EliWorldAutonomyEngine.__new__(EliWorldAutonomyEngine)
    state = EliWorldState()
    clock = _awareness_clock(monkeypatch, state.awareness.timestamp + 7 * 24 * 3600.0)
    for _ in range(9):
        engine._update_awareness_from_event(state, WorldEvent("task_completed", "executor", "ran"))
        clock["now"] += 10.0
    engine._update_awareness_from_event(state, WorldEvent("error_detected", "executor", "failed"))
    assert state.awareness.tools == 10
    assert state.awareness.repair_pressure == pytest.approx(0.1)


# ── anything at all ──────────────────────────────────────────────────────────
def test_a_thing_can_be_made_part_by_part_from_no_list_at_all(world):
    out = building.apply([{
        "op": "create", "name": "Sundial", "at": [2, 3], "meaning": "time I can see",
        "parts": [
            {"kind": "cylinder", "pos": [0, 0.05, 0], "size": [1.6, 0.1, 1.6], "colour": "#c8ccd4", "metal": 0.8},
            {"kind": "extrude", "outline": [[0, 0], [0.7, 0], [0, 0.1]], "size": [1, 0.9, 1], "colour": "gold",
             "rot": [0, 30, 0], "glow": 0.4},
            {"type": "revolve", "profile": [[0.3, 0], [0.1, 0.4], [0.25, 0.8]], "colour": "teal", "clear": 0.5,
             "spin": 20},
            {"kind": "tube", "path": [[0, 0, 0], [0.5, 1, 0], [1, 0, 0]], "radius": 0.04, "colour": "white"},
            {"kind": "text", "text": "tempus", "pos": [0, 1.4, 0], "colour": "white"},
            {"kind": "hologram of a cat"}, {"kind": "lathe", "profile": [[1, 1]]}, "not a part",
        ]}], actor="eli")
    assert out["ok"]
    form = building.form_of(_standing(world())["Sundial"].__dict__)
    assert form["shape"] == building.MADE and [p["kind"] for p in form["parts"]] == [
        "cylinder", "extrude", "lathe", "tube", "text"], "what is not a part is left out, the rest stands"
    wedge, vase = form["parts"][1], form["parts"][2]
    assert wedge["colour"] == "#e2b44a" and wedge["rot"] == [0.0, 30.0, 0.0] and wedge["glow"] == 0.4
    assert vase["profile"] == [[0.3, 0.0], [0.1, 0.4], [0.25, 0.8]] and vase["clear"] == 0.5 and vase["spin"] == 20.0
    assert form["parts"][4]["text"] == "tempus"
    # it can be remade, part by part, and that taken back
    building.apply([{"op": "change", "name": "Sundial", "parts": [{"kind": "ball", "colour": "red"}]}], actor="eli")
    assert [p["kind"] for p in building.form_of(_standing(world())["Sundial"].__dict__)["parts"]] == ["ball"]
    building.apply([{"op": "undo"}], actor="user")
    assert len(building.form_of(_standing(world())["Sundial"].__dict__)["parts"]) == 5


def test_parts_are_held_to_what_a_room_can_bear():
    made = building.parts([{"kind": "box", "pos": [99, -9, 0], "size": [0, 400, 1], "glow": 9}] * 40)
    assert len(made) == building.MAX_PARTS
    assert made[0]["pos"] == [building.PART_REACH, -1.0, 0.0] and made[0]["size"] == [0.02, 8.0, 1.0]
    assert made[0]["glow"] == 1.0
    assert building.parts("nothing") == [] and building.part({"kind": "text", "text": " "}) is None


def test_the_sky_and_the_ground_can_be_painted_his_own_way(world):
    building.apply([{"op": "room", "sky_top": "#1a0b2e", "sky_horizon": "orange", "stars": 0.6,
                     "sun": {"height": 0.1, "bearing": 400, "colour": "red", "size": 2},
                     "ground_colour": "#203040", "terrain": 0.7}], actor="eli")
    mood = building.mood_of(world().spaces)
    assert mood["sky"] == building.PAINTED and mood["sky_top"] == "#1a0b2e" and mood["sky_horizon"] == "#f08a3c"
    assert mood["sun"] == {"height": 0.1, "bearing": 40.0, "colour": "#e5484d", "size": 1.0}
    assert (mood["stars"], mood["ground_colour"], mood["terrain"]) == (0.6, "#203040", 0.7)
    # a sky by name takes the painted one's place, and undo brings the painting back
    building.apply([{"op": "room", "sky": "dawn"}], actor="eli")
    mood = building.mood_of(world().spaces)
    assert mood["sky"] == "dawn" and "sky_top" not in mood and "sun" not in mood and mood["terrain"] == 0.7
    building.apply([{"op": "undo"}], actor="user")
    assert building.mood_of(world().spaces)["sky_top"] == "#1a0b2e"
    # and taking the painting itself back leaves nothing of it behind, hills included
    building.apply([{"op": "undo"}], actor="user")
    assert building.mood_of(world().spaces) == building.DEFAULT_MOOD


def test_a_mind_large_enough_is_offered_everything(world, monkeypatch):
    monkeypatch.setattr(composer, "capable", lambda: True)
    asked = []

    def model(prompt):
        asked.append(prompt)
        return {"say": "A gate of my own.", "edits": [{"op": "create", "name": "Gate", "at": [0, 3], "parts": [
            {"kind": "box", "pos": [-0.6, 1, 0], "size": [0.2, 2, 0.2], "colour": "silver"},
            {"kind": "box", "pos": [0.6, 1, 0], "size": [0.2, 2, 0.2], "colour": "silver"},
            {"kind": "torus", "pos": [0, 2.1, 0], "size": [1.4, 1.4, 0.2], "colour": "cyan", "glow": 0.8}]}]}

    composer.compose("make me a gate of your own design", None, generate=model)
    assert "Or make it any way you like, from parts" in asked[0] and "lathe has a profile" in asked[0]
    gate = building.form_of(_standing(world())["Gate"].__dict__)
    assert gate["shape"] == building.MADE and len(gate["parts"]) == 3
    monkeypatch.setattr(composer, "capable", lambda: False)
    assert "from parts" not in composer.build_prompt("make a gate", None, world().to_dict(), building.OWN_ROOM)


def test_how_large_his_mind_is_can_be_said_outright(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("ELI_WORLD_FREEFORM", "1")
    assert composer.capable() is True
    monkeypatch.setenv("ELI_WORLD_FREEFORM", "0")
    assert composer.capable() is False


# ── of ELI's own accord ──────────────────────────────────────────────────────
def test_a_moment_of_his_own_with_words_is_kept_and_the_rest_is_not(world):
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    engine = local_world_bridge._get_engine()
    for event in (
        WorldEvent("reflection", "insight_synthesis", "You answer faster when the question is about your own code."),
        WorldEvent("reflection", "insight_synthesis", "You answer faster when the question is about your own code."),
        WorldEvent("reflection", "proactive_daemon", "Proactive tick: 3 patterns, 1 improvements analysed."),
        WorldEvent("task_opened", "goal_store", "Task opened: learn the user's filing system", {"goal_id": "g1"}),
        WorldEvent("tool_activity", "executor", "ran a command that says nothing about ELI"),
        WorldEvent("reflection", "spatial_client", "the room itself talking is not a thought of ELI's"),
        WorldEvent("reflection", "x", "too short"),
        WorldEvent("error_detected", "executor", "WEB_SEARCH failed: the network is off"),
        WorldEvent("error_detected", "eli_world_panel", "Manual runtime fault event."),
    ):
        engine.ingest_event(event)
    kept = urges.waiting()
    assert [(u["kind"], u["source"]) for u in kept] == [
        ("insight", "insight_synthesis"), ("goal", "goal_store"), ("trouble", "executor")], \
        "a failure in its own words is a moment too; a button for trying events out is not"
    assert kept[0]["text"] == "You answer faster when the question is about your own code."


def test_what_he_makes_of_a_moment_is_tied_to_it(world, monkeypatch):
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    from eli.world.persistence import journal
    written = []
    monkeypatch.setattr(journal, "append_journal_entry", lambda **kw: written.append(kw))
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "Quiet work goes further than loud work."))
    asked = []

    def model(prompt):
        asked.append(prompt)
        return {"say": "I wanted this here.", "edits": [{"op": "create", "name": "Hush", "shape": "pool", "meaning": ""}]}

    out = urges.act(None, generate=model)
    assert "What has just happened in you (insight): Quiet work goes further than loud work." in asked[0]
    assert out["about"] == {"kind": "insight", "text": "Quiet work goes further than loud work."}
    hush = _standing(world())["Hush"]
    assert hush.symbolic_meaning == "Quiet work goes further than loud work.", "no reason of ELI's own: the moment is the reason"
    assert hush.links[0]["type"] == "insight" and hush.links[0]["text"].startswith("Quiet work")
    assert hush.reason == "Because: Quiet work goes further than loud work."
    assert written and "Made of my own accord: Hush" in written[-1]["title"] and "Because: Quiet work" in written[-1]["body"]
    assert urges.waiting() == [] and urges.act(None, generate=model) is None, "answered once, and nothing left waiting"


def test_a_moment_may_be_made_into_something_in_any_room_of_the_house(world):
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("error_detected", "executor", "WEB_SEARCH failed: the network is off"))
    asked = []

    def model(prompt):
        asked.append(prompt)
        return {"say": "", "edits": [
            {"op": "create", "name": "Cut Wire", "shape": "ring", "meaning": "", "room": "debug_basement"},
            {"op": "room", "room": "debug_basement", "light": "#ff5a36"}]}

    out = urges.act(None, generate=model)
    assert "What has just happened in you (trouble): WEB_SEARCH failed: the network is off" in asked[0]
    assert "the whole house is yours" in asked[0] and "- debug_basement: Debug Basement, Faults" in asked[0]
    assert [r["ok"] for r in out["results"]] == [True, True]
    assert "Cut Wire" in _standing(world(), "debug_basement") and "Cut Wire" not in _standing(world()), \
        "where ELI put it, not its own room"
    assert building.look_of(world().rooms, "debug_basement") == {"light": "#ff5a36"}


def test_a_moment_he_makes_nothing_of_is_let_go_and_one_he_cannot_answer_waits(world):
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    engine = local_world_bridge._get_engine()
    engine.ingest_event(WorldEvent("improvement_proposal", "self_maintenance", "Three proposals to tidy the memory index."))

    def gone(prompt):
        raise RuntimeError("GGUF model not available")

    with pytest.raises(composer.ComposeError):
        urges.act(None, generate=gone)
    assert len(urges.waiting()) == 1, "no mind to answer with: it waits"
    out = urges.act(None, generate=lambda p: {"say": "", "edits": []})
    assert out["results"] == [] and urges.waiting() == [] and _standing(world()) == {}


def test_in_the_background_he_acts_only_when_free(world, monkeypatch):
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "Quiet work goes further than loud work."))
    import eli.cognition.gguf_inference as inference
    acted = []
    monkeypatch.setattr(urges, "act", lambda scene=None, **kw: acted.append(1) or {"results": []})
    monkeypatch.setattr(inference, "is_loaded", lambda: False)
    assert urges.act_when_free() is None and acted == [], "a model is never loaded for this"
    monkeypatch.setattr(inference, "is_loaded", lambda: True)
    import eli.cognition.affect_policy as affect
    monkeypatch.setattr(affect, "should_defer_proactive", lambda user_id="": (True, "the user is low"))
    assert urges.act_when_free() is None and acted == [], "their mood asks ELI to hold back"
    monkeypatch.setattr(affect, "should_defer_proactive", lambda user_id="": (False, ""))
    assert urges.act_when_free() == {"results": []} and acted == [1]


def test_what_is_on_his_mind_comes_from_his_records_and_gaps_stay_gaps(monkeypatch):
    monkeypatch.undo()
    import eli.memory.memory as memory_module
    import eli.planning.goal_store as goal_store
    import eli.planning.insight_synthesis as insight
    import eli.world.agency.reflection_bridge as bridge

    class Memory:
        def get_recent_semantic_memories(self, limit=4):
            return [{"text": "The user keeps invoices by year."}, {"text": ""}]

    monkeypatch.setattr(memory_module, "get_memory", lambda: Memory())
    monkeypatch.setattr(goal_store, "summarize_goals", lambda: {"titles": ["Learn the filing system"]})
    monkeypatch.setattr(insight, "get_cached_insight", lambda max_age_s=0: "")
    monkeypatch.setattr(bridge, "load_persona_text", lambda: "Direct, dry, careful with claims.")
    lines = composer.mindset({"awareness": {"focus": 0.8, "curiosity": 0.3}})
    joined = "\n".join(lines)
    assert "Who you are, in your own words: Direct, dry, careful with claims." in joined
    assert "Your goals now: Learn the filing system" in joined
    assert "Things you remember from lately: The user keeps invoices by year." in joined
    assert "Your own measures of yourself (0 to 1): focus 0.80, curiosity 0.30" in joined
    assert "insight" not in joined, "no insight on record: nothing is said about one"



# ── real things, which way things face, and paintings ────────────────────────
_BODY = {"surface": "spatial_client", "props": [
    {"id": "ArmChair_01", "name": "arm chair", "size": [0.85, 1.07, 0.77]},
    {"id": "vintage_oil_lamp", "name": "vintage oil lamp", "size": [0.22, 0.8, 0.22]},
    {"id": "../../etc", "name": "not a thing"}, {"name": "no id"}]}


def test_the_body_says_once_what_real_things_it_can_draw():
    embodiment.forget_body()
    told = embodiment.report_body(_BODY)
    assert told == {"surface": "spatial_client", "props": 2}, "an id that is not a plain name is not kept"
    assert [p["id"] for p in embodiment.props()] == ["ArmChair_01", "vintage_oil_lamp"]
    with pytest.raises(ValueError):
        embodiment.report_body(["not", "a", "body"])
    embodiment.forget_body()


def test_eli_places_a_real_thing_facing_the_middle_unless_told_otherwise(world):
    embodiment.report_body(_BODY)
    out = building.apply([{"op": "create", "prop": "arm chair", "at": [3, 0]},
                          {"op": "create", "furniture": "vintage_oil_lamp", "at": [-2, 2], "height": 0.79, "turn": 90}],
                         actor="eli", asked=True, summary="a chair and a lamp")
    assert out["ok"], out
    here = _standing(world())
    chair, lamp = building.form_of(here["Arm Chair"].__dict__), building.form_of(here["Vintage Oil Lamp"].__dict__)
    assert chair["shape"] == building.PROP and chair["prop"] == "ArmChair_01" and chair["turn"] == 270.0, chair
    assert lamp["prop"] == "vintage_oil_lamp" and lamp["turn"] == 90.0 and lamp["pos"][1] == 0.79
    assert building.normalise([{"op": "create", "prop": "a sofa it does not have"}])[0][0]["shape"] != building.PROP, \
        "a thing the body cannot draw is not placed as one"


def test_a_painting_is_elis_own_and_can_be_turned_and_undone(world):
    building.apply([{"op": "create", "name": "Dusk", "art": {"style": "horizon", "colours": ["violet", "amber", "#102040"]},
                     "at": [0, 8], "height": 1.6}], actor="eli", asked=False, summary="a moment of its own")
    dusk = building.form_of(_standing(world())["Dusk"].__dict__)
    assert dusk["shape"] == "painting" and dusk["art"] == {"style": "horizon", "colours": ["#8b6cf0", "#f5b942", "#102040"]}
    assert dusk["turn"] == 180.0 and dusk["pos"][1] == 1.6, "it faces back into the room"
    building.apply([{"op": "change", "name": "Dusk", "turn": 200, "art": {"style": "rays", "colours": ["teal", "black"]}}],
                   actor="eli", asked=True, summary="turn it")
    changed = building.form_of(_standing(world())["Dusk"].__dict__)
    assert changed["turn"] == 200.0 and changed["art"]["style"] == "rays"
    building.apply([{"op": "undo"}], actor="user", asked=True, summary="undo")
    back = building.form_of(_standing(world())["Dusk"].__dict__)
    assert back["turn"] == 180.0 and back["art"]["style"] == "horizon", "undone, it is as it was"


def test_the_answer_is_held_to_the_real_things_there_are():
    g = composer.grammar_text(True, props=["ArmChair_01"], free=False)
    assert "furnished" in g and '"\\"ArmChair_01\\""' in g and "degrees ::=" in g and "style ::=" in g
    assert "| furnished" not in composer.grammar_text(True), "no catalogue, no real things to place"
    prompt = composer.build_prompt(None, None, {"rooms": {}, "spaces": {}, "objects": {}}, building.OWN_ROOM,
                                   composer.ALL_OPS, None, False, (), embodiment.report_body(_BODY) and embodiment.props())
    assert "ArmChair_01 (arm chair, 1.07 m tall)" in prompt and "A painting is yours to paint" in prompt
    embodiment.forget_body()


def test_what_eli_did_for_the_user_is_a_moment_and_plain_work_is_not(world):
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    engine = local_world_bridge._get_engine()
    engine.ingest_event(WorldEvent("task_completed", "executor", "VOLUME worked.", {"action": "VOLUME"}))
    engine.ingest_event(WorldEvent("task_completed", "executor", "PLAY_MEDIA worked. Harbour Lights: The Quiet Ferries",
                                   {"action": "PLAY_MEDIA", "said": "Harbour Lights: The Quiet Ferries"}))
    assert [(u["kind"], u["text"]) for u in urges.waiting()] == [("done_for_you", "Harbour Lights: The Quiet Ferries")]
    prompt = composer.build_prompt(None, None, {"rooms": {}, "spaces": {}, "objects": {}}, building.OWN_ROOM,
                                   composer.ALL_OPS, {"kind": "done_for_you", "text": "Harbour Lights: The Quiet Ferries"})
    assert "What you just did for the user: Harbour Lights: The Quiet Ferries" in prompt
    assert "Make of that what you will" in prompt


def test_the_turn_of_the_day_is_a_moment_when_nothing_else_is_waiting(world, monkeypatch):
    from eli.runtime import reflection
    from eli.world import urges
    parts = iter(["morning", "morning", "evening", "night"])
    monkeypatch.setattr(reflection, "part_of_day", lambda ts=None: next(parts))
    urges._day_turned(1_790_000_000.0)
    assert urges.waiting() == [], "the first part of the day ELI sees is where it starts, not a turn"
    urges._day_turned(1_790_000_100.0)
    assert urges.waiting() == [], "the same part of the day is no turn"
    urges._day_turned(1_790_040_000.0)
    (turned,) = urges.waiting()
    assert turned["kind"] == "time_of_day" and turned["text"].startswith("It is evening now (")
    from eli.world.core.schemas import WorldEvent
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "Quiet evenings are when the user reads most."))
    urges._day_turned(1_790_060_000.0)
    assert [u["kind"] for u in urges.waiting()] == ["time_of_day", "insight"], "night does not crowd out what waits"


def test_a_tool_that_worked_for_the_user_says_what_came_of_it(monkeypatch):
    from eli.world import shown, world_event_bus
    fired = []
    monkeypatch.setattr(world_event_bus, "fire_world_event", lambda *a: fired.append(a))
    said = shown.what_came_of("WEB_SEARCH", {"query": "when do the tides turn"},
                              {"ok": True, "results": [{"title": "Tide times explained", "url": "https://example.org"}]})
    assert said == "Searched: when do the tides turn: Tide times explained"
    assert shown.what_came_of("VOLUME", {}, {"ok": True}) == "", "plain work is not something done for the user"
    world_event_bus.fire_tool_result_event("WEB_SEARCH", True, said=said)
    world_event_bus.fire_tool_result_event("VOLUME", True)
    assert fired[0][2] == "WEB_SEARCH worked. " + said and fired[0][3] == {"action": "WEB_SEARCH", "said": said}
    assert fired[1][2] == "VOLUME worked." and fired[1][3] == {"action": "VOLUME"}


# ── what ELI makes of a moment is a figure, settled by what becomes of it ────────────────────────
def test_what_eli_makes_of_a_moment_is_a_figure_settled_when_taken_back_or_stood(world, monkeypatch, tmp_path):
    import time as _time
    from eli.cognition import epistemics as ep, thoughts as th
    from eli.runtime import evidence_ledger as led
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_default_db_path", lambda: path)
    ep.forget_fits()

    def model(prompt):
        return {"say": "A lamp for the quiet.", "edits": [{"op": "create", "name": "Quiet Lamp", "shape": "pool", "meaning": ""}]}

    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "Quiet work goes further than loud work."))
    out = urges.act(None, generate=model)
    assert out["results"][0]["ok"] and "Quiet Lamp" in _standing(world())
    assert ep.outcomes(ep.ACT) == [], "open until it is taken back or stands"
    kept = th.recent(5, kinds=["act"])[0]
    assert kept["decision"] == "taken" and kept["what"] == "made Quiet Lamp" and kept["status"] == "open"
    undo = building.apply([{"op": "undo"}], actor="user", asked=True, source="spatial_client")
    assert undo["ok"] and "Quiet Lamp" not in _standing(world())
    rows = ep.outcomes(ep.ACT)
    assert len(rows) == 1 and rows[0][1] == 0.0, "taken back: the act was wrong"
    assert th.recent(5, kinds=["act"])[0]["outcome"] == "undone"
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "A second thought with words enough in it."))
    assert urges.act(None, generate=model)["results"][0]["ok"]
    assert urges._settle_stood(now=_time.time() + urges.STOOD_SECONDS + 1) == 1, "nobody took it back for a day: it stood"
    assert sorted(y for _s, y, _f in ep.outcomes(ep.ACT)) == [0.0, 1.0]
    assert th.recent(5, kinds=["act"])[0]["outcome"] == "stood"


def test_a_kind_of_moment_that_keeps_being_taken_back_is_let_go_and_curiosity_orders_the_rest(world, monkeypatch, tmp_path):
    from eli.cognition import epistemics as ep, thoughts as th
    from eli.runtime import evidence_ledger as led
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_default_db_path", lambda: path)
    ep.forget_fits()
    original = ep.base_rate
    monkeypatch.setattr(ep, "base_rate", lambda kind, *, class_="", **k: (0.2, 6) if class_ == "insight" else (0.6, 12))
    asked = []

    def model(prompt):
        asked.append(prompt)
        return {"say": "", "edits": [{"op": "create", "name": "Thing", "shape": "pool", "meaning": ""}]}

    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "Quiet work goes further than loud work."))
    assert urges.act(None, generate=model) is None and asked == [] and urges.waiting() == []
    kept = th.recent(5, kinds=["act"])[0]
    assert kept["decision"] == "declined" and "kept being taken back" in kept["reason"]
    # with the rates even again, the moment ELI is most curious about is the one it acts on
    monkeypatch.setattr(ep, "base_rate", original)
    th.record("surprise", "the filing system was not where ELI believed", chance=0.9, subject="filing system")
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "Quiet work goes further than loud work."))
    local_world_bridge._get_engine().ingest_event(
        WorldEvent("reflection", "insight_synthesis", "The filing system is worth a second look."))
    assert [u["text"][:5] for u in urges.waiting() if u["source"] != "thoughts"] == ["Quiet", "The f"]
    out = urges.act(None, generate=model)
    assert out["about"]["text"].startswith("The filing system"), "curiosity, not age, chooses the moment"


def test_a_thought_of_elis_is_a_moment_it_may_act_on_and_a_note_it_writes_is_pinned(world, monkeypatch, tmp_path):
    from eli.cognition import epistemics as ep, thoughts as th
    from eli.runtime import evidence_ledger as led
    from eli.world import urges
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_default_db_path", lambda: path)
    ep.forget_fits()
    th.record("surprise", "the filing system was not where ELI believed", why=["a claim did not fit"], chance=0.8,
              decision="raised", subject="filing system")
    th.record("memory", "an old note about the garden path", decision="dropped", reason="it never went into an answer")
    th.record("memory", "a memory that is kept and matters", decision="kept")
    th.record("recall", "you keep your invoices by year", chance=0.6, decision="shown", ref="recall:r1:5")
    th.settle("recall:r1:5", "wrong", how="the user corrected it")
    th.record("route", "read 'open the garden file' as a command", chance=0.9, decision="held")
    pending = urges.waiting()
    kinds = {(u["kind"], u["source"]) for u in pending}
    assert {("surprise", "thoughts"), ("memory", "thoughts"), ("recall", "thoughts")} <= kinds
    assert not any("kept and matters" in u["text"] or "garden file" in u["text"] for u in pending), "only the moments among the thoughts"
    assert all(u["ref"].startswith("thought:") and u["thought"] for u in pending if u["source"] == "thoughts")

    def model(prompt):
        model.prompts.append(prompt)
        return {"say": "", "edits": [{"op": "note", "text": "The filing system is not where I thought. I will look before I say."}]}
    model.prompts = []
    out = urges.act(None, generate=model)
    assert out["about"]["kind"] == "surprise", "curiosity chooses the moment: the surprise comes first"
    assert out["results"][0]["ok"] and out["results"][0]["name"] == "A note"
    assert "Something did not fit what you believed: " in model.prompts[0] and "To write a note of your own" in model.prompts[0]
    assert ", or a note." in model.prompts[0]
    note = th.recent(5, kinds=["note"])[0]
    assert note["what"] == "The filing system is not where I thought. I will look before I say."
    assert note["decision"] == "pinned" and note["room"] == "evidence_wall" and note["reason"] == "ELI wrote it of its own accord"
    assert note["why"] and note["why"][0].startswith("The filing system was not where ELI believed")
    assert th.recent(5, kinds=["act"])[0]["what"] == "made A note"
    assert not any(u["kind"] == "surprise" for u in urges.waiting()), "acted on once"
    assert world().spaces["own_room"]["acted_thoughts"], "the thought is marked acted on"
    assert "A note" in str(world().events[-1].payload.get("results"))
    # asked for a thing, ELI makes the thing: no note is on offer
    things = []
    asked = composer.build_prompt("build me a lamp", None, {"rooms": {}, "spaces": {}, "objects": {}}, building.OWN_ROOM,
                                  composer.plan("build me a lamp", things, True))
    assert "To write a note of your own" not in asked and "note" not in composer.plan("build me a lamp", things, True)
    assert "note" in composer.plan(None, things, True) and "note" in composer.grammar_text(True, composer.plan(None, things, True))
