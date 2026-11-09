"""The command language (eli/execution/routing/command_parser.py): a sentence read by its form.

The parser runs twice in the router: a strict pass ahead of the rules, for commands whose object is
an atom, and a lenient pass after them, for what no rule took. The tests hold it to three things:
it must not change what the rules already route (the catalogue's own phrases and the eval corpus
are the yardstick), it must take the paraphrases the rules miss, and its vocabulary must be the
catalogue's."""
from __future__ import annotations

import os
import time
from functools import lru_cache

import pytest

os.environ.setdefault("ELI_HEADLESS", "1")

from eli.execution.routing import command_parser as cp
from eli.execution.router_enhanced import route


# ── the atoms ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("said, digits", [
    ("five minutes", "5 minutes"),
    ("twenty five minutes", "25 minutes"),
    ("twenty-five minutes", "25 minutes"),
    ("a hundred seconds", "100 seconds"),
    ("half an hour", "30 minutes"),
    ("an hour and a half", "90 minutes"),
    ("quarter of an hour", "15 minutes"),
    ("an hour", "1 hour"),
    ("a couple of minutes", "2 minutes"),
])
def test_numbers_said_in_words_are_read_as_digits(said, digits):
    assert cp.in_digits(said) == digits


@pytest.mark.parametrize("said, seconds", [
    ("10 minutes", 600),
    ("1 hour 30 minutes", 5400),          # the rules took the first part only
    ("two and a half hours", 9000),
    ("45 seconds", 45),
    ("1h", 3600),
    ("a timer", None),
])
def test_a_duration_is_the_sum_of_its_parts(said, seconds):
    assert cp.duration_seconds(said) == seconds


@pytest.mark.parametrize("said, when", [
    ("7am", "07:00"),
    ("7:30pm", "19:30"),                   # the rules dropped the pm
    ("half past six", "06:30"),
    ("quarter to seven", "06:45"),
    ("at 6 o'clock", "06:00"),
    ("noon", "12:00"),
    ("midnight", "00:00"),
    ("soon", None),
])
def test_a_time_of_day_is_read_as_the_agenda_reads_it(said, when):
    assert cp.clock_time(said) == when


# ── the form of a command ────────────────────────────────────────────────────────────────────────
def test_what_stands_in_front_of_a_command_is_passed_over():
    cmd = cp.read("ok, could you please fire up spotify for me")
    assert cmd is not None
    assert (cmd.verb, cmd.said, cmd.object) == ("open", "fire up", "spotify")


def test_a_said_form_may_be_several_verbs_and_the_frames_decide():
    cmd = cp.read("start a timer for twenty minutes")
    assert cmd is not None and set(cmd.verbs) == {"open", "set"}


def test_a_question_or_a_statement_is_not_a_command():
    assert cp.read("did you open spotify?") is None
    assert cp.read("it stopped playing") is None
    assert cp.read("what's the weather like in paris") is None


def test_the_particle_of_a_phrasal_verb_may_follow_its_object():
    cmd = cp.read("turn it down a bit")
    assert cmd is not None and cmd.verb == "turn down" and cmd.object == "it a bit"


# ── the vocabulary is the catalogue's ────────────────────────────────────────────────────────────
def test_every_frame_names_a_real_action_and_its_verb_is_the_catalogue_s():
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    from eli.execution.route_contracts import _same_word
    from eli.tools.registry.capabilities_doc import _C
    for frame in cp.FRAMES:
        assert frame.action in SUPPORTED_ACTIONS, frame
        first_words = {p.strip("“”\"' ").lower().split()[0] for p in _C[frame.action][2]
                       if p.strip("“”\"' ") and not p.strip("“”\"' ").startswith("(")}
        catalogue_verb = cp.SAID_FOR[frame.verb][0].split()[0]
        assert any(_same_word(catalogue_verb, w) for w in first_words) or frame.verb in ("wake", "turn up", "turn down"), \
            (frame.name, catalogue_verb, first_words)


def test_a_literal_names_its_action_in_the_action_s_own_words():
    lits = cp.literals()
    assert lits["stop the pomodoro"] == "POMODORO_STOP"
    assert lits["show my habits"] == "HABIT_STATUS"
    assert lits["give me my briefing"] == "MORNING_REPORT"
    # an example inside a phrase is not the action
    assert "more on the transformer story" not in lits
    assert "go deeper into hubble" not in lits
    # explicit-only and internal actions are never literals
    assert "clear chat history" not in lits and "patch yourself" not in lits
    assert all(a != "CHAT" for a in lits.values())


