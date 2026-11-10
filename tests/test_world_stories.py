"""What ELI writes of its own records when nobody needs it (eli/world/stories.py): a short story from the
newest conversation no story was drawn from, in its model's words, kept on disk in its own world folder
and marked as fiction or as it happened with the records it came from; whether a position it holds is a
liking; its own names for its shelves; and none of it while ELI is not free. The model here is a stand-in
that answers as told; every store is the test's own and every word invented."""
from __future__ import annotations

import json

from eli.world import books, stories
from tests._world_records import DAY, T0, records  # noqa: F401 - the fixture

STORY = "TITLE: The Blue Kettle\nTOLD: fiction\nThe kettle waited on the sill. It had seen worse mornings."


def _talk(records):
    records.turn("s1", "user", "Where did I leave the blue kettle?", T0)
    records.turn("s1", "assistant", "On the windowsill, you said.", T0 + 60)
    records.turn("s0", "user", "My cousin Ada moved to Lowmarsh.", T0 - DAY)       # an older conversation


def test_a_story_is_written_once_from_the_newest_untold_conversation_and_kept_on_disk(records, monkeypatch):
    _talk(records)
    from eli.cognition import thoughts
    thoughts.record("act", "made a lamp for the sill", decision="taken", now=T0 + 30)
    asked = []
    monkeypatch.setattr(stories, "_model_name", lambda: "small-model")
    story = stories.write_story(lambda prompt: asked.append(prompt) or STORY, now=T0 + DAY)
    assert story["title"] == "The Blue Kettle" and story["fiction"] is True
    assert story["text"] == "The kettle waited on the sill. It had seen worse mornings."
    assert [d["ref"].split(":")[0] for d in story["drawn_from"]] == ["conversation", "thought"]
    assert story["drawn_from"][0]["words"].startswith("the conversation of ") and story["about_from"] == T0
    [prompt] = asked
    assert "Where did I leave the blue kettle?" in prompt and "Made a lamp for the sill" in prompt
    assert "Ada" not in prompt and "Lowmarsh" not in prompt, "only the records the story is drawn from"
    assert "add nothing about them that is not here" in prompt
    on_disk = json.loads((stories.folder() / f"{story['id']}.json").read_text())
    assert on_disk == story and str(stories.folder()).startswith(str(records.root / "world")), "ELI v3's own world folder"
    [book] = [b for b in books.listed() if b["of"] == "story"]
    assert book["by"] == "ELI, with small-model" and book["fiction"] is True and book["title"] == "The Blue Kettle"
    # the next one is of the older conversation, and with none left untold nothing is written
    second = stories.write_story(lambda prompt: asked.append(prompt) or STORY.replace("fiction", "as it happened"))
    assert second["fiction"] is False and "Lowmarsh" in asked[-1]
    assert stories.write_story(lambda prompt: asked.append(prompt) or STORY) is None and len(asked) == 2
    assert len(stories.kept()) == 2


def test_an_answer_that_is_not_a_story_is_not_kept_and_one_cut_short_keeps_its_whole_sentences(records):
    _talk(records)
    assert stories.write_story(lambda prompt: "") is None, "no model, or a turn came: nothing written"
    assert stories.material() is not None, "and the conversation waits for the next time"
    assert stories.write_story(lambda prompt: "Once upon a time there was a kettle.") is None
    assert stories.kept() == [], "not in the shape asked: not kept"
    cut = stories.write_story(lambda prompt: "TITLE: Sill\nTOLD: as it happened\nYou asked about the kettle. I said the sill. Then")
    assert cut["text"] == "You asked about the kettle. I said the sill.", "a story stopped at its cap keeps whole sentences"
    records.turn("s2", "user", "Good night.", T0 + DAY)
    asked = []
    assert stories.write_story(lambda prompt: asked.append(prompt) or "NOTHING") is None, "ELI may find no story in it"
    assert "Good night." in asked[0] and stories.material() is None, "and is not asked of the same records again"
    assert len(stories.kept()) == 1


