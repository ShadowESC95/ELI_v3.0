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
@pytest.mark.parametrize("text", ["yes", "yeah", "yep", "sure", "ok go ahead", "yes please"])
def test_affirmations(text):
    assert gr.is_affirmation(text) is True


@pytest.mark.parametrize("text", ["no", "nope", "don't", "no thanks"])
def test_negations(text):
    assert gr.is_negation(text) is True


def test_affirmation_negation_are_distinct():
    assert gr.is_affirmation("yes") and not gr.is_negation("yes")
    assert gr.is_negation("no") and not gr.is_affirmation("no")


def test_empty_is_neither():
    assert gr.is_affirmation("") is False
    assert gr.is_negation("") is False
    assert gr.is_affirmation("hello there") is False


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
