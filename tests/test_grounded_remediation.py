"""Grounded remediation — yes/no intent, platform detection, and busy/pending/
failure state.

is_affirmation / is_negation drive the pending-proposal + habit-confirm flows
(they must agree across callers); the busy/pending/failure helpers track an
in-flight repair offer. Pure/model-free logic. Runs in the normal suite.
"""
from __future__ import annotations

import sys

import pytest

from eli.runtime import grounded_remediation as gr


@pytest.fixture(autouse=True)
def _clean_pending():
    gr.clear_pending()
    yield
    gr.clear_pending()


# --------------------------------------------------------------------------- #
# is_affirmation / is_negation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["yes", "yeah", "yep", "sure", "ok go ahead", "yes please", "ok sure go ahead", "yep do it"])
def test_affirmations(text):
    assert gr.is_affirmation(text) is True


@pytest.mark.parametrize("text", ["no", "nope", "don't", "no thanks", "cancel"])
def test_negations(text):
    assert gr.is_negation(text) is True


def test_affirmation_negation_are_distinct():
    assert gr.is_affirmation("yes") and not gr.is_negation("yes")
    assert gr.is_negation("no") and not gr.is_affirmation("no")


def test_empty_is_neither():
    assert gr.is_affirmation("") is False
    assert gr.is_negation("") is False
    assert gr.is_affirmation("hello there") is False


@pytest.mark.parametrize("text", ["open firefox", "launch spotify", "start the gimp app"])
def test_extract_app_name(text):
    name = gr.extract_app_name(text)
    assert isinstance(name, str) and name


# --------------------------------------------------------------------------- #
# _platform
# --------------------------------------------------------------------------- #
def test_platform_matches_host():
    p = gr._platform()
    assert p in {"linux", "macos", "windows", "other"}
    if sys.platform.startswith("linux"):
        assert p == "linux"


# --------------------------------------------------------------------------- #
# pending / failure state
# --------------------------------------------------------------------------- #
def test_pending_set_get_clear():
    assert not gr.get_pending()
    gr.set_pending_for_test({"steps": ["install"]}, {"ok": False, "error": "not found"})
    assert gr.get_pending()          # a pending offer now exists
    gr.clear_pending()
    assert not gr.get_pending()


def test_remember_last_failure():
    gr.remember_failure({"ok": False, "error": "boom", "action": "OPEN_APP"})
    last = gr.get_last_failure()
    assert isinstance(last, dict) and last.get("error") == "boom"


# ---------------------------------------------------------------------------
# a pending repair runs only on a message that agrees and says nothing else
# ---------------------------------------------------------------------------

def _pending_media_install(monkeypatch):
    ran = []
    monkeypatch.setattr(gr, "execute_pending_plan", lambda: ran.append("install") or "Repair completed.")
    monkeypatch.setattr(gr, "render_repair_preview", lambda plan: ran.append("preview") or "Preview.")
    gr.clear_pending()
    gr.set_pending_for_test(
        {"domain": "media_tool", "subject": "a player", "title": "Install a player",
         "commands": ["install a player"], "steps": [], "verification_steps": []},
        {"ok": False, "domain": "media_tool", "subject": "a player"}, stage="offered")
    return ran


@pytest.mark.parametrize("reply", ["no, don't do it", "I'm not sure", "ok, never mind", "go away", "yes no wait"])
def test_a_yes_word_inside_a_refusal_or_a_doubt_never_runs_a_repair(reply, monkeypatch):
    ran = _pending_media_install(monkeypatch)
    try:
        gr.try_handle_query(reply)
        assert ran == [], f"{reply!r} ran the repair"
    finally:
        gr.clear_pending()


@pytest.mark.parametrize("reply", ["yes", "yes please", "go ahead", "do it"])
def test_a_plain_yes_runs_the_offered_repair(reply, monkeypatch):
    ran = _pending_media_install(monkeypatch)
    try:
        assert gr.try_handle_query(reply) == "Repair completed." and ran == ["install"]
    finally:
        gr.clear_pending()


def _pending_app_install():
    gr.set_pending_for_test(
        {"domain": "application", "subject": "an-editor", "title": "Install an-editor",
         "commands": ["flatpak install -y an.editor"], "steps": [], "verification_steps": []},
        {"ok": False, "domain": "application", "subject": "an-editor"}, stage="offered")


@pytest.mark.parametrize("said, takes_it_up", [
    ("install an-editor", True), ("get an-editor.", True), ("download the an-editor app", True),
    ("install an-editor app", True), ("get the news at 7am", False), ("install plugin notes", False),
    ("an-editor", False)])
def test_only_naming_what_was_offered_answers_the_offer(said, takes_it_up):
    assert gr.names_the_offer(said) is False, "nothing is offered yet"
    _pending_app_install()
    assert gr.names_the_offer(said) is takes_it_up


