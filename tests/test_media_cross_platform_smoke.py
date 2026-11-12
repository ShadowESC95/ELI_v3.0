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
