"""Small games ELI sets in its rooms (eli/world/games.py): how one is asked for, set at once by the
rules and the record, how ELI's own words come after, the range with its guns and rings, how a
result is kept, and how it reaches the desktop.

Everything runs on a world in a temporary folder with the journal, the memory and the event bus
turned aside, and the model is a stand-in.
"""
from __future__ import annotations

import time

import pytest

from eli.world import building, composer, embodiment, games, local_world_bridge, spatial_feed
from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
from eli.world.persistence import storage as world_storage
from eli.world.persistence.storage import EliWorldStorage

SCENE = {
    "surface": "eli_vr", "session": "s1", "room": "core_room", "room_name": "Core Room",
    "seen_as": "An open deck on a station in orbit.", "headset": True, "screen": False,
    "user": {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0], "looking_at": {"kind": "room", "name": "Core Room"}},
    "eli": {"visible": True, "distance": 2.0}, "things": [],
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fresh world of its own, with nothing of ELI's real records reached. Gives a reader."""
    monkeypatch.setattr(world_storage, "world_dir", lambda: tmp_path)
    monkeypatch.setattr(world_storage, "WORLD_DIR", tmp_path)
    monkeypatch.setattr(world_storage, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(world_storage, "ACTIONS_PATH", tmp_path / "actions.jsonl")
    store = EliWorldStorage(state_path=tmp_path / "world.json")
    monkeypatch.setattr(local_world_bridge, "_engine_instance", EliWorldAutonomyEngine(storage=store))
    monkeypatch.setattr(local_world_bridge, "load_persona_text", lambda: "")
    monkeypatch.setattr(composer, "mindset", lambda state: ["Your latest insight: quiet work goes further."])
    monkeypatch.setattr(games, "_mind_loaded", lambda: False)       # no mind in memory: no words unless a caller brings one
    import eli.cognition.thoughts as thoughts_module
    monkeypatch.setattr(thoughts_module, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    from eli.world.persistence import journal
    import eli.memory.memory as memory_module
    import eli.world.world_event_bus as bus
    kept = {"journal": [], "memories": [], "events": []}

    class Memory:
        def store_memory(self, text, **kw):
            kept["memories"].append((text, kw))

    monkeypatch.setattr(journal, "append_journal_entry", lambda **kw: kept["journal"].append(kw))
    monkeypatch.setattr(memory_module, "get_memory", lambda: Memory())
    monkeypatch.setattr(bus, "fire_world_event", lambda *a: kept["events"].append(a))
    embodiment.forget()
    embodiment.forget_body()
    store.kept = kept
    yield store
    embodiment.forget()
    embodiment.forget_body()


def _standing(state, room="core_room"):
    return {o.object_id: o for o in state.objects.values() if o.room == room and not o.retired}


def _pieces(state, role=None, room="core_room"):
    out = [o for o in _standing(state, room).values() if (o.metadata or {}).get("piece")]
    if role:
        out = [o for o in out if o.metadata["piece"]["role"] == role]
    return sorted(out, key=lambda o: o.metadata["piece"]["index"])


WORDS = {"say": "Here is a game.", "name": "Lantern Hunt", "goal": "Hit every lantern before it fades.",
         "lines": {"start": "Go.", "hit": "Got one.", "miss": "Gone.", "end": "That was it."}}


def _model(words):
    """A stand-in mind: ELI's words for a game already set."""
    def answer(prompt):
        answer.prompts.append(prompt)
        return dict(words)
    answer.prompts = []
    return answer


# ── asking for one ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("said", [
    "let's play a game", "can we play catch", "set up a quick game here", "make me a game",
    "challenge me", "play a round", "something to play?", "rematch", "let's play again",
    "could you invent a small vr game", "lets have another round", "hide something for me to find",
    "hide one of your lanterns somewhere", "let's do hide and seek", "give me some targets to hit", "something to catch",
    "set up the range", "let's shoot", "give me a gun", "shooting range please", "target practice", "open the range",
    "hand me the shotgun", "guns and targets",
])
def test_a_request_to_play_is_heard_as_one(said):
    assert games.wants_game(said)


