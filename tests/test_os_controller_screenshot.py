"""Screenshots on a Linux desktop: the portal is asked and heard on one D-Bus connection, each way's
outcome is kept and reported, and the time limits come from what worked on this desktop.

On GNOME every screen read waited 90 s and said "No screenshot was taken": the request went out
through one `gdbus call` process and the answer was listened for by a separate `gdbus monitor`, which
never receives a signal addressed to another connection. The portal had taken the picture and left it
in ~/Pictures. A stand-in session bus plays the portal here; nothing reaches this machine's desktop.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("dbus_fast")
from dbus_fast import MessageType, Variant  # noqa: E402

import eli.perception.os_controller as osc  # noqa: E402

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux desktop's ways")


def _png(path: Path) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), "black").save(path)


class _Portal:
    """A session bus with the desktop portal on it, as far as a Screenshot request goes."""

    unique_name = ":1.42"

    def __init__(self, *, picture: Path | None, answer: bool = True, has_screenshot: bool = True):
        self.picture, self.answer, self.has_screenshot = picture, answer, has_screenshot
        self.handlers, self.rules, self.asked = [], [], []
        self.disconnected = False

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def disconnect(self):
        self.disconnected = True

    async def call(self, msg):
        if msg.member == "AddMatch":
            self.rules.append(msg.body[0])
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[], error_name=None)
        if msg.member == "Get":
            if not self.has_screenshot:
                return SimpleNamespace(message_type=MessageType.ERROR, error_name="org.freedesktop.DBus.Error.InvalidArgs",
                                       body=["No such interface 'org.freedesktop.portal.Screenshot'"])
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[Variant("u", 2)], error_name=None)
        assert (msg.interface, msg.member) == ("org.freedesktop.portal.Screenshot", "Screenshot")
        token = msg.body[1]["handle_token"].value
        self.asked.append(msg.body[1]["interactive"].value)
        handle = f"/org/freedesktop/portal/desktop/request/1_42/{token}"
        if self.picture is not None:
            _png(self.picture)
        if self.answer:
            signal = SimpleNamespace(message_type=MessageType.SIGNAL, interface="org.freedesktop.portal.Request",
                                     member="Response", path=handle,
                                     body=[0, {"uri": Variant("s", self.picture.as_uri())}])
            someone_else = SimpleNamespace(message_type=MessageType.SIGNAL, interface="org.freedesktop.portal.Request",
                                           member="Response", path=handle.replace("1_42", "1_7"),
                                           body=[1, {}])
            loop = asyncio.get_running_loop()
            loop.call_soon(lambda: [h(someone_else) for h in list(self.handlers)])
            loop.call_soon(lambda: [h(signal) for h in list(self.handlers)])
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[handle], error_name=None)


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    """GNOME on Wayland with no capture program installed; ELI's folders and record in tmp_path."""
    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    for name, value in (("LINUX", True), ("WINDOWS", False), ("MACOS", False), ("IS_WAYLAND", True),
                        ("IS_X11", False), ("SESSION_TYPE", "wayland"), ("DESKTOP", "ubuntu:gnome")):
        monkeypatch.setattr(osc, name, value)
    monkeypatch.setattr(osc, "_check_tool", lambda _tool: False)
    monkeypatch.setattr(osc.shutil, "which", lambda _tool: None)
    monkeypatch.setattr(osc.platform, "user_pictures_dir", lambda: pictures)
    monkeypatch.setattr(osc.platform, "user_dirs", lambda: {"pictures": str(pictures)})
    monkeypatch.setattr(osc, "_own_captures_dir", lambda: tmp_path / "screens")
    monkeypatch.setattr(osc, "_record_file", lambda: tmp_path / "runtime" / "screen_capture.json")
    return SimpleNamespace(pictures=pictures, screens=tmp_path / "screens")


def _bus(monkeypatch, portal: _Portal) -> None:
    async def _connect():
        return portal
    monkeypatch.setattr(osc, "_session_bus", _connect)


def test_the_portals_answer_is_heard_on_the_connection_that_asked(desktop, monkeypatch):
    portal = _Portal(picture=desktop.pictures / "Screenshot.png")
    _bus(monkeypatch, portal)
    started = time.monotonic()
    out = osc.take_screenshot("full", keep=False)
    assert out["ok"] is True, out.get("error")
    assert time.monotonic() - started < 5, "the answer was not heard: the capture waited"
    assert Path(out["path"]).parent == desktop.screens and Path(out["path"]).is_file()
    assert not (desktop.pictures / "Screenshot.png").exists(), "ELI's own screen read was left in Pictures"
    assert any("/request/1_42/" in rule for rule in portal.rules), "no match on this connection's request path"
    assert portal.asked == [False] and portal.disconnected
    assert out["attempts"][0]["way"] == "the desktop's screenshot portal" and out["attempts"][0]["status"] == "ok"


