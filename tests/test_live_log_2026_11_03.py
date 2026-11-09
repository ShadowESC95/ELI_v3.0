"""What went wrong in a live session of 3 November 2026 (the GUI conversation and the audit chain),
each held here so it stays fixed. The sentences are invented ones, of the same shape as those that
went wrong."""
from __future__ import annotations

import time

import pytest

from eli.execution.router_enhanced import route, when_said_topic
from eli.runtime.command_splitter import instruction, sentences


def _action(said: str) -> str:
    got = route(said)
    return str(got.get("action") if isinstance(got, dict) else getattr(got, "action", ""))


def test_a_remark_joined_to_an_instruction_by_a_slash_is_split():
    said = "it's all easy going here, no rush/ open Sudoku and pause the music"
    assert sentences(said) == ["it's all easy going here, no rush", "open Sudoku and pause the music"]
    assert instruction("open Sudoku and pause the music")
    assert sentences("open firefox/chrome and steam") == ["open firefox/chrome and steam"]
    assert sentences("the ratio is 16/9 so make it wider") == ["the ratio is 16/9 so make it wider"]


def test_starting_the_audit_is_the_audit_however_it_is_said():
    for said in ("yes start the audit please", "run_audit", "run the audit", "ok do a full audit", "audit yourself",
                 "so why did you tell me to type run_audit in order to run the audit?! that is what you said!"):
        assert _action(said) == "RUNTIME_AUDIT", said
    assert _action("the audit of the accounts is due") != "RUNTIME_AUDIT"


def test_a_sentence_with_paste_in_it_is_not_a_clipboard_read():
    said = ("paste what, i can't paste a phone call with you in it.. never mind then, we will sort that out "
            "another time. how are you doing? have you noticed ANY changes or updates in your code?")
    for part in sentences(said):
        assert _action(part) not in ("GET_CLIPBOARD", "SET_CLIPBOARD"), part
    assert _action("what's in my clipboard") == "GET_CLIPBOARD"
    assert _action("copy hello there to the clipboard") == "SET_CLIPBOARD"


def test_asking_for_the_record_of_a_thing_said_is_a_when_said_question():
    for said in ("i was asking when exactly that message was from, do you have logs eli?",
                 "we are testing eli, can i have the full logs from that comment on the 12th please?",
                 "do you have logs eli?", "give me the transcript of that conversation"):
        assert when_said_topic(said) == "", said
    for said in ("i need the logs for the build", "do you have logs of the build server", "show me the error logs"):
        assert when_said_topic(said) is None, said


def test_remembering_the_one_eli_just_named_goes_to_memory():
    got = route("you remember him, Eli?")
    assert got["action"] == "MEMORY_RECALL" and got["args"] == {"query": "", "referent": "last_reply"}
    assert _action("do you remember that one?") == "MEMORY_RECALL"
    assert _action("you remember the lamp i asked for?") != "MEMORY_RECALL"


def test_updates_to_elis_own_code_are_answered_from_the_record():
    for said in ("notice any updates to your code, voice, plugins etc?",
                 "can you not check the diff, or check your status logs before the install and after the update?",
                 "what version are you", "have you been updated"):
        assert _action(said) == "VERSION_CHANGES", said
    assert _action("any updates to the kitchen plan?") != "VERSION_CHANGES"


def test_the_version_answer_is_held_to_what_is_recorded(tmp_path, monkeypatch):
    from eli.runtime import version_record
    monkeypatch.setattr(version_record, "_seen_path", lambda: tmp_path / "versions_seen.json")
    monkeypatch.setattr(version_record, "current_version", lambda: "3.0.149")
    monkeypatch.setattr(version_record, "_last_examination", lambda: "")
    now = time.time()
    version_record.note_seen("3.0.148", now - 3 * 86400)
    version_record.note_seen(now=now)
    told = version_record.told(now)
    assert told.startswith("I'm ELI v3 3.0.149, first seen here on") and "(today)" in told
    assert "Before that: 3.0.148" in told and "I keep no diff of my own code" in told
    assert version_record.note_seen("3.0.149") == version_record.note_seen(), "noted once"


def test_a_patch_to_elis_own_code_that_nothing_made_is_not_served():
    from eli.cognition import self_claims
    text = ("Fair point. Here is the patch for `router_enhanced.py`.\n\n**File:** /somewhere/eli/execution/router_enhanced.py\n"
            "```diff\n--- a/eli/execution/router_enhanced.py\n+++ b/eli/execution/router_enhanced.py\n-x = 1\n+x = 2\n```\n\nThat is all.")
    out = "".join(self_claims.gate_stream([text], "ok; routed by fallback.chat; model ran 1 time"))
    assert "x = 2" not in out and "Here is the patch" not in out and self_claims.NO_PATCH_NOTE in out
    assert "That is all." in out, "what follows the block is kept"
    kept = "".join(self_claims.gate_stream(["Here is the code:\n```python\nprint(1)\n```\nDone."], ""))
    assert "print(1)" in kept, "code that is not a patch to ELI passes as before"


def test_recall_is_told_in_words_with_the_thing_asked_about_first():
    from eli.execution.effectors.memory_ops import _plain_hit, _recall_in_words
    assert _plain_hit("[earlier starts to the day] (value)\n User —[prefers]→ earlier starts to the day (as of 14 Oct 2026)") \
        == "you prefer earlier starts to the day (as of 14 Oct 2026)"
    assert _plain_hit("[Tue 27 Oct 13:01, 7 days ago] Fair point, the cello is my favourite instrument") \
        == "Tue 27 Oct 13:01 (7 days ago): you said “Fair point, the cello is my favourite instrument”"
    assert _plain_hit("[Tue 27 Oct 13:02, 7 days ago] Session digest (20 turns): topics — haha") == ""
    told = _recall_in_words("the cello", [
        "[earlier starts to the day] (value)\n User —[prefers]→ earlier starts to the day (as of 14 Oct 2026)",
        "[Tue 27 Oct 13:01, 7 days ago] Fair point, the cello is my favourite instrument"])
    assert told.splitlines()[0] == "What I have on “the cello”:" and "the cello" in told.splitlines()[1]


def test_a_reply_denying_what_the_search_found_gives_way_to_the_results():
    from eli.runtime.web_answer_check import denies_what_was_found, from_the_results
    results = [{"title": "The Lantern Keeper (2026) - IMDb", "body": "Directed by Ada Morrow. With Felix Hart.",
                "href": "https://www.imdb.com/title/tt1/"}]
    query = "the lantern keeper 2026 movie"
    assert denies_what_was_found("Because there is no Lantern Keeper movie scheduled for 2026. I searched. Nothing came back.",
                                 results, query)
    assert not denies_what_was_found("The newest one is The Lantern Keeper (2026), directed by Ada Morrow.", results, query)
    assert not denies_what_was_found("There is no such thing.", [{"title": "Best Buy breakfast deals", "body": ""}], query), \
        "results that do not carry the question are not forced on the reply"
    told = from_the_results(query, results)
    assert told.startswith("What the web has on “the lantern keeper 2026 movie” just now:") and "(imdb.com)" in told