@pytest.mark.parametrize("said", [
    "play some music", "play the song again", "let's play a video", "play a game of chess",
    "how are you today", "build me a lantern", "open the game on steam", "a trivia game please",
    "what range does the wifi have", "the mountain range looks far",
])
def test_music_chess_and_talk_are_not(said):
    assert not games.wants_game(said)


def test_stopping_and_the_kind_named():
    assert all(games.wants_stop(s) for s in ("stop the game", "I give up", "game over", "enough of this round", "let's stop",
                                             "stop the range"))
    assert not any(games.wants_stop(s) for s in ("stop the music", "let's play", "stop"))
    for said, kind in (("let's play catch", "catch"), ("hide something for me to find", "hide"), ("a memory game", "order"),
                       ("sort them into their mats", "sort"), ("hit the lanterns", "targets"), ("shoot the targets", "range"),
                       ("give me a gun", "range"), ("set up the range", "range"), ("a game", "")):
        assert games.kind_named(said) == kind, said


# ── the rules: the first game's, then the record's ───────────────────────────
def _went(kind, hits, misses, count=None, pace=None, abandoned=False):
    return {"kind": kind, "hits": hits, "score": hits, "misses": misses, "abandoned": abandoned,
            "count": count or games.FIRST[kind][0], "pace": games.FIRST[kind][2] if pace is None else pace}


def test_the_rules_are_the_first_games_then_stepped_from_the_last_of_the_kind():
    first = games.rules_for("targets", [])
    assert (first["count"], first["seconds"], first["pace"], first["from"]) == (8, 60, 0.5, None)
    harder = games.rules_for("targets", [_went("targets", 6, 1)])
    assert (harder["count"], harder["pace"]) == (10, 0.62), "two hits in three or better: a step harder"
    kinder = games.rules_for("targets", [_went("targets", 1, 5)])
    assert (kinder["count"], kinder["pace"]) == (6, 0.38), "one in three or worse: a step kinder"
    same = games.rules_for("targets", [_went("targets", 1, 1)])
    assert (same["count"], same["pace"]) == (8, 0.5), "in between: as it was"
    climbed = games.rules_for("targets", [_went("targets", 9, 1, count=10, pace=0.62)])
    assert (climbed["count"], climbed["pace"]) == (12, 0.74), "stepped from the last game's own rules, not reset"
    assert games.rules_for("targets", [_went("catch", 9, 0), _went("targets", 1, 5, abandoned=True)])["count"] == 8, \
        "another kind, or a game given up, says nothing"
    assert games.rules_for("hide", [_went("hide", 0, 6)])["count"] == 2 and games.rules_for("hide", [_went("hide", 0, 9, count=2)])["count"] == 1
    top = games.rules_for("range", [_went("range", 30, 0, count=games.MAX_PIECES, pace=1.0)])
    assert (top["count"], top["pace"]) == (games.MAX_PIECES, 1.0), "never past what a room bears"


def test_a_game_by_the_rules_takes_the_shape_named_and_the_rooms_light():
    lit = {"spaces": {"own_room": {"mood": {"light": "#ff8800"}}}, "rooms": {}}
    spec = games.quick("targets", "give me lanterns to shoot", lit, building.OWN_ROOM)
    assert spec["pieces"][0]["shape"] == "lamp" and spec["pieces"][0]["colour"] == "#ff8800"
    assert spec["name"] == "Targets" and spec["goal"] == games.GOALS["targets"] and spec["lines"] == {"start": "", "hit": "", "miss": "", "end": ""}
    plain = games.quick("sort", "", {}, "core_room")
    assert [p["shape"] for p in plain["pieces"]] == ["block", "orb"] and [m["tag"] for m in plain["mats"]] == ["block", "orb"]
    assert plain["pieces"][0]["colour"] in building.COLOURS.values(), "no light given the room: a named colour"
    shoot = games.quick("range", "", {}, "core_room")
    assert shoot["pieces"][0]["shape"] == "target" and set(shoot["arms"]) == set(games.ARMS) and shoot["rings"] == [3, 2, 1]
    assert shoot["motion"] == "pop" and games.quick("range", "", {}, "core_room", [_went("range", 9, 1, pace=0.75)])["motion"] == "fly"