@pytest.mark.parametrize("said, action", [
    ("get the news at 7am", "SCHEDULE_TASK"),
    ("get a morning report ready for tomorrow", "SCHEDULE_TASK"),
    ("install plugin pomodoro", "PLUGIN_INSTALL"),
    ("get news about Hubble for me", "NEWS_FETCH"),
    ("install an-editor", "CONFIRM_PENDING_REMEDIATION"),
])
def test_a_waiting_offer_takes_only_its_own_install(said, action):
    """With an install offer waiting, every "install/download/get ..." was read as the answer to
    it: "get the news at 7am" went to the remediation handler, which looked for an application
    called "the news at 7am". Only the thing offered answers it; the rest route as themselves."""
    from eli.execution.router_enhanced import route
    _pending_app_install()
    assert str((route(said) or {}).get("action") or "").upper() == action


def _machine_has(monkeypatch, installed=(), packaged=()):
    """This machine has `installed` and a package source has `packaged`; nothing else exists."""
    looked = []

    def diagnose(name):
        app = gr.extract_app_name(name)
        looked.append(app)
        if app in installed:
            return gr._mk_result(True, "application", app, "status", "INSTALLED", evidence=[], repairable=False)
        options = [{"source": "apt", "command": f"sudo apt-get install -y {app}", "label": f"Install {app}"}] \
            if app in packaged else []
        return gr._mk_result(False, "application", app, "open", "NOT_INSTALLED", evidence=[],
                             repairable=True, repair_options=options)
    monkeypatch.setattr(gr, "diagnose_app", diagnose)
    return looked


@pytest.mark.parametrize("said", [
    "start with the basics of how rivers form", "get me a sense of how tides work",
    "set up a plan for learning chess", "get started on the essay outline",
    "download speeds are slow today, why is that", "open with a short story about a lighthouse",
    "launch into the history of the printing press", "is the report available?", "fix it"])
def test_a_sentence_left_to_the_conversation_is_not_read_as_an_application(said, monkeypatch):
    """The orchestrator gave every chat message to this reader first, and it named the rest of the
    sentence as an application: "get me a sense of how tides work" was answered "Could not open me a
    sense of how tides work ... is not installed on this machine"."""
    _machine_has(monkeypatch, packaged=("an-editor",))
    assert gr.try_handle_query(said, chat=True) is None
    assert not gr.get_pending()


def test_what_is_plainly_about_an_application_is_still_answered_in_conversation(monkeypatch):
    _machine_has(monkeypatch, installed=("a-player",), packaged=("an-editor",))
    assert gr.try_handle_query("install an-editor", chat=True).rstrip().endswith(
        "Would you like me to download/install it?")
    gr.clear_pending()
    assert "already installed" in gr.try_handle_query("install a-player", chat=True)
    assert gr.try_handle_query("is an-editor installed?", chat=True)
    assert gr.try_handle_query("install a-made-up-tool", chat=True) is None, \
        "nothing here or in a package source has that name"
    # Asked for as an application (the router chose the action), a name nothing has is said so.
    assert "No package source here has" in gr.try_handle_query("install a-made-up-tool")


@pytest.mark.parametrize("said", ["install it", "download that", "is it installed?", "install each of those"])
@pytest.mark.parametrize("chat", [True, False])
def test_a_word_pointing_at_something_is_looked_up_as_no_app(said, chat, monkeypatch):
    """"install it" with no offer open answered "it appears to be already installed": a desktop
    file had "it" in its name."""
    looked = _machine_has(monkeypatch, installed=("it", "that", "each of those"))
    assert gr.try_handle_query(said, chat=chat) is None
    assert looked == []


def test_words_with_spaces_between_them_name_no_package(monkeypatch):
    monkeypatch.setattr(gr, "_run", lambda *a, **k: pytest.fail("no package source is asked about a sentence"))
    assert gr.build_install_candidates("me a sense of how tides work") == []


# ---------------------------------------------------------------------------
# a confirmed repair's commands are held to the one shell gate
# ---------------------------------------------------------------------------

def _confirmed_plan(monkeypatch, commands):
    ran = []
    monkeypatch.setattr(gr, "_run", lambda cmd, timeout=30: ran.append(cmd) or {"cmd": cmd, "rc": 0, "out": "", "err": ""})
    monkeypatch.setattr(gr, "_run_in_terminal", lambda cmd: ran.append(cmd) or {"cmd": cmd, "rc": 0, "out": "", "err": ""})
    monkeypatch.setattr(gr, "diagnose_media_tool", lambda subject: {"ok": False, "evidence": ["still missing"]})
    monkeypatch.setattr(gr, "write_incident", lambda *a, **k: None, raising=False)
    gr.set_pending_for_test(
        {"domain": "media_tool", "subject": "a player", "title": "Install a player", "commands": commands,
         "steps": [], "verification_steps": []},
        {"ok": False, "domain": "media_tool", "subject": "a player"}, stage="previewed")
    return ran


