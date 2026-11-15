"""ELI's records as books for its rooms' shelves (eli/world/books.py): every book is a record of the
user's or of ELI's own, under a title, cut into sections in the record's own words; one is read whole by
its id, and another member's records are never one of the owner's books. Every store is the test's own
(tests/_world_records.py); every word is invented."""
from __future__ import annotations

import json

from eli.world import books
from tests._world_records import DAY, T0, records  # noqa: F401 - the fixture


def _by_of(listed):
    out = {}
    for book in listed:
        out.setdefault(book["of"], []).append(book)
    return out


def test_the_archive_keeps_a_book_of_every_conversation_session_failure_and_week(records, monkeypatch):
    records.turn("s1", "user", "Where did I leave the blue kettle?", T0)
    records.turn("s1", "assistant", "On the windowsill, you said.", T0 + 60)
    records.turn("s2", "user", "Plan a walk by the river.", T0 + DAY)
    records.turn("s2", "assistant", "The east path is flat.", T0 + DAY + 30)
    records.turn("s9", "user", "a member's own words", T0 + DAY, user_id="member-7")
    records.summary("s2", "We planned a river walk. The east path won.", T0 + DAY, T0 + DAY + 30, 2)
    records.ran("OPEN_APP", False, T0 + 10, session="s1", said="Notes is not installed")
    records.ran("OPEN_URL", True, T0 + 20, session="s1", said="Opened notes.example.org",
                args={"_alternative_of": "OPEN_APP"})
    records.ran("WEB_SEARCH", True, T0 + DAY + 5, session="s2", said="Searched: river paths")
    from eli.memory import policy
    monkeypatch.setattr(policy, "rbac_members", lambda: frozenset({"member-7"}))   # one of the install's members
    listed = books.listed()
    by = _by_of(listed)
    talks = sorted(by["conversation"], key=lambda b: b["from"])
    assert [b["room"] for b in talks] == ["memory_archive", "memory_archive"], "a member's conversation is not the owner's"
    assert talks[0]["about"] == "2 turns" and talks[0]["from"] == T0 and talks[0]["to"] == T0 + 60
    assert talks[1]["title"] == "We planned a river walk.", "a conversation is titled by what it came to"
    whole = books.read(talks[0]["id"])
    assert [s["text"] for s in whole["sections"]] == ["Where did I leave the blue kettle?", "On the windowsill, you said."]
    assert whole["sections"][0]["heading"].startswith("You, ") and whole["sections"][1]["heading"].startswith("ELI, ")
    assert whole["sections"][0]["when"] == T0 and "session" not in whole, "the session id is not sent"
    [session] = by["session"]
    assert session["sections"] == [{"heading": "", "when": T0 + DAY + 30, "text": "We planned a river walk. The east path won."}]
    [log] = by["log"]
    assert log["title"] == "Notes is not installed" and log["about"] == "1 of 2 actions failed"
    assert [s["text"] for s in log["sections"]][1] == "Opened notes.example.org\ntried instead of open app"
    assert log["sections"][0]["text"].startswith("Notes is not installed\nfailed: ")
    weeks = by["period"]
    assert sum(len(w["sections"]) for w in weeks) == 2, "a section for each day ELI talked or acted"
    assert all(w["title"].startswith("The week of ") for w in weeks)
    text = "\n".join(s["text"] for w in weeks for s in w["sections"])
    assert "open app ×1 (1 failed)" in text and "web search ×1" in text
    assert books.read("book_conversation_nothing") is None and books.read("not a book") is None
    assert all(b["id"].startswith(f"book_{b['of']}_") and " " not in b["id"] for b in listed), "ids go in a path"