# ── the shape of ELI's words ─────────────────────────────────────────────────
def test_elis_words_are_held_to_an_exact_shape_that_llama_cpp_accepts():
    text = games.words_grammar()
    assert text.startswith('root ::= "{" ws "\\"say\\":" ws say "," ws "\\"name\\":" ws name')
    assert '"\\"lines\\":"' in text and "{1,%d}" % games.LINE_MAX in text
    assert "{1," not in games.words_grammar(bounded=False)
    try:
        from llama_cpp import LlamaGrammar
        for grammar in (text, games.words_grammar(bounded=False)):
            LlamaGrammar.from_string(grammar, verbose=False)
    except Exception as unavailable:  # mocked or absent here
        pytest.skip(f"llama.cpp not usable in this lane ({type(unavailable).__name__})")


def test_a_game_is_made_exact_and_held_to_what_a_room_bears():
    spec = games.normalise({"kind": "targets", "count": 99, "seconds": 3, "pace": 7, "motion": "wobble",
                            "pieces": [{"name": "Lantern", "shape": "tower", "colour": "Gray", "size": 40, "tag": "Lamp Post!"},
                                       "not a piece"]})
    assert (spec["count"], spec["seconds"], spec["pace"], spec["motion"]) == (games.MAX_PIECES, games.MIN_SECONDS, 1.0, "still")
    assert spec["pieces"] == [{"name": "Lantern", "colour": "#8a8f98", "size": building.MAX_SIZE, "glow": 0.3,
                               "tag": "lamppost", "shape": "orb"}], "a shape too big to be a piece is an orb"
    assert spec["lines"] == {"start": "", "hit": "", "miss": "", "end": ""} and spec["name"] == "Targets"
    # a sorting game has two kinds at least, no two with one tag, and a mat for each
    spec = games.normalise({"kind": "sort", "pieces": [{"name": "Stone", "shape": "block", "colour": "grey", "tag": "a"},
                                                       {"name": "Shell", "shape": "orb", "colour": "pink", "tag": "a"}],
                            "mats": [{"name": "Stone mat", "colour": "white", "tag": "a"}], "count": 1})
    assert [p["tag"] for p in spec["pieces"]] == ["a", "a2"] and spec["count"] == 2
    assert spec["mats"] == [{"name": "Stone mat", "colour": "#f2f2f2", "tag": "a"}, {"name": "Shell mat", "colour": "#f078b4", "tag": "a2"}]
    assert "motion" not in spec
    lone = games.normalise({"kind": "sort", "pieces": [{"name": "Stone", "shape": "block", "colour": "grey", "tag": "s"}]})
    assert len(lone["pieces"]) == 2 and len(lone["mats"]) == 2, "one kind alone cannot be sorted: a second is made"
    hide = games.normalise({"kind": "hide", "count": 5, "pieces": [{"name": "Key"}, {"name": "Other"}]})
    assert len(hide["pieces"]) == 1 and hide["count"] == 5, "one thing to find, five rounds"
    assert games.normalise({"kind": "nonsense", "pieces": []}, "let's play catch")["kind"] == "catch", "the kind the user named"
    assert games.normalise({"pieces": []})["pieces"][0]["shape"] == "orb", "no pieces given: one is made"
    assert games.normalise({"kind": "targets", "count": 20}, room_left=5)["count"] == 4, "the game itself stands in the room too"
    # the range: its guns and their table stand in the room too, and only guns there are
    shoot = games.normalise({"kind": "range", "count": 20, "arms": ["rifle", "phaser"]}, room_left=12)
    assert list(shoot["arms"]) == ["rifle"] and shoot["rings"] == [3, 2, 1] and shoot["count"] == 12 - 1 - 1 - 1
    assert set(games.normalise({"kind": "range", "arms": "none"}, room_left=20)["arms"]) == set(games.ARMS), "no guns named: all of them"
    # a small mind hands the prompt back: a name, a goal or a line that is a line of the prompt, or no words, is dropped
    prompt = games.words_prompt(games.quick("targets"), "let's play", None, {"rooms": {}, "spaces": {}, "objects": {}}, "core_room",
                                [{"name": "Lantern Hunt", "kind": "targets", "room": "core_room", "score": 2, "misses": 4, "seconds": 45, "when": ""}])
    echoed = games.normalise({"kind": "targets", "name": "> Quiet work goes further.", "goal": ".10.00",
                              "lines": {"start": "Go.", "hit": "- hide: one piece is hidden somewhere in the room each round",
                                        "miss": ".", "end": "Lantern Hunt (targets, core_room): 2 hits, 4 misses, 45 s,"}},
                             prompt=prompt)
    assert echoed["name"] == "Targets" and echoed["goal"] == games.GOALS["targets"]
    assert echoed["lines"] == {"start": "Go.", "hit": "", "miss": "", "end": ""}
    with pytest.raises(composer.ComposeError):
        games.normalise({"kind": "targets"}, room_left=1)
    with pytest.raises(composer.ComposeError):
        games.normalise("not a game")