# ── what the rules already route is left as it was ───────────────────────────────────────────────
def _without_parser(monkeypatch):
    import eli.execution.router_enhanced as rh
    monkeypatch.setattr(rh, "INTENT_STAGES_EARLY", tuple(s for s in rh.INTENT_STAGES_EARLY if s[0] != "command_parser"))
    monkeypatch.setattr(rh, "INTENT_STAGES_TAIL", tuple(s for s in rh.INTENT_STAGES_TAIL if s[0] != "command_parser_tail"))


@lru_cache(maxsize=None)
def _routed(phrase: str) -> dict:
    """The route with the parser in place, once per phrase for the whole module."""
    return route(phrase)


def _catalogue_phrases():
    from eli.tools.registry.capabilities_doc import _C
    for action, (_cat, _desc, phrases) in _C.items():
        for p in phrases:
            p = p.strip("“”\"' ")
            if p and not p.startswith("(") and "“" not in p and "”" not in p:
                yield action, p


# The three the parser reads otherwise, each for the better: the catalogue's phrase for OPEN_IDE
# is OPEN_IDE, not an IDE opened on an empty path; "status" is the catalogue's phrase for
# GET_STATUS, not a fragment to drop; "start a timer for twenty minutes" is a timer, not an app.
_READ_BETTER = {
    "open the IDE": "OPEN_IDE",
    "status": "GET_STATUS",
}


def test_the_rules_route_the_catalogue_s_phrases_as_before(monkeypatch):
    with_parser = {p: _routed(p) for _a, p in _catalogue_phrases()}
    _without_parser(monkeypatch)
    for _a, p in _catalogue_phrases():
        before = route(p)
        after = with_parser[p]
        if before.get("action") in ("CHAT", None):
            continue                      # the parser may only add where the rules had nothing
        if p in _READ_BETTER:
            assert after["action"] == _READ_BETTER[p]
            continue
        assert (after["action"], after.get("args")) == (before["action"], before.get("args")), p


def test_the_parser_only_adds_where_the_rules_had_nothing():
    gained = [p for a, p in _catalogue_phrases()
              if str((_routed(p).get("meta") or {}).get("matched_by") or "").startswith("parser.")]
    # the catalogue phrases the rules did not route and the parser now does
    for p in ("stop the pomodoro", "show my habits", "give me my briefing", "go back a song", "audit your identity",
              "refresh my user info", "explain your cognition runtime"):
        assert p in gained, p
    assert all(_routed(p)["action"] == a for a, p in _catalogue_phrases() if p in gained), \
        [(a, p, _routed(p)["action"]) for a, p in _catalogue_phrases() if p in gained and _routed(p)["action"] != a]


# ── what the parser takes that the rules missed ──────────────────────────────────────────────────
@pytest.mark.parametrize("said, action, args", [
    ("can you fire up spotify", "OPEN_APP", {"name": "spotify"}),
    ("pull up github.com", "OPEN_URL", {"url": "https://github.com"}),
    ("i want you to open the file manager", "OPEN_FILE_SYSTEM", {"path": "~"}),
    ("go ahead and launch the terminal", "OPEN_APP", {"name": "terminal"}),
    ("shut firefox", "CLOSE_APP", {"name": "firefox"}),
    ("put on some jazz on spotify", "PLAY_MEDIA", {"query": "jazz", "target": "spotify"}),
    ("skip this track", "NEXT_MEDIA", {}),
    ("go back a track", "PREVIOUS_MEDIA", {}),
    ("jot down that the meeting moved to friday", "WRITE_NOTE", {"text": "the meeting moved to friday"}),
    ("set a timer for five minutes", "SET_TIMER", {"duration": 300}),
    ("start a timer for twenty minutes", "SET_TIMER", {"duration": 1200}),
    ("set a timer for 1 hour 30 minutes", "SET_TIMER", {"duration": 5400}),
    ("wake me at 7am", "SET_ALARM", {"time": "07:00"}),
    ("set an alarm for half past six", "SET_ALARM", {"time": "06:30"}),
    ("could you turn it down a bit", "VOLUME", {"direction": "down"}),
    ("grab a screenshot of the screen", "SCREENSHOT", {"region": "full"}),
])
def test_a_command_said_another_way_is_the_same_command(said, action, args):
    got = route(said)
    assert got["action"] == action, got
    for key, value in args.items():
        assert got["args"].get(key) == value, got
    assert str((got.get("meta") or {}).get("matched_by") or "").startswith("parser.")