def test_elis_own_room_keeps_its_likings_beliefs_doings_looking_back_goals_and_diary(records):
    from eli.cognition import thoughts
    from eli.planning import goal_store
    from eli.world.persistence.journal import append_journal_entry
    records.stance("rain sounds", "I am fond of the sound of rain on a roof.", T0, valence=1)
    records.stance("loud alarms", "I don't care for alarms that shout.", T0 + 10, valence=-1)
    records.stance("machine minds", "I am not a person, and I do not pretend to be.", T0 + 20)
    records.memory_row("Looking back, short answers landed better this week.", T0 + 30, kind="reflection")
    thoughts.record("act", "made a lamp for the reading corner", chance=0.6, decision="taken", now=T0 + 40)
    thoughts.record("note", "Ask before moving the desk.", decision="pinned", now=T0 + 50)
    thoughts.record("route", "read a sentence as a command", chance=0.5, decision="held", now=T0 + 60)
    append_journal_entry("A note from the rooms", "The reading corner is warmer now.")
    goal = goal_store.upsert_goal({"title": "Tidy the index", "objective": "Fewer duplicate rows.",
                                   "success_criteria": ["no duplicate rows"],
                                   "metadata": {"work": {"status": "active", "steps": [
                                       {"at": T0, "action": "SCAN", "outcome": "done", "detail": "found 3"},
                                       {"at": T0 + 60, "action": "MERGE", "outcome": "failed", "detail": "locked"}]}}})
    (records.root / "insight.json").write_text(json.dumps({"insight": "Mornings go better unhurried.", "ts": T0 + 70}))
    by = _by_of(books.listed())
    assert [b["title"] for b in by["likes"]] == ["I am fond of the sound of rain on a roof."]
    assert [b["title"] for b in by["dislikes"]] == ["I don't care for alarms that shout."]
    assert [b["title"] for b in by["beliefs"]] == ["I am not a person, and I do not pretend to be."], \
        "a position not judged a liking stands with the beliefs"
    assert all(b["room"] == "own_room" and b["made_by"] == "eli" for of in books.OWN_SHELVES for b in by.get(of, []))
    assert by["likes"][0]["sections"][0]["text"] == "I am fond of the sound of rain on a roof."
    assert [b["title"] for b in by["thoughts"]] == ["made a lamp for the reading corner"], "ELI's own doings, not its routing"
    reflections = {b["title"] for b in by["reflection"]}
    assert reflections == {"Looking back, short answers landed better this week.", "Mornings go better unhurried."}
    [diary] = [b for b in by["diary"] if any("Ask before moving the desk." in s["text"] for s in b["sections"])]
    [goal_book] = by["goals"]
    assert goal_book["title"] == "Tidy the index" and goal_book["about"] == "active, 1 of 2 steps done"
    assert goal_book["progress"] == 0.5 and goal_book["from"] == goal.created_at
    steps = next(s for s in goal_book["sections"] if s["heading"] == "Steps")
    assert steps["text"].splitlines()[1].endswith("MERGE: failed — locked")
    assert any(s["heading"] == "Done when" and s["text"] == "no duplicate rows" for s in goal_book["sections"])
    journal = [b for b in by["diary"] if any(s["heading"] == "A note from the rooms" for s in b["sections"])]
    assert journal and journal[0]["sections"][0]["text"] == "The reading corner is warmer now."


def test_the_evidence_wall_keeps_a_case_file_for_every_lesson_with_its_record(records):
    from eli.runtime import lessons
    kept = lessons.propose("OPEN_APP", "the app is not installed", ["Notes failed twice"], "open the site instead",
                           hypothesis="the site does the same", predicted_outcome="the page opens", now=T0)
    lessons.record_outcome(kept, True, now=T0 + DAY)
    [case] = [b for b in books.listed() if b["of"] == "lesson"]
    assert case["id"] == f"book_lesson_{kept}" and case["room"] == "evidence_wall" and case["made_by"] == "lessons"
    assert case["applies_to"] == "OPEN_APP" and case["title"] == "open app: open the site instead"
    assert case["record"] == {"status": "active", "checks": 1, "helped": 1, "harmed": 0, "retired_reason": ""}
    assert case["from"] == T0 and case["to"] == T0 + DAY
    headings = [s["heading"] for s in case["sections"]]
    assert headings == ["When", "What was seen", "Why", "What to do", "What should follow", "How it has gone"]
    assert case["sections"][1]["text"] == "Notes failed twice"
    # what it says to do is sent whole beside the title, which is cut to fit a cover
    long = "before opening an app by its name, look the name up among the installed apps and open the site when it is not there"
    lessons.propose("OPEN_APP", "the app is not installed again", ["Notes failed"], long, now=T0 + 2 * DAY)
    wordy = next(b for b in books.listed() if b["of"] == "lesson" and b["id"] != case["id"])
    assert wordy["title"].endswith("…") and wordy["change"] == long and case["change"] == "open the site instead"


def test_a_story_elis_model_wrote_stands_with_who_wrote_it_and_what_it_came_from(records):
    from eli.world import stories
    folder = stories.folder()
    folder.mkdir(parents=True)
    (folder / "abc123def456.json").write_text(json.dumps({
        "id": "abc123def456", "title": "The Kettle", "text": "It sat on the sill. Nobody minded.", "fiction": True,
        "drawn_from": [{"ref": "conversation:x", "words": "the conversation of Mon 21 Sep 2026 14:13"}],
        "about_from": T0, "about_to": T0 + 60, "written_at": T0 + DAY, "model": "small-model"}))
    (folder / "broken.json").write_text("not json")
    [story] = [b for b in books.listed() if b["of"] == "story"]
    assert story["by"] == "ELI, with small-model" and story["fiction"] is True
    assert story["drawn_from"] == ["the conversation of Mon 21 Sep 2026 14:13"] and story["from"] == T0
    assert story["sections"][0]["text"] == "It sat on the sill. Nobody minded."


def test_a_record_that_cannot_be_read_gives_no_books_of_its_own(records, monkeypatch):
    records.turn("s1", "user", "Hello there, kettle.", T0)
    monkeypatch.setattr(books, "_lessons", lambda: (_ for _ in ()).throw(OSError("locked")))
    by = _by_of(books.listed())
    assert "conversation" in by and "lesson" not in by