# ── set in the room ──────────────────────────────────────────────────────────
def test_eli_sets_a_game_where_the_user_stands_at_once_and_the_feed_carries_it(world):
    scene = embodiment.report(SCENE)
    model = _model(WORDS)
    out = games.compose("let's play a game", scene, generate=model)
    prompt = model.prompts[0]
    for part in ("in the Core Room, with the user", "A game for the two of you has just been set", "The game: targets.",
                 'The user said: "let\'s play a game"', "You have not played together before.",
                 "Your latest insight: quiet work goes further.", "Make it about something the two of you share"):
        assert part in prompt, part
    assert out["kind"] == "game" and out["text"] == "Here is a game."
    assert out["game"]["name"] == "Lantern Hunt" and out["game"]["kind"] == "targets" and out["game"]["count"] == 8
    assert out["words"]["lines"]["hit"] == "Got one."
    state = world.load()
    game = state.objects[out["game"]["id"]]
    assert game.object_type == building.GAME and game.room == "core_room" and game.metadata["status"] == "set"
    assert game.name == "Lantern Hunt" and game.metadata["game"]["name"] == "Lantern Hunt"
    assert game.metadata["game"]["lines"]["hit"] == "Got one." and game.metadata["game"]["say"] == "Here is a game."
    assert game.symbolic_meaning == WORDS["goal"] and game.metadata["game"]["goal"] == WORDS["goal"]
    pieces = _pieces(state)
    assert len(pieces) == 8 and {p.name.split(" ")[0] for p in pieces} == {"Orb"}
    assert [p.metadata["piece"]["index"] for p in pieces] == list(range(8))
    assert all(p.metadata["piece"] == {"game": game.object_id, "role": "target", "tag": "orb",
                                       "index": p.metadata["piece"]["index"]} for p in pieces)
    for p in pieces:
        form = building.form_of(p.__dict__)
        assert form["shape"] == "orb" and form["size"] == 0.6
        far = (form["pos"][0] ** 2 + form["pos"][2] ** 2) ** 0.5
        assert 1.0 <= far <= building.REACH and 0.4 <= form["pos"][1] <= 2.2, "round the user, where a hand or a pointer reaches"
    assert games.current("core_room")["id"] == game.object_id and games.current("workshop") is None
    assert [a.action_type for a in state.actions if a.payload.get("built")] == ["create_object"] * 9, "every piece judged by the policy"
    # the feed carries the game and each piece's part in it, and the body may tell the pieces from the rest
    message = spatial_feed.spatial_state(state.to_dict(), version=2)
    sent = {o["id"]: o for o in message["objects"]}
    assert sent[game.object_id]["kind"] == "game" and sent[game.object_id]["game"]["kind"] == "targets"
    assert sent[game.object_id]["status"] == "set" and sent[game.object_id]["game"]["lines"]["start"] == "Go."
    assert sent[pieces[0].object_id]["piece"]["role"] == "target" and "game" not in sent[pieces[0].object_id]
    assert "games" in spatial_feed.CAPABILITIES[2]
    # the user's own words handed back are not an answer: the goal is said instead
    echo = games.compose("let's play a game", scene, generate=_model({**WORDS, "say": "Let's play a game!"}))
    assert echo["text"] == WORDS["goal"], "the user's own words handed back are not an answer: the goal is said"


