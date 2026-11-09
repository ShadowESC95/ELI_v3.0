"""PortAudio indices aren't stable across PyAudio instances — resolve by NAME.

The resolver probed 'pulse' at index 14 of a 19-device enumeration and handed that
integer to sr.Microphone, which saw only 10 devices → "Device index out of range" →
silent fallback to a possibly-different microphone. live_device_index() re-resolves
against the enumeration the caller will actually open with.
"""
from __future__ import annotations
import pytest
from eli.perception.mic_resolver import CaptureChoice, live_device_index


class _FakeInfo(dict):
    pass


class _FakePyAudio:
    """Enumeration stub: names in order, all input-capable unless prefixed '!'."""
    def __init__(self, names): self._names = names
    def get_device_count(self): return len(self._names)
    def get_device_info_by_index(self, i):
        n = self._names[i]
        return _FakeInfo(name=n.lstrip("!"), maxInputChannels=0 if n.startswith("!") else 2)
    def terminate(self): pass


@pytest.fixture
def fake_pa(monkeypatch):
    def _install(names):
        import types, sys
        mod = types.ModuleType("pyaudio")
        mod.PyAudio = lambda: _FakePyAudio(names)
        monkeypatch.setitem(sys.modules, "pyaudio", mod)
    return _install


def test_stale_index_is_re_resolved_by_name(fake_pa):
    # probed at 14 in a 19-device list; this process sees 10 with 'pulse' at 7
    fake_pa(["a", "b", "c", "d", "e", "f", "g", "pulse", "h", "i"])
    c = CaptureChoice(device_index=14, pulse_source=None, reason="probed", device_name="pulse")
    assert live_device_index(c) == 7   # not 14, not a crash


def test_missing_device_yields_none_not_a_wrong_device(fake_pa):
    # the named device is gone AND the stored index is out of range → OS default
    fake_pa(["mic0", "mic1"])
    c = CaptureChoice(device_index=14, pulse_source=None, reason="probed", device_name="pulse")
    assert live_device_index(c) is None


def test_stored_index_kept_only_if_still_an_input(fake_pa):
    # name gone, index in range but that slot is OUTPUT-only → must not select it
    fake_pa(["mic0", "!speaker", "mic2"])
    c = CaptureChoice(device_index=1, pulse_source=None, reason="probed", device_name="gone")
    assert live_device_index(c) is None
    # same setup but the slot IS an input → keep it
    c2 = CaptureChoice(device_index=2, pulse_source=None, reason="probed", device_name="gone")
    assert live_device_index(c2) == 2


def test_no_name_passes_index_through_unchanged(fake_pa):
    # explicit ELI_MIC_DEVICE_INDEX override / legacy cache: behaviour unchanged
    fake_pa(["mic0", "mic1", "mic2", "mic3"])
    c = CaptureChoice(device_index=3, pulse_source=None, reason="override")
    assert live_device_index(c) == 3


def test_exact_name_match_not_substring(fake_pa):
    fake_pa(["pulse_monitor", "pulse", "pulseaudio"])
    c = CaptureChoice(device_index=99, pulse_source=None, reason="probed", device_name="pulse")
    assert live_device_index(c) == 1


# ── the GUI's microphone choice (eli/gui/main_window/_mixins/conversation.py) ──

def _gui_choice(monkeypatch, data, *, save):
    import os
    import time
    import types
    import eli.perception.mic_resolver as mr
    from eli.gui.main_window._mixins.conversation import ConversationMixin
    forced = []
    monkeypatch.setattr(mr, "resolve_capture", lambda force=False: forced.append(force) or
                        CaptureChoice(None, None, "stub"))

    class _Window(ConversationMixin):
        _first_run_complete = False
        mic_device_combo = types.SimpleNamespace(currentData=lambda: data)

    _Window()._on_mic_device_changed(_save=save)
    deadline = time.monotonic() + 5
    while save and not forced and time.monotonic() < deadline:
        time.sleep(0.01)
    return os.environ, forced


def test_a_pulse_source_chosen_in_settings_pins_no_device_index(monkeypatch):
    """Choosing a PipeWire/PulseAudio source (a Bluetooth headset) set ELI_MIC_DEVICE_INDEX to 14,
    one machine's PortAudio number: elsewhere it opened another device or none."""
    for name in ("ELI_MIC_PULSE_SOURCE", "PULSE_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ELI_MIC_DEVICE_INDEX", "3")
    env, forced = _gui_choice(monkeypatch, ("pulse", "bluez_input.AA_BB_CC.0"), save=True)
    assert "ELI_MIC_DEVICE_INDEX" not in env
    assert env["ELI_MIC_PULSE_SOURCE"] == env["PULSE_SOURCE"] == "bluez_input.AA_BB_CC.0"
    assert forced == [True], "the cached earlier choice is resolved again"


def test_a_device_chosen_after_a_pulse_source_is_not_overridden_by_it(monkeypatch):
    monkeypatch.setenv("ELI_MIC_PULSE_SOURCE", "bluez_input.AA_BB_CC.0")
    monkeypatch.setenv("PULSE_SOURCE", "bluez_input.AA_BB_CC.0")
    env, forced = _gui_choice(monkeypatch, ("alsa", 2), save=False)
    assert env["ELI_MIC_DEVICE_INDEX"] == "2"
    assert "ELI_MIC_PULSE_SOURCE" not in env and "PULSE_SOURCE" not in env
    assert forced == [], "restoring the saved choice at start-up opens no microphone"
