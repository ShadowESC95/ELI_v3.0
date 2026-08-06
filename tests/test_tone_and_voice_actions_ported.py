"""SET_TONE / CLEAR_TONE / CREATE_VOICE / DESIGN_VOICE reach the user in v3.

These four existed in v2 and were absent from v3 — not the machinery, which had
ported fine (`tone_adaptor`, `emotion_palette`, `voice_fx` were all here, and the
Settings dropdown already called `set_tone()`), but the *conversational surface*:
no router intent, no executor branch, no manifest entry. So a user could change
ELI's register from a dropdown and not by saying "be comedic".

Half-ports are silent — nothing errors, the feature is just unreachable — so
these pin the reachable path, not the module.
"""
from __future__ import annotations

import pytest

from eli.execution.router_enhanced import route_intent
from eli.execution.executor_enhanced import execute, SUPPORTED_ACTIONS

ACTIONS = ["SET_TONE", "CLEAR_TONE", "CREATE_VOICE", "DESIGN_VOICE"]


@pytest.mark.parametrize("action", ACTIONS)
def test_action_is_registered(action):
    assert action in SUPPORTED_ACTIONS, f"{action} missing from SUPPORTED_ACTIONS"


@pytest.mark.parametrize("phrase,expected", [
    ("be comedic", "SET_TONE"),
    ("talk street", "SET_TONE"),
    ("sound more professional", "SET_TONE"),
    ("go back to normal", "CLEAR_TONE"),
    ("be yourself", "CLEAR_TONE"),
    ("drop the tone", "CLEAR_TONE"),
    ("design a voice called Nova, deep and slow", "DESIGN_VOICE"),
])
def test_phrase_routes_to_action(phrase, expected):
    r = route_intent(phrase) or {}
    assert r.get("action") == expected, \
        f"{phrase!r} routed to {r.get('action')!r}, expected {expected}"


def test_set_tone_then_clear_round_trips():
    r = execute("SET_TONE", {"tone": "comedic"})
    assert r.get("ok") is True, r
    assert r.get("tone") == "comedic"

    from eli.cognition import tone_adaptor
    assert tone_adaptor.current_tone().get("tone") == "comedic"

    r = execute("CLEAR_TONE", {})
    assert r.get("ok") is True, r
    assert tone_adaptor.current_tone().get("tone") != "comedic"


def test_unknown_tone_fails_helpfully_not_silently():
    r = execute("SET_TONE", {"tone": "definitely-not-a-tone"})
    assert r.get("ok") is False
    # must name real alternatives rather than just refusing
    assert "comedic" in str(r.get("content", "")).lower()


def test_design_voice_without_a_name_explains_itself():
    r = execute("DESIGN_VOICE", {})
    assert r.get("ok") is False
    assert "name" in str(r.get("content", "")).lower()


def test_passing_mention_does_not_hijack_the_turn():
    """'be comedic' sets a tone; talking *about* comedy must not."""
    r = route_intent("that joke was comedic gold") or {}
    assert r.get("action") != "SET_TONE"