def test_a_game_is_set_whether_or_not_a_mind_has_words_for_it(world, monkeypatch):
    scene = embodiment.report(SCENE)
    out = games.compose("let's play", scene)
    assert out["kind"] == "game" and "words" not in out and out["text"] == games.GOALS["targets"], "no mind in memory: the plain goal"
    game = world.load().objects[out["game"]["id"]]
    assert game.name == "Targets" and game.metadata["game"]["lines"] == {"start": "", "hit": "", "miss": "", "end": ""}
    # a mind that fails, or answers with no words, leaves the game as it is
    def gone(prompt):
        raise RuntimeError("GGUF model not available")
    assert games.compose("let's play", scene, generate=gone)["kind"] == "game"
    assert "words" not in games.compose("let's play", scene, generate=lambda p: {"say": "", "name": ""})
    assert "words" not in games.compose("let's play", scene, generate=lambda p: "nonsense")
    # ELI's words put on a game later, in the background: the body reads them off the game object
    out = games.compose("let's play", scene)
    put = games.apply_words(out["game"]["id"], {"name": "Night Lanterns", "lines": {"start": "Ready?", "hit": "", "miss": "", "end": ""}})
    assert put["name"] == "Night Lanterns" and put["lines"]["start"] == "Ready?" and put["lines"]["hit"] == ""
    assert games.current("core_room")["game"]["name"] == "Night Lanterns" and world.load().objects[out["game"]["id"]].name == "Night Lanterns"
    games.played(out["game"]["id"], {"score": 1})
    assert games.apply_words(out["game"]["id"], {"name": "Too late"}) is None, "a game over takes no words"
    # with a mind in memory the words are asked for in the background and arrive on the game
    monkeypatch.setattr(games, "_mind_loaded", lambda: True)
    monkeypatch.setattr(composer, "_generate", lambda prompt, grammar, loose="": {"say": "Mine.", "name": "Quiet Hunt", "goal": "", "lines": {}})
    out = games.compose("let's play", scene)
    assert "words" not in out, "set at once; the words follow"
    for _ in range(100):
        if world.load().objects[out["game"]["id"]].name == "Quiet Hunt":
            break
        time.sleep(0.02)
    assert world.load().objects[out["game"]["id"]].name == "Quiet Hunt" and games.current("core_room")["game"]["say"] == "Mine."


def test_the_range_puts_guns_on_a_table_beside_the_user_and_targets_ahead(world):
    scene = embodiment.report(SCENE)
    out = games.compose("set up the range", scene)
    assert out["game"]["kind"] == "range" and out["game"]["arms"] == sorted(games.ARMS) and out["text"] == games.GOALS["range"]
    state = world.load()
    (rack,) = _pieces(state, games.RACK)
    arms = _pieces(state, games.ARM)
    targets = _pieces(state, "target")
    table = building.form_of(rack.__dict__)
    assert table["shape"] == "table" and (table["pos"][0] ** 2 + table["pos"][2] ** 2) ** 0.5 < 1.6, "a step from the user"
    assert table["pos"][0] > 0 and table["pos"][2] > 0, "ahead, to the right"
    assert [a.metadata["piece"]["tag"] for a in arms] == list(games.ARMS) and [a.name for a in arms] == [i["name"] for i in games.ARMS.values()]
    for arm in arms:
        form = building.form_of(arm.__dict__)
        assert form["shape"] == "arm" and abs(form["pos"][1] - 0.78) < 0.01, "laid on the table's top"
        assert abs(form["pos"][0] - table["pos"][0]) < 0.75 and abs(form["pos"][2] - table["pos"][2]) < 0.75, "along it"
        assert form["turn"] == building.form_of(arms[0].__dict__)["turn"], "all pointing the same way, away from the user"
    assert len({tuple(building.form_of(a.__dict__)["pos"]) for a in arms}) == len(arms), "each in its own place, not pushed apart"
    assert len(targets) == games.FIRST["range"][0]
    for target in targets:
        form = building.form_of(target.__dict__)
        assert form["shape"] == "target" and form["pos"][2] >= 3.0 and 0.8 <= form["pos"][1] <= 2.0, "ahead, further off"
        assert 120.0 <= form["turn"] <= 240.0, "its face turned to the user"
    spec = games.current("core_room")["game"]
    assert spec["arms"]["shotgun"]["shots"] == 7 and spec["arms"]["bolt"]["fire"] == "bolt" and spec["rings"] == [3, 2, 1]
    told = games.played(out["game"]["id"], {"score": 23, "hits": 9, "shots": 14, "misses": 2, "seconds": 61.2,
                                             "arms": {"rifle": {"shots": 6, "hits": 5, "points": 13},
                                                      "pistol": {"shots": 8, "hits": 4, "points": 10},
                                                      "phaser": {"shots": 1, "hits": 1, "points": 3}}})
    assert told["result"]["arms"] == {"rifle": {"shots": 6, "hits": 5, "points": 13}, "pistol": {"shots": 8, "hits": 4, "points": 10}}
    assert told["said"] == ("You shot The range (range) in the Core Room: 23 points from 9 hits in 14 shots, 2 targets missed, "
                            "most with the rifle, in 61 seconds.")
    assert told["best"] and told["text"] == "That is your best at range so far."
    (kept,) = games.record()
    assert (kept["hits"], kept["shots"], kept["score"], kept["count"]) == (9, 14, 23, games.FIRST["range"][0])
    again = games.compose("again", scene)
    assert again["game"]["kind"] == "range" and again["game"]["count"] == 15, "went well: a step harder, and the same kind as last named"


