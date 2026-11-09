"""The screen effector (eli/execution/effectors/screen.py) tells why a capture failed from what the
capture tried, each way and what it did, not a fixed install hint."""
from __future__ import annotations


def _failed_capture(monkeypatch, result):
    import eli.perception.os_controller as oc
    monkeypatch.setattr(oc, "take_screenshot", lambda region="full", keep=True: dict(result))


def test_a_failed_screen_read_says_what_each_way_did(monkeypatch):
    from eli.execution.effectors.screen import _h_screen_read_analyze
    _failed_capture(monkeypatch, {"ok": False, "error": "No screenshot was taken; install scrot or maim.",
                                  "attempts": [{"method": "the desktop's screenshot portal",
                                                "outcome": "did not answer in 25 s"},
                                               {"method": "grim", "outcome": "not supported by this compositor"}]})
    out = _h_screen_read_analyze("SCREEN_READ_ANALYZE", {"query": "what's on my screen"})
    assert out["ok"] is False
    assert "the desktop's screenshot portal: did not answer in 25 s" in out["response"]
    assert "grim: not supported by this compositor" in out["response"]
    assert "install" not in out["response"]
    assert len(out["attempts"]) == 2


def test_a_failed_screenshot_says_why(monkeypatch):
    from eli.execution.effectors.screen import _h_screenshot
    _failed_capture(monkeypatch, {"ok": False, "error": "x", "content": "Screenshot failed",
                                  "attempts": ["Pillow: the X display refused the connection"]})
    out = _h_screenshot("SCREENSHOT", {})
    assert out["ok"] is False
    assert out["response"] == "Screenshot failed: Pillow: the X display refused the connection"


def test_without_attempts_the_capture_error_is_the_reason(monkeypatch):
    from eli.execution.effectors.screen import _h_screenshot
    _failed_capture(monkeypatch, {"ok": False, "error": "screencapture did not produce a picture"})
    assert _h_screenshot("SCREENSHOT", {})["response"] == "Screenshot failed: screencapture did not produce a picture"
