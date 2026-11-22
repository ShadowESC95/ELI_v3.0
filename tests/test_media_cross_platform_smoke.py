"""Cross-platform media smoke tests — run on Linux, macOS, and Windows CI."""
from __future__ import annotations

import sys

import pytest


def test_platform_capability_report_runs():
    from eli.integrations.media.capabilities import platform_capability_report
    report = platform_capability_report(verbose=True)
    assert "Platform:" in report


def test_open_url_does_not_crash(monkeypatch):
    from eli.utils import platform_compat as pc
    monkeypatch.setattr(pc, "open_url", lambda u: True)
    from eli.execution.effectors.system_helpers import open_browser
    r = open_browser("https://example.com")
    assert r["ok"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only mpv pipe path")
def test_mpv_socket_path_windows():
    from eli.integrations.media.cross_platform import mpv_socket_path
    p = mpv_socket_path()
    assert "pipe" in p.lower() or "\\\\" in p


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS browser open smoke")
def test_macos_open_url_smoke(monkeypatch):
    import subprocess
    from eli.utils import platform_compat as pc
    calls = []
    monkeypatch.delenv("ELI_BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a) or type("P", (), {"poll": lambda s: 0})())
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(pc, "MACOS", True)
    monkeypatch.setattr(pc, "LINUX", False)
    monkeypatch.setattr(pc, "WINDOWS", False)
    assert pc._macos_open_url("https://example.com") is True
    assert calls == [["open", "https://example.com"]]  # the default browser, not Safari by name


@pytest.mark.skipif(sys.platform != "win32", reason="Windows browser open smoke")
def test_windows_open_url_smoke(monkeypatch):
    import subprocess
    from eli.utils import platform_compat as pc
    import os
    opened = []
    monkeypatch.delenv("ELI_BROWSER", raising=False)
    monkeypatch.setattr(pc.shutil, "which", lambda name: "chrome.exe" if name == "chrome" else None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: opened.append(a[0]) or type("P", (), {"poll": lambda s: 0})())
    monkeypatch.setattr(os, "startfile", lambda target, *a: opened.append(target), raising=False)
    monkeypatch.setattr(pc, "WINDOWS", True)
    monkeypatch.setattr(pc, "LINUX", False)
    monkeypatch.setattr(pc, "MACOS", False)
    assert pc._windows_open_url("https://example.com") is True
    assert opened == ["https://example.com"]  # the default browser, not Chrome by name


def _fake_mixer(monkeypatch, start):
    from eli.utils import platform_compat as pc
    level = {"v": start}
    monkeypatch.setattr(pc, "get_volume", lambda: level["v"])
    monkeypatch.setattr(pc, "set_volume", lambda v: level.update(v=v) or True)
    monkeypatch.setattr(pc, "adjust_volume", lambda d: level.update(v=level["v"] + d) or True)
    monkeypatch.setattr(pc, "set_muted", lambda m: True)
    return level


def test_volume_is_read_when_nothing_is_to_change(monkeypatch):
    """Nothing produced a read and an empty VOLUME said "no wpctl, pactl, or amixer available"; the
    level ELI set was then guessed by the model."""
    from eli.execution.effectors.system_helpers import volume_fallback
    _fake_mixer(monkeypatch, 45)
    for asked in ({"direction": "get"}, {"direction": ""}, {"direction": "set"}):
        out = volume_fallback(**asked)
        assert out["ok"] is True and out["level"] == 45 and "45%" in out["response"]


def test_a_volume_change_says_the_level_it_left(monkeypatch):
    from eli.execution.effectors.system_helpers import volume_fallback
    _fake_mixer(monkeypatch, 60)
    out = volume_fallback("down", delta=15)
    assert out["level"] == 45 and "45%" in out["response"]


def test_an_unreadable_volume_is_said(monkeypatch):
    from eli.execution.effectors.system_helpers import volume_fallback
    from eli.utils import platform_compat as pc
    monkeypatch.setattr(pc, "get_volume", lambda: None)
    out = volume_fallback("get")
    assert out["ok"] is False and out["error"] == "volume_unreadable"


# ── up is up, down is down, and the level said is the one in force ─────────────────────────────────
def test_a_level_above_full_is_read_as_it_is(monkeypatch):
    """wpctl gives a factor (1.0 is 100%); "Volume: 1.05" was taken for a percentage and read as 1%."""
    from eli.utils import platform_compat as pc
    monkeypatch.setattr(pc.shutil, "which", lambda c: "/usr/bin/wpctl" if c == "wpctl" else None)
    for said, level in (("Volume: 0.65", 65), ("Volume: 1.05", 105), ("Volume: 0.40 [MUTED]", 40)):
        monkeypatch.setattr(pc.subprocess, "check_output", lambda *a, _said=said, **k: _said)
        assert pc._linux_get_volume() == level


def test_a_step_names_the_level_it_found_and_the_level_it_left(monkeypatch):
    """Four "vol up" said 39%, 77%, 87%, 75%: the level had been changed elsewhere between them, and the
    replies, naming only the level left, read as up going down."""
    from eli.execution.effectors.system_helpers import volume_fallback
    level = _fake_mixer(monkeypatch, 29)
    assert volume_fallback("up")["response"] == "Volume up from 29% to 39%"
    level["v"] = 87                                  # changed elsewhere
    out = volume_fallback("down")
    assert out["response"] == "Volume down from 87% to 77%" and out["level"] == 77 and out["was"] == 87
    level["v"] = 95
    assert volume_fallback("up")["level"] == 100     # up stops at full
    level["v"] = 105
    out = volume_fallback("up")
    assert out["level"] == 105 and level["v"] == 105, "up never lowers a level above full"
    level["v"] = 4
    assert volume_fallback("down")["level"] == 0


def _listening_sink(monkeypatch, start=0.65, *, tool="wpctl"):
    """ELI's wake-word listener over a sink faked at the command line, and platform_compat on the same
    sink: nothing reaches the machine's own audio."""
    import eli.perception.audio_stt as stt
    from eli.utils import platform_compat as pc
    sink = {"v": start}

    def run_quiet(argv, timeout=1.5):
        if argv[0] != tool:
            return False, ""
        if argv[1] in ("get-volume", "get-sink-volume"):
            return True, (f"Volume: {sink['v']:.2f}" if tool == "wpctl"
                          else f"Volume: front-left: 1 / {round(sink['v'] * 100)}% / 0 dB")
        if argv[1] in ("set-volume", "set-sink-volume"):
            said = argv[3]
            sink["v"] = float(said[:-1]) / 100 if said.endswith("%") else float(said)
            return True, ""
        return False, ""
    monkeypatch.setattr(stt, "_eli_run_quiet", run_quiet)
    monkeypatch.setattr(stt, "_ELI_DUCK_ENABLED", True)
    monkeypatch.setattr(stt, "media_player_duck_resume", lambda: None)
    monkeypatch.setattr(pc, "get_volume", lambda: round(sink["v"] * 100))
    monkeypatch.setattr(pc, "set_volume", lambda v: sink.update(v=max(0, min(100, v)) / 100) or True)
    monkeypatch.setattr(pc, "adjust_volume", lambda d: sink.update(v=sink["v"] + d / 100) or True)
    return stt, sink


def test_a_volume_change_while_eli_listens_is_made_to_the_level_in_force(monkeypatch):
    """After the wake word ELI holds the output low and puts the level back after the command. "volume
    up" said then was made to the held level (18% to 28%), and the put-back undid it."""
    from eli.execution.effectors.system_helpers import volume_fallback
    stt, sink = _listening_sink(monkeypatch)
    held = stt._eli_duck_output()
    assert round(sink["v"] * 100) == 18
    out = volume_fallback("up")
    assert out["response"] == "Volume up from 65% to 75%"
    stt._eli_restore_duck(held)                      # the listener's own put-back, after the command
    assert round(sink["v"] * 100) == 75


def test_a_level_set_while_eli_listens_stays_set(monkeypatch):
    from eli.execution import executor_enhanced as exe
    stt, sink = _listening_sink(monkeypatch)
    held = stt._eli_duck_output()
    out = exe.execute("VOLUME", {"level": 60, "percent": 60, "mode": "absolute", "direction": "set"})
    assert out["ok"] is True and out["response"] == "Volume set to 60%"
    stt._eli_restore_duck(held)
    assert round(sink["v"] * 100) == 60


def test_the_level_is_put_back_where_only_pactl_answers(monkeypatch):
    """With no wpctl the output was ducked after the wake word and never put back."""
    stt, sink = _listening_sink(monkeypatch, 0.55, tool="pactl")
    held = stt._eli_duck_output()
    assert round(sink["v"] * 100) == 18
    stt._eli_restore_duck(held)
    assert round(sink["v"] * 100) == 55