def test_a_sorting_game_stands_in_a_row_with_its_mats_to_either_side(world):
    scene = embodiment.report(SCENE)
    out = games.compose("let's sort things", scene)
    state = world.load()
    mats = _pieces(state, games.MAT)
    pieces = _pieces(state, "piece")
    assert [m.name for m in mats] == ["Block mat", "Orb mat"] and [m.metadata["piece"]["tag"] for m in mats] == ["block", "orb"]
    assert [building.form_of(m.__dict__)["shape"] for m in mats] == ["mat", "mat"]
    left, right = (building.form_of(m.__dict__)["pos"] for m in mats)
    assert left[0] < 0 < right[0] and left[2] > 1.5 and right[2] > 1.5, "the mats to either side, ahead"
    assert [p.metadata["piece"]["tag"] for p in pieces] == ["block", "orb"] * 3, "the kinds take turns"
    row = [building.form_of(p.__dict__)["pos"] for p in pieces]
    assert all(abs(pos[2] - row[0][2]) < 0.05 for pos in row) and row[0][2] > 1.5, "in a row before the user"
    assert out["game"]["kind"] == "sort" and games.current("core_room")["game"]["mats"][0]["tag"] == "block"


def test_the_kind_the_user_names_is_the_kind_and_the_least_played_otherwise(world):
    scene = embodiment.report(SCENE)
    model = _model(WORDS)
    out = games.compose("let's play catch", scene, generate=model)
    assert "The game: catch." in model.prompts[0] and out["game"]["kind"] == "catch"
    assert all(building.form_of(o.__dict__)["pos"][1] == building.MAX_HEIGHT for o in _pieces(world.load())), "catches start high up"
    games.played(out["game"]["id"], {"score": 2})
    assert games.compose("play", scene)["game"]["kind"] == "targets", "nothing named: the kind played least, the first among equals"
    games.played(games.current("core_room")["id"], {"score": 2})
    assert games.compose("play", scene)["game"]["kind"] == "sort"


def test_a_game_already_on_gives_way_to_the_next(world):
    scene = embodiment.report(SCENE)
    first = games.compose("let's play", scene)
    second = games.compose("another game", scene)
    state = world.load()
    assert games.current("core_room")["id"] == second["game"]["id"]
    old = state.objects[first["game"]["id"]]
    assert old.retired and old.metadata["status"] == "done" and old.metadata["result"]["abandoned"]
    assert old.metadata["result"]["reason"] == "another game was set in its place"
    assert len(_pieces(state)) == 8, "the first game's pieces are gone"