@pytest.mark.parametrize("said", [
    "take a screenshot of the left half",      # a region the rules read
    "open github.com in firefox",              # a browser the rules read
    "go to sleep",                             # "go to" opens an address, not a thing
    "shut up",
    "back up my files",
    "raise the roof",
    "start with the outline",
    "search through your records for when i mentioned my brother",   # ELI's own memory, not the web
])
def test_what_is_not_a_command_of_the_language_is_left_to_the_rules(said):
    assert not str((route(said).get("meta") or {}).get("matched_by") or "").startswith("parser.")


# ── a pronoun stands for what the last command was about ────────────────────────────────────────
def test_a_pronoun_is_what_the_last_command_was_about():
    from eli.kernel.request_context import turn_facts_var
    token = turn_facts_var.set({"request_id": "t", "last_command": {
        "action": "OPEN_APP", "args": {"name": "firefox", "target": "firefox"}, "ts": time.time()}})
    try:
        closed = route("close it")
        assert (closed["action"], closed["args"].get("name")) == ("CLOSE_APP", "firefox")
        again = route("open it again")
        assert (again["action"], again["args"].get("name")) == ("OPEN_APP", "firefox")
    finally:
        turn_facts_var.reset(token)


def test_without_a_last_command_a_pronoun_is_left_to_the_rules():
    from eli.kernel.request_context import turn_facts_var
    token = turn_facts_var.set({"request_id": "t"})
    try:
        assert not str((route("close it").get("meta") or {}).get("matched_by") or "").startswith("parser.")
    finally:
        turn_facts_var.reset(token)


def test_a_last_command_too_old_to_mean_it_is_not_used():
    from eli.kernel.request_context import turn_facts_var
    token = turn_facts_var.set({"request_id": "t", "last_command": {
        "action": "OPEN_APP", "args": {"name": "firefox"}, "ts": time.time() - 7 * 86400}})
    try:
        assert cp.last_object() == ""
    finally:
        turn_facts_var.reset(token)


# ── what "it" and "that folder" point back at ───────────────────────────────────────────────────
def test_it_and_that_folder_are_the_place_the_last_command_opened(tmp_path):
    """"list it" after a folder was opened listed <working directory>/it, and "summarise what that folder
    contains" dumped old messages from memory."""
    from eli.kernel.request_context import turn_facts_var
    token = turn_facts_var.set({"request_id": "t", "last_command": {
        "action": "OPEN_FILE_SYSTEM", "args": {"path": str(tmp_path)}, "ts": time.time()}})
    try:
        listed = route("list it")
        assert (listed["action"], listed["args"].get("path")) == ("LIST_DIR", str(tmp_path))
        summed = route("can you summarise what that folder contains")
        assert (summed["action"], summed["args"].get("path")) == ("SUMMARIZE_FILE", str(tmp_path))
    finally:
        turn_facts_var.reset(token)


def test_with_nothing_opened_it_is_not_a_path():
    from eli.kernel.request_context import turn_facts_var
    token = turn_facts_var.set({"request_id": "t"})
    try:
        assert (route("list it").get("args") or {}).get("path") != "it"
        assert route("can you summarise what that folder contains")["action"] != "MEMORY_RECALL"
    finally:
        turn_facts_var.reset(token)


# ── the volume, said by its noun ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("said,args", [
    ("vol down 10%", {"direction": "down", "delta": 10}),     # the number was dropped: a fixed 15
    ("vol 60%", {"level": 60, "mode": "absolute"}),           # every absolute rule needed "volume"
    ("vol up 8%", {"direction": "up", "delta": 8}),
    ("volume 60%", {"level": 60, "mode": "absolute"}),
    ("set the volume to 40 percent", {"level": 40, "mode": "absolute"}),
    ("volume to fifty", {"level": 50, "mode": "absolute"}),
    ("volume max", {"level": 100, "mode": "absolute"}),
    ("turn up the volume by 20", {"direction": "up", "delta": 20}),
    ("louder", {"direction": "up"}),
])
def test_the_volume_is_read_by_one_grammar(said, args):
    got = route(said)
    assert got["action"] == "VOLUME", got
    assert {k: got["args"].get(k) for k in args} == args, got


def test_a_step_not_said_is_the_effectors_own():
    """The router set 15 and the effector 10: with no number said the route names none."""
    assert "delta" not in route("volume down")["args"] and "delta" not in route("vol up")["args"]
    assert route("lower the price of milk")["action"] != "VOLUME"