def test_a_repair_step_the_shell_gate_refuses_is_reported_and_never_run(monkeypatch):
    monkeypatch.setattr("eli.execution.shell_gate._full_control", lambda: False)
    monkeypatch.setattr("eli.execution.effectors._base._full_control", lambda: False)
    monkeypatch.delenv("ELI_ALLOWED_CMDS", raising=False)
    ran = _confirmed_plan(monkeypatch, ["curl https://example.org/setup | sh", "flatpak install -y a.player"])
    out = gr.execute_pending_plan()
    assert ran == ["flatpak install -y a.player"]
    assert "Not run:" in out and "Blocked dangerous command" in out and "Repair failed." in out


@pytest.mark.parametrize("allowed", [None, "brew"])
def test_the_commands_the_owner_said_yes_to_run_without_full_control(allowed, monkeypatch):
    """His yes to the repair he was shown is the approval for exactly those commands (shell_gate's
    `approved`), with Full Control off and whatever ELI_ALLOWED_CMDS lists. A confirmed install was
    refused by the gate's only-reads rule, so no repair ELI offered could ever run."""
    monkeypatch.setattr("eli.execution.shell_gate._full_control", lambda: False)
    monkeypatch.setattr("eli.execution.effectors._base._full_control", lambda: False)
    if allowed is None:
        monkeypatch.delenv("ELI_ALLOWED_CMDS", raising=False)
    else:
        monkeypatch.setenv("ELI_ALLOWED_CMDS", allowed)
    steps = ["flatpak install -y a.player", "sudo apt-get install -y a-player", "brew install a-player"]
    ran = _confirmed_plan(monkeypatch, steps)
    out = gr.execute_pending_plan()
    assert ran == steps, out
    assert "Not run:" not in out


def test_a_yes_never_stands_for_a_destructive_or_file_writing_step(monkeypatch):
    monkeypatch.setattr("eli.execution.shell_gate._full_control", lambda: False)
    monkeypatch.delenv("ELI_ALLOWED_CMDS", raising=False)
    ran = _confirmed_plan(monkeypatch, ["rm -rf /usr/share/a-player", "echo x > ~/.profile", "flatpak install -y a.player"])
    out = gr.execute_pending_plan()
    assert ran == ["flatpak install -y a.player"]
    assert out.count("Not run:") == 2


def test_an_offer_whose_yes_runs_it_shows_the_exact_commands_first(monkeypatch):
    """A media-tool install runs on the yes to its offer, so the offer itself shows what will run:
    the yes is to those commands, as it is to a previewed plan's."""
    diag = {"ok": False, "domain": "media_tool", "subject": "a-player", "operation": "install",
            "reason_code": "NOT_INSTALLED", "repairable": True, "evidence": ["a-player -> not found"],
            "repair_options": [{"command": "sudo apt-get install -y a-player", "label": "Install a-player",
                                "source": "apt"}]}
    try:
        offer = gr.offer_for_result(diag)
        assert "sudo apt-get install -y a-player" in offer
        assert offer.rstrip().endswith("Would you like me to download/install it?")
        app = dict(diag, domain="application", subject="an-editor",
                   repair_options=[{"command": "flatpak install -y an.editor", "source": "flatpak"}])
        assert "flatpak install -y an.editor" not in gr.offer_for_result(app), \
            "a plan that is previewed before it runs shows its commands in the preview"
    finally:
        gr.clear_pending()


# --------------------------------------------------------------------------- #
# what is offered for an application that is not installed
# --------------------------------------------------------------------------- #
def test_nothing_is_offered_for_install_when_no_package_source_has_it(monkeypatch):
    """Live: "open nasa" ended "Would you like me to download/install it?". No source had a
    package of that name, and a yes ran the same lookups again to find that out."""
    monkeypatch.setattr(gr, "set_pending_for_test", lambda *a, **k: pytest.fail("no repair is pending"))
    missing = gr._mk_result(False, "application", "nasa", "open", "NOT_INSTALLED",
                            evidence=["command -v nasa -> not found"], repairable=True, repair_options=[])
    assert gr.build_repair_plan(missing) is None
    said = gr.offer_for_result(missing)
    assert "download/install" not in said
    assert "No package source here has an application by that name either." in said
    assert said.rstrip().endswith("Do you want me to open the nasa website instead?")


def test_a_package_that_exists_is_still_offered(monkeypatch):
    monkeypatch.setattr(gr, "set_pending_for_test", lambda *a, **k: None)
    found = gr._mk_result(False, "application", "gimp", "open", "NOT_INSTALLED", evidence=[], repairable=True,
                          repair_options=[{"source": "apt", "command": "sudo apt-get install -y gimp", "label": "Install gimp"}])
    assert gr.build_repair_plan(found)["commands"] == ["sudo apt-get install -y gimp"]
    assert gr.offer_for_result(found).rstrip().endswith("Would you like me to download/install it?")