# ── how it went ──────────────────────────────────────────────────────────────
def test_how_it_went_is_kept_journalled_remembered_and_a_moment_of_elis_own(world):
    scene = embodiment.report(SCENE)
    building.apply([{"op": "create", "name": "Old Lamp", "shape": "lamp", "room": "core_room"}], actor="user", asked=True,
                   default_room="core_room")
    out = games.compose("let's play", scene, generate=_model(WORDS))
    game_id = out["game"]["id"]
    told = games.played(game_id, {"score": 5, "misses": 1, "seconds": 44.6, "rounds": "x"}, now=1_790_000_000.0)
    assert told["ok"] and told["best"] and told["text"] == "That is your best at targets so far."
    assert told["result"] == {"score": 5, "misses": 1, "rounds": 0, "seconds": 44, "abandoned": False}
    assert told["said"] == "You played Lantern Hunt (targets) in the Core Room: 5 hits and 1 miss in 44 seconds."
    state = world.load()
    assert games.current("core_room") is None
    game = state.objects[game_id]
    assert game.retired and game.metadata["status"] == "done" and game.metadata["result"]["score"] == 5
    assert not any((o.metadata or {}).get("piece") for o in state.objects.values()), "its pieces are not kept"
    assert set(o.name for o in _standing(state).values()) == {"Old Lamp"}
    retired = [a for a in state.actions if a.action_type == "retire_object" and a.payload.get("built")]
    assert len(retired) == 9, "each piece, and the game, left through the world's policy"
    assert world.kept["journal"][-1]["title"] == "A game: Lantern Hunt" and "5 hits" in world.kept["journal"][-1]["body"]
    assert world.kept["memories"][-1][0] == told["said"] and world.kept["memories"][-1][1]["tags"] == ["game", "play", "vr"]
    fired = world.kept["events"][-1]
    assert fired[0] == games.EVENT_PLAYED and fired[1] == games.SOURCE and fired[2] == told["said"]
    assert fired[3]["result"]["score"] == 5 and fired[3]["game"] == game_id
    # the record reads newest first, and "undo" after a game does not bring its pieces back
    (kept,) = games.record()
    assert (kept["name"], kept["kind"], kept["score"], kept["misses"], kept["seconds"]) == ("Lantern Hunt", "targets", 5, 1, 44)
    assert kept["when"].endswith(":00") or kept["when"], "told with when it was"
    assert games.played(game_id, {"score": 99})["already"] is True, "finished once"
    building.apply([{"op": "undo"}], actor="user", asked=True)
    assert _standing(world.load()) == {}, "the undo took back the lamp, not the game"
    # the moment of it is kept as an urge, in its own words
    from eli.world import urges
    from eli.world.core.schemas import WorldEvent
    local_world_bridge._get_engine().ingest_event(WorldEvent(games.EVENT_PLAYED, games.SOURCE, told["said"], fired[3]))
    assert [(u["kind"], u["text"]) for u in urges.waiting() if u["source"] == games.SOURCE] == [("play", told["said"])]
    prompt = composer.build_prompt(None, None, world.load().to_dict(), building.OWN_ROOM, composer.ALL_OPS,
                                   {"kind": "play", "text": told["said"]})
    assert "A game you made up and played together: You played Lantern Hunt" in prompt


def test_the_next_game_is_told_how_the_last_went_and_steps_from_it(world):
    scene = embodiment.report(SCENE)
    out = games.compose("let's play", scene, generate=_model(WORDS))
    games.played(out["game"]["id"], {"score": 2, "misses": 4, "seconds": 45})
    model = _model({**WORDS, "name": "Kinder Hunt"})
    again = games.compose("again", scene, generate=model)
    assert "How the games went before, newest first:" in model.prompts[0]
    assert "- Lantern Hunt (targets, core_room): 2 hits, 4 misses, 45 s," in model.prompts[0]
    assert again["game"]["count"] == 6, "one hit in three: a step kinder"
    assert games.best_so_far("targets", 2) is False and games.best_so_far("targets", 3) is True
    assert games.best_so_far("catch", 1) is True and games.best_so_far("targets", 0) is False


def test_only_the_last_finished_games_are_kept(world, monkeypatch):
    monkeypatch.setattr(games, "KEPT", 2)
    scene = embodiment.report(SCENE)
    ids = []
    for i in range(4):
        out = games.compose("play", scene, generate=_model({**WORDS, "name": f"Game {i}"}))
        ids.append(out["game"]["id"])
        games.played(out["game"]["id"], {"score": i}, now=1_790_000_000.0 + i)
    state = world.load()
    assert [g["name"] for g in games.record()] == ["Game 3", "Game 2"]
    assert ids[0] not in state.objects and ids[1] not in state.objects