def test_a_portal_that_never_answers_is_a_timeout_and_not_install_advice(desktop, monkeypatch):
    _bus(monkeypatch, _Portal(picture=None, answer=False))
    monkeypatch.setattr(osc, "FIRST_TRY_CEILING_S", 0.3)
    out = osc.take_screenshot("full", keep=False)
    assert out["ok"] is False
    portal = out["attempts"][0]
    assert portal["status"] == "timeout" and "no answer within" in portal["detail"]
    assert "timed out" in out["error"]
    assert "install" not in out["error"].lower(), "advised installing what is there"
    from eli.runtime.failure_taxonomy import TIMEOUT, classify_category
    assert classify_category(out["error"], "SCREEN_READ_ANALYZE") == TIMEOUT


def test_a_picture_the_portal_saves_after_the_wait_is_collected(desktop, monkeypatch):
    _bus(monkeypatch, _Portal(picture=desktop.pictures / "Screenshot-1.png", answer=False))
    monkeypatch.setattr(osc, "FIRST_TRY_CEILING_S", 0.3)
    out = osc.take_screenshot("full", keep=False)
    assert out["ok"] is True and Path(out["path"]).parent == desktop.screens
    assert list(desktop.pictures.iterdir()) == [], "a full-screen picture was left in Pictures"


def test_a_screenshot_the_user_took_themselves_is_not_taken(desktop, monkeypatch):
    mine = desktop.pictures / "Screenshot From 2026-01-01 10-00-00.png"
    _png(mine)
    os.utime(mine, (time.time() + 5, time.time() + 5))
    _bus(monkeypatch, _Portal(picture=None, answer=False))
    monkeypatch.setattr(osc, "FIRST_TRY_CEILING_S", 0.3)
    assert osc.take_screenshot("full", keep=False)["ok"] is False
    assert mine.exists()


def test_install_advice_names_the_portal_backend_only_when_it_is_missing(desktop, monkeypatch):
    _bus(monkeypatch, _Portal(picture=None, has_screenshot=False))
    out = osc.take_screenshot("full", keep=False)
    assert out["ok"] is False
    assert out["attempts"][0]["status"] == "unavailable"
    assert "xdg-desktop-portal-gnome" in out["error"]


def test_the_way_that_worked_here_goes_first_with_a_limit_from_its_own_times(desktop, monkeypatch):
    calls = []

    def way(name, status, seconds):
        def run(limit):
            calls.append((name, round(limit, 3)))
            return osc._outcome(name, status, seconds)
        return (name, None, run)

    monkeypatch.setattr(osc, "_capture_ways", lambda _path, _area: [way("broken", "failed", 0.01),
                                                                     way("good", "ok", 0.2)])
    assert osc.take_screenshot("full", keep=False)["way"] == "good"
    assert calls == [("broken", osc.FIRST_TRY_CEILING_S), ("good", osc.FIRST_TRY_CEILING_S)]
    calls.clear()
    assert osc.take_screenshot("full", keep=False)["way"] == "good"
    assert calls == [("good", round(0.2 * osc.STALL_FACTOR, 3))], "the way that works here was not tried first"


def test_a_way_that_ran_out_of_time_gets_the_full_wait_next_time(desktop, monkeypatch):
    calls = []

    def run(limit):
        calls.append(limit)
        return osc._outcome("portal-like", "timeout" if len(calls) == 2 else "ok", 0.1)

    monkeypatch.setattr(osc, "_capture_ways", lambda _path, _area: [("portal-like", None, run)])
    for _ in range(3):
        osc.take_screenshot("full", keep=False)
    assert calls[1] == pytest.approx(0.1 * osc.STALL_FACTOR)
    assert calls[2] == pytest.approx(osc.FIRST_TRY_CEILING_S, abs=0.5), "a way that may be waiting on a dialog was cut short again"


def test_captures_from_an_earlier_run_are_removed(desktop):
    desktop.screens.mkdir(parents=True)
    old, new = desktop.screens / "Screenshot_old.png", desktop.screens / "Screenshot_new.png"
    _png(old)
    _png(new)
    os.utime(old, (osc._STARTED - 60, osc._STARTED - 60))
    osc._prune_own_captures()
    assert not old.exists() and new.exists()
