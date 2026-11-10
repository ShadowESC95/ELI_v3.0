"""A record of ELI's in the user's hand (eli/world/embodiment.py): in ELI's rooms the user picks up a
sheet (a thought, a belief, a memory, a finding), the body reports it with the scene, ELI's prompt
carries what it says and what is on record behind it, and a question about it stays conversation
(eli/execution/routing/intents/world_held.py) answered from that record."""
from __future__ import annotations

import pytest

from eli.cognition import thoughts as th
from eli.world import embodiment


@pytest.fixture()
def quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(th, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    monkeypatch.setattr(embodiment, "_remember", lambda title, text: True)
    monkeypatch.setattr(embodiment, "_measure_headset", lambda: None)
    embodiment.forget()
    yield
    embodiment.forget()


def _scene(holding):
    return {"surface": "eli_vr", "room": "memory_archive", "room_name": "Memory Archive", "headset": True,
            "user": {"pos": [0.0, 1.7, 0.0], "facing": [0.0, 1.0], "looking_at": {"kind": "object", "name": "a sheet"}},
            "holding": holding}


def test_a_thought_sheet_in_the_users_hand_is_in_elis_prompt_with_its_record(quiet):
    tid = th.record("recall", "you prefer early starts", why=["it matches 'morning'", "you said it twice"], chance=0.72,
                    decision="shown", reason="readiest to mind", ref="recall:r1:7", now=1_800_000_000.0)
    th.settle("recall:r1:7", "right", how="nobody corrected it")
    embodiment.report(_scene({"id": f"thought_{tid}", "kind": "thought", "name": "recall: you prefer early starts",
                              "lines": ["you prefer early starts", "shown: readiest to mind", "chance 72% · proved right"]}))
    held = embodiment.held()
    assert held == {"id": f"thought_{tid}", "kind": "thought", "name": "recall: you prefer early starts",
                    "lines": ["you prefer early starts", "shown: readiest to mind", "chance 72% · proved right"]}
    block = embodiment.context_block()
    assert ('In their hand: a thought sheet of yours, "recall: you prefer early starts". It reads: you prefer early starts / '
            "shown: readiest to mind / chance 72% · proved right") in block
    for line in ("A recall thought you had: you prefer early starts",
                 "It came up because it matches 'morning'; you said it twice.",
                 "You decided: shown, because readiest to mind.", "How sure you were: 72%.",
                 "What became of it: it proved right (nobody corrected it).",
                 "answer from that record: why it came up, how sure you were"):
        assert line in block, line
    assert "They are looking at: a sheet." in block
    # a sheet whose record is gone still reads as the body reports it, and nothing is filled in
    embodiment.report(_scene({"id": "thought_999999", "kind": "thought", "name": "gone", "lines": ["what it said"]}))
    block = embodiment.context_block()
    assert 'In their hand: a thought sheet of yours, "gone". It reads: what it said' in block and "A recall thought" not in block
    # nothing in hand: nothing said of one
    embodiment.report({k: v for k, v in _scene(None).items() if k != "holding"})
    assert embodiment.held() is None and "In their hand" not in embodiment.context_block()
    assert "holding" not in embodiment._clean({"holding": {"kind": "thought"}}), "a sheet with no id is not one"


def test_a_question_about_the_sheet_in_hand_stays_conversation(quiet):
    from eli.execution.routing.intents.world_held import route_world_held
    route = lambda s: route_world_held(s, s, s.lower(), s.lower())  # noqa: E731
    assert route("why did you think that?") is None, "nothing in hand: the other stages decide"
    embodiment.report(_scene({"id": "thought_3", "kind": "thought", "name": "a thought", "lines": []}))
    for said in ("why did you think that?", "how sure were you about this", "is this still right", "what became of it",
                 "why did you drop this memory", "tell me about this note", "explain this one", "when was that"):
        got = route(said)
        assert got and got["action"] == "CHAT" and got["meta"]["matched_by"] == "router.held_note_chat", said
        assert got["meta"]["held"] == "thought_3" and got["meta"]["spoken_as_remark"] is True
    for said in ("open spotify", "what's the weather", "why is the sky blue", "put it on the table"):
        assert route(said) is None, said
    import eli.execution.router_enhanced as R
    assert R.route("why did you think that?")["meta"]["matched_by"] == "router.held_note_chat", "before the self-report stages"
    embodiment.forget()
    assert route("why did you think that?") is None


def test_a_held_belief_memory_and_finding_are_read_from_their_records(quiet, monkeypatch):
    import sqlite3
    import eli.memory.memory as memory_module
    from eli.memory import claims
    from eli.world import shown
    conn = sqlite3.connect(":memory:")
    claims.record(conn, "lives_in", "Harbour Town", origin="user_said", now=1_780_100_000.0)
    claims.record(conn, "lives_in", "Upper Ridge", origin="eli_said", now=1_780_200_000.0)
    (held_claim,) = [r for r in claims.current(conn) if r["value"] == "Harbour Town"]

    class Memory:
        def claims_current(self):
            return [{**held_claim, "id": 4}]

        def memory_facts(self, mid):
            return {"id": mid, "text": "The user keeps their invoices by year.", "when": 1_780_000_000.0, "source": "user",
                    "importance": 0.8, "helped": 3, "corrected": 1} if mid == 7 else {}

    monkeypatch.setattr(memory_module, "get_memory", lambda: Memory())
    monkeypatch.setattr(shown, "items", lambda now=None: [{"id": "found_9", "kind": "found", "name": "Searched: when do the tides turn",
                                                           "lines": ["Tide times explained", "    example.org"]}])
    belief = embodiment.held_record({"id": "claim_4", "kind": "claim"})
    assert belief[0].startswith("A belief of yours about the user: lives in: Harbour Town") and belief[1].startswith("Its basis:")
    assert any(line.startswith("The chance it is right, as you measure it:") for line in belief)
    assert belief[-1] == "Something else you were told disputes it."
    memory = embodiment.held_record({"id": "memory_7", "kind": "memory"})
    assert memory[0] == "A memory of yours: The user keeps their invoices by year."
    assert memory[1].startswith("Kept on ") and memory[1].endswith(", from user.")
    assert memory[2] == "How much it matters to you: 80%; it helped 3 answers and was corrected 1 time."
    assert embodiment.held_record({"id": "memory_8", "kind": "memory"}) == [], "no such memory: nothing filled in"
    found = embodiment.held_record({"id": "found_9", "kind": "found"})
    assert found == ["What you found for the user: Searched: when do the tides turn.", "Tide times explained", "    example.org"]
    assert embodiment.held_record({"id": "goal_abc", "kind": "goal"}) == [] and embodiment.held_record({}) == []


def test_a_book_in_the_users_hand_is_in_elis_prompt_with_what_it_is_and_its_record(quiet, tmp_path, monkeypatch):
    import json
    from tests._world_records import DAY, T0, Records
    from eli.runtime import lessons
    from eli.world import stories
    Records(tmp_path, monkeypatch)
    kept = lessons.propose("OPEN_APP", "the app is not installed", ["Notes failed twice"], "open the site instead", now=T0)
    lessons.record_outcome(kept, False, now=T0 + DAY)
    embodiment.report(_scene({"id": f"book_lesson_{kept}", "kind": "book", "name": "open app: open the site instead",
                              "lines": ["When: the app is not installed"]}))
    block = embodiment.context_block()
    assert 'In their hand: a book of yours, "open app: open the site instead". It reads: When: the app is not installed' in block
    assert 'A book of yours (lesson): "open app: open the site instead"' in block
    assert "It is a lesson about OPEN_APP: active, checked 1 time, it helped 0 and harmed 1." in block
    folder = stories.folder()
    folder.mkdir(parents=True)
    (folder / "abc123def456.json").write_text(json.dumps({
        "id": "abc123def456", "title": "The Blue Kettle", "text": "The kettle waited on the sill.", "fiction": True,
        "drawn_from": [{"ref": "conversation:x", "words": "the conversation of a Monday"}],
        "about_from": T0, "about_to": T0 + 60, "written_at": T0 + DAY, "model": "small-model"}))
    embodiment.report(_scene({"id": "book_story_abc123def456", "kind": "book", "name": "The Blue Kettle", "lines": []}))
    block = embodiment.context_block()
    assert "You wrote it as fiction, with small-model, from the conversation of a Monday." in block
    embodiment.report(_scene({"id": "book_story_000000000000", "kind": "book", "name": "Gone", "lines": ["a page"]}))
    block = embodiment.context_block()
    assert 'a book of yours, "Gone". It reads: a page' in block and "A book of yours (" not in block, "nothing filled in"