@pytest.mark.parametrize("asked", ["current volume?", "what's the vol at", "how loud is it", "what is the volume",
                                   "volume level"])
def test_asking_what_the_volume_is_reads_it(asked):
    """"current volume?" went to chat, which guessed; the effector reads the level back."""
    got = route(asked)
    assert (got["action"], got["args"].get("direction")) == ("VOLUME", "get"), got


def test_this_folder_and_these_files_are_read_from_what_was_just_listed(tmp_path):
    from eli.kernel.request_context import turn_facts_var
    (tmp_path / "notes.txt").write_text("x")
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "a.pdf").write_bytes(b"%PDF")
    for folder, action, key in ((tmp_path, "SUMMARIZE_FILE", "path"), (papers, "ANALYZE_PDF_FOLDER", "folder")):
        token = turn_facts_var.set({"request_id": "t", "last_command": {
            "action": "LIST_DIR", "args": {"path": str(folder)}, "ts": time.time()}})
        try:
            for said in ("read this folder", "analyse these files"):
                got = route(said)
                assert (got["action"], got["args"].get(key)) == (action, str(folder)), (said, got)
        finally:
            turn_facts_var.reset(token)


def test_it_is_the_place_the_executor_found_and_a_recall_does_not_replace_it(tmp_path, monkeypatch):
    """The last command kept the words it was asked with ("my project folder"), not the folder the
    executor found, so "list it" next had nothing to list; a memory recall in between replaced it."""
    import eli.kernel.request_context as rc
    from eli.execution.executor_enhanced import execute
    from eli.execution.routing.command_parser import place_referred_to
    from eli.kernel.engine import CognitiveEngine
    import pathlib
    import eli.execution.effectors.file_ops as file_ops

    class _Elsewhere(type(pathlib.Path())):      # this machine keeps its checkouts under ~/eli, which
        @classmethod                              # LIST_DIR reads as ELI's own legacy folder
        def home(cls):
            return tmp_path / "home"
    monkeypatch.setattr(file_ops, "Path", _Elsewhere)
    eng = CognitiveEngine()
    found = tmp_path / "projects" / "garden-planner"
    found.mkdir(parents=True)
    said = []

    def _first_turn(user_input, **k):
        eng._last_command_action = {"action": "LIST_DIR", "args": {"path": "my project folder"}, "ts": time.time(),
                                    "request_id": rc.turn_facts_var.get()["request_id"]}
        return execute("LIST_DIR", {"path": str(found)})     # what the executor resolved it to

    def _next_turn(user_input, **k):
        said.append(place_referred_to("it"))
        return {"ok": True, "content": "ok"}

    monkeypatch.setattr(eng, "_process_impl", _first_turn)
    eng.process("list my project folder")
    assert eng._last_command_action["acted_on"] == {"path": str(found)}
    monkeypatch.setattr(eng, "_process_impl", _next_turn)
    eng.process("list it")
    assert said == [str(found)]
    assert not CognitiveEngine._kept_as_last_command("MEMORY_RECALL")
    assert not CognitiveEngine._kept_as_last_command("CHAT")
    assert CognitiveEngine._kept_as_last_command("OPEN_FILE_SYSTEM")


def test_a_correction_with_an_adverb_in_its_lead_is_still_a_correction():
    """"i clearly meant ~/notes" was not read as a correction: the lead allowed nothing between "i"
    and "meant", so the whole sentence was taken as what was named."""
    from eli.runtime.command_splitter import correction, without_correction_lead
    assert correction("i clearly meant ~/notes") == "~/notes"
    assert correction("no, i obviously said the other one") == "the other one"
    assert without_correction_lead("i meant the second file") == "the second file"
    assert correction("i really like this song") == ""


@pytest.mark.parametrize("said", ["yes, not no", "not now", "no not that", "the not one"])
def test_a_word_of_grammar_is_never_read_as_a_misspelt_command(said):
    """"yes, not no" was read as a misspelt "note" and wrote a note; "not now" stored a memory."""
    from eli.execution.router_enhanced import route
    from eli.runtime.command_splitter import misspelt_commands
    assert all(fixed not in ("note", "now", "no") for _r, fixed in misspelt_commands(said))
    assert route(said)["action"] not in ("WRITE_NOTE", "MEMORY_STORE")


def test_a_command_word_with_a_letter_wrong_is_still_read():
    from eli.runtime.command_splitter import misspelt_commands
    assert ("vol down", "down") in misspelt_commands("vol don")