def test_stopping_with_a_body_there_lets_it_say_the_score_and_without_one_finishes_it(world):
    scene = embodiment.report(SCENE)
    out = games.compose("let's play", scene, generate=_model(WORDS))
    stopped = games.stop("core_room", said="stop the game")
    assert stopped["stopped"] and stopped["text"] == "Stopping Lantern Hunt."
    assert games.current("core_room")["status"] == "stopping", "the body posts how far it got"
    told = games.played(out["game"]["id"], {"score": 3, "misses": 0, "seconds": 12, "abandoned": True})
    assert told["said"] == "We stopped Lantern Hunt (targets) in the Core Room after 12 seconds, at 3 hits and 0 misses."
    assert not told["best"], "a game given up is not a best"
    assert games.stop("core_room")["stopped"] is False
    embodiment.forget()
    out = games.compose("let's play", embodiment._clean(SCENE), generate=_model(WORDS))
    done = games.stop("core_room")
    assert done["stopped"] and done["text"] == "Stopped Lantern Hunt." and "We stopped" in done["said"]
    assert world.load().objects[out["game"]["id"]].metadata["result"]["reason"] == "the user stopped it"


def test_a_sentence_in_the_room_goes_to_the_games_first(world, monkeypatch):
    scene = embodiment.report(SCENE)
    went = []
    real_games, real_building = games.compose, composer.compose
    monkeypatch.setattr(games, "compose", lambda text, scene, **kw: went.append(("game", text)) or {"kind": "game"})
    monkeypatch.setattr(composer, "compose", lambda text, scene, **kw: went.append(("build", text)) or {"kind": "build"})
    spatial_feed.say("let's play a game here")
    spatial_feed.say("build a lamp here")
    assert spatial_feed.say("how are you", lambda text: "well")["kind"] == "chat"
    assert went == [("game", "let's play a game here"), ("build", "build a lamp here")]
    monkeypatch.setattr(games, "compose", real_games)
    monkeypatch.setattr(composer, "compose", real_building)
    assert spatial_feed.say("stop the game", lambda text: "chat")["kind"] == "chat", "no game on: a remark"
    games.compose("let's play", scene)
    assert spatial_feed.say("stop the game")["stopped"] is True


# ── from the desktop ─────────────────────────────────────────────────────────
def test_from_the_desktop_a_game_needs_the_rooms_or_someone_in_them(world):
    from eli.execution.routing.intents.world_games import route_world_games
    route = lambda s: route_world_games(s, s, s.lower(), s.lower())  # noqa: E731
    embodiment.forget()
    assert route("let's play a game") is None, "nobody in a room: conversation"
    assert route("stop the game") is None
    assert route("let's play a game in VR")["action"] == "PLAY_GAME"
    assert route("set up a catch game in your room")["args"]["kind"] == "catch"
    assert route("play some music in vr") is None
    embodiment.report(SCENE)
    assert route("let's play a game")["action"] == "PLAY_GAME" and route("I give up")["action"] == "STOP_GAME"
    assert route("hide something for me to find")["args"]["kind"] == "hide"
    assert route("set up the range")["args"]["kind"] == "range"
    import eli.execution.router_enhanced as R
    assert R.route("I give up")["action"] == "STOP_GAME", "with someone in the rooms, before the chat guards"
    assert R.route("hide something for me to find")["action"] == "PLAY_GAME"
    embodiment.forget()
    assert R.route("I give up")["action"] != "STOP_GAME", "nobody in the rooms and no game on: a remark"
    from eli.execution.effectors import world_games as effector
    embodiment.forget()
    said = effector._h_play_game("PLAY_GAME", {"text": "let's play"})
    assert not said["ok"] and "Open ELI VR" in said["response"]
    embodiment.report(SCENE)
    import eli.world.games as module
    module_compose = module.compose
    module.compose = lambda text, scene, **kw: module_compose(text, scene, generate=_model(WORDS))
    try:
        said = effector._h_play_game("PLAY_GAME", {"text": "let's play"})
    finally:
        module.compose = module_compose
    assert said["ok"] and said["response"] == "Here is a game. (Lantern Hunt: targets, 60 seconds, in Core Room)"
    stopped = effector._h_stop_game("STOP_GAME", {})
    assert stopped["ok"] and stopped["response"] == "Stopping Lantern Hunt."
