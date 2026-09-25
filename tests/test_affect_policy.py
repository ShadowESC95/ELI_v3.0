"""Affect must change what ELI DOES, not only how it sounds.

The stack already detected emotion well; it just never reached a decision. These tests
pin the behavioural half: frustration shortens and stops hedging, low mood holds
non-urgent initiative, a weak read changes nothing, and crisis states stay with the
crisis path rather than being answered with a tone tweak.
"""
from __future__ import annotations

import pytest

from eli.cognition import affect_policy as ap


def _tone(monkeypatch, detected, confidence=0.9):
    monkeypatch.setattr("eli.cognition.tone_adaptor.current_tone",
                        lambda: {"tone": detected, "detected": detected,
                                 "source": "auto", "confidence": confidence})


def _runs(monkeypatch, n):
    monkeypatch.setattr("eli.cognition.emotion_timeline.recent",
                        lambda **k: [{"detected": "angry"} for _ in range(n)])


def test_neutral_changes_nothing(monkeypatch):
    _tone(monkeypatch, "neutral")
    p = ap.current_policy()
    assert not p.active


def test_a_weak_read_changes_nothing(monkeypatch):
    """Below the confidence bar the detectors are guessing, and a guess must not
    silently alter behaviour."""
    _tone(monkeypatch, "angry", confidence=0.2)
    p = ap.current_policy()
    assert not p.active and p.reason == "no confident read"


def test_frustration_shortens_and_stops_hedging(monkeypatch):
    _tone(monkeypatch, "irritated")
    _runs(monkeypatch, 0)
    p = ap.current_policy()
    assert p.prefer_brevity and p.prefer_certainty and p.defer_proactive
    assert "irritated" in p.reason


def test_repeated_failure_makes_eli_offer_to_take_over(monkeypatch):
    _tone(monkeypatch, "angry")
    _runs(monkeypatch, 3)
    assert ap.current_policy().offer_to_take_over is True


def test_a_single_bad_turn_is_not_a_pattern(monkeypatch):
    _tone(monkeypatch, "angry")
    _runs(monkeypatch, 1)
    assert ap.current_policy().offer_to_take_over is False


def test_low_mood_holds_non_urgent_initiative(monkeypatch):
    _tone(monkeypatch, "sad")
    p = ap.current_policy()
    assert p.defer_proactive is True


def test_crisis_states_are_left_to_the_crisis_path(monkeypatch):
    """Softening behaviour is not a substitute for the crisis guard."""
    _tone(monkeypatch, "hopeless")
    p = ap.current_policy()
    assert not p.active
    assert "crisis" in p.reason


def test_positive_mood_does_not_suppress_anything(monkeypatch):
    _tone(monkeypatch, "joyful")
    assert ap.current_policy().defer_proactive is False


def test_unavailable_detectors_degrade_to_neutral(monkeypatch):
    def boom():
        raise RuntimeError("detector down")
    monkeypatch.setattr("eli.cognition.tone_adaptor.current_tone", boom)
    assert not ap.current_policy().active


def test_directive_states_the_behaviour_not_the_mood(monkeypatch):
    _tone(monkeypatch, "irritated")
    _runs(monkeypatch, 0)
    d = ap.directive()
    assert d and "briefly" in d.lower()
    # it instructs behaviour; it must not tell the model to *sound* a way
    assert "irritated" not in d.lower()


def test_directive_is_empty_when_inactive(monkeypatch):
    _tone(monkeypatch, "neutral")
    assert ap.directive() == ""


# --------------------------------------------------------------------------
# The wiring: affect reaching a real decision point
# --------------------------------------------------------------------------

def test_proactive_suggestions_are_held_when_the_user_is_frustrated(monkeypatch):
    """The whole point — emotion reaching a decision, not just the wording."""
    monkeypatch.setattr(ap, "current_policy",
                        lambda user_id="": ap.AffectPolicy(defer_proactive=True,
                                                           reason="frustrated"))
    defer, why = ap.should_defer_proactive()
    assert defer is True and why == "frustrated"


def test_proactive_daemon_consults_the_affect_gate():
    """_should_emit is the single choke point for unprompted suggestions."""
    import inspect
    from eli.planning import proactive_daemon
    src = inspect.getsource(proactive_daemon.ProactiveDaemon._should_emit)
    assert "should_defer_proactive" in src


def test_emotional_checkin_is_not_gated_by_the_same_path():
    """A low mood is the reason to ask how someone is — it must not suppress that."""
    import inspect
    from eli.planning import proactive_daemon
    src = inspect.getsource(proactive_daemon)
    idx = src.index("emotion_checkin")
    window = src[max(0, idx - 2000):idx]
    assert "_should_emit(\"emotion_checkin\"" not in window


def test_prompt_assembly_carries_the_behavioural_directive():
    import inspect
    from eli.kernel import engine as prompt_assembly
    src = inspect.getsource(prompt_assembly)
    assert "build_situation_brief" in src
    from eli.cognition import tone_adaptor
    assert "affect_policy" in inspect.getsource(tone_adaptor)