def test_whether_a_held_position_is_a_liking_is_elis_models_answer(records):
    liked = records.stance("rain sounds", "I am fond of the sound of rain on a roof.", T0)
    other = records.stance("machine minds", "I am not a person, and I do not pretend to be.", T0 + 10)
    asked = []
    assert stories.judge_stance(lambda prompt: asked.append(prompt) or "neither, it is a claim") == 0
    assert stories.judge_stance(lambda prompt: asked.append(prompt) or "Like.") == 1
    assert stories.judge_stance(lambda prompt: asked.append(prompt) or "like") is None, "every position judged"
    valences = {s["id"]: s["valence"] for s in records.memory.stances()}
    assert valences == {other: 0, liked: 1}
    assert "I am not a person" in asked[0] and len(asked) == 2
    [like] = [b for b in books.listed() if b["of"] == "likes"]
    assert like["title"] == "I am fond of the sound of rain on a roof."


def test_elis_shelves_get_its_own_names_for_the_kinds_that_have_books(records):
    records.stance("rain sounds", "I am fond of the sound of rain on a roof.", T0, valence=1)
    records.stance("machine minds", "I am not a person, and I do not pretend to be.", T0 + 10)
    asked = []
    names = stories.name_shelves(lambda prompt: asked.append(prompt) or "likes: Small Joys\nbeliefs: \"What I Hold\"")
    assert names == {"likes": "Small Joys", "beliefs": "What I Hold"} and stories.shelf_names() == names
    assert "likes:" in asked[0] and "beliefs:" in asked[0] and "diary" not in asked[0], "only shelves with books"
    by = {b["of"]: b for b in books.listed() if b["room"] == "own_room"}
    assert by["likes"]["shelf"] == "Small Joys" and by["beliefs"]["shelf"] == "What I Hold"
    assert stories.name_shelves(lambda prompt: asked.append(prompt) or "") == names and len(asked) == 1, "named once"


def test_elis_writing_waits_until_it_is_free(records, monkeypatch):
    from eli.world import urges
    _talk(records)
    ran = []
    monkeypatch.setattr(stories, "write_story", lambda: ran.append("story"))
    monkeypatch.setattr(stories, "judge_stance", lambda: ran.append("stance"))
    monkeypatch.setattr(stories, "name_shelves", lambda: ran.append("shelves"))
    monkeypatch.setattr(urges, "free", lambda: False)
    assert stories.write_when_free() == {} and ran == []
    monkeypatch.setattr(urges, "free", lambda: True)
    stories.write_when_free()
    assert ran == ["story", "stance", "shelves"]
    # ELI's own background loop writes after it acts
    ran.clear()
    monkeypatch.setattr(urges, "act", lambda scene=None: {"results": []})
    monkeypatch.setattr(urges, "_day_turned", lambda now=None: None)
    assert urges.act_when_free() == {"results": []} and ran == ["story", "stance", "shelves"]


def test_a_turn_that_comes_while_eli_writes_leaves_nothing_written(records, monkeypatch):
    from eli.cognition import inference_broker

    class Broker:
        gguf_ready = True

        def infer(self, prompt, system="", background=False, **kw):
            assert background, "ELI's writing is background work: it waits out the owner's pace and a turn stops it"
            return STORY

    _talk(records)
    monkeypatch.setattr(inference_broker, "get_inference_broker", lambda: Broker())
    monkeypatch.setattr(inference_broker, "foreground_in_progress", lambda: True)
    assert stories.write_story() is None and stories.kept() == []
    monkeypatch.setattr(inference_broker, "foreground_in_progress", lambda: False)
    monkeypatch.setattr(inference_broker, "foreground_recently_active", lambda window=None: False)
    monkeypatch.setattr(stories, "_model_name", lambda: "")
    assert stories.write_story()["title"] == "The Blue Kettle"
    assert stories.by(stories.kept()[0]) == "ELI"
