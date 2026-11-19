"""Cross-OS desktop capability + UI grounding hooks."""
from __future__ import annotations

from eli.perception import desktop_capabilities as dc
from eli.perception import ui_ground as ug


def test_runtime_tools_report_has_platform():
    rep = dc.runtime_tools_report()
    assert rep["platform"] in {"linux", "windows", "macos", "android"}
    assert "input" in rep
    assert "locate" in rep


def test_display_server_on_linux():
    ds = dc.display_server()
    assert ds in {"wayland", "x11", "linux-headless", "windows", "macos", "android", "other"}


def test_precision_backend_disabled_by_default(monkeypatch):
    monkeypatch.setattr(ug, "_setting", lambda k, d="": d)
    assert ug.configured_precision_backend() == ""


def test_locate_without_backend_returns_error():
    hit = ug.locate_with_precision_backend("blue icon", "/tmp/nope.png", backend="none")
    assert hit.get("ok") is False


def test_the_session_type_is_decided_one_way(monkeypatch):
    """os_controller read XDG_SESSION_TYPE alone and defaulted to x11, this module trusted
    WAYLAND_DISPLAY first and the clipboard a third rule: a Wayland session started without
    XDG_SESSION_TYPE was driven with the X11 tools. All three ask portable_app_control now."""
    import subprocess
    import sys
    from eli.integrations.mpris import playerctl_backend as pb
    from eli.system import portable_app_control as pac

    monkeypatch.setattr(dc.pc, "WINDOWS", False)
    monkeypatch.setattr(dc.pc, "MACOS", False)
    monkeypatch.setattr(dc.pc, "ANDROID", False)
    monkeypatch.setattr(dc.pc, "LINUX", True)
    monkeypatch.setattr(pac, "_system", lambda: "linux")
    monkeypatch.setattr(pb, "_has", lambda tool: tool in ("wl-copy", "xclip"))
    for env, expected, clip in (
            ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland", "wayland"),
            ({"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "x11", "xclip"),
            ({"DISPLAY": ":0"}, "x11", "xclip"),
            ({}, "linux-headless", "xclip")):
        for name in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert dc.display_server() == expected, env
        assert pb._clipboard_backend() == clip, env

    # os_controller decides once, at import: a fresh process with only WAYLAND_DISPLAY set
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("XDG_SESSION_TYPE", "DISPLAY")}
    env["WAYLAND_DISPLAY"] = "wayland-0"
    code = ("import sys; from eli.perception import os_controller as oc; "
            "sys.exit(0 if (not oc.LINUX or oc.IS_WAYLAND) else 3)")
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert done.returncode == 0, done.stderr[-400:]
