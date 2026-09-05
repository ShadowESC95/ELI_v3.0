"""NOW_PLAYING must execute playerctl directly, never GGUF-synthesise."""
from __future__ import annotations

from eli.cognition.agent_bus import SystemAgent
from eli.execution.effectors import media as em
from eli.kernel.stages.fast_actions import (
    _DIRECT_FINAL_ACTIONS,
    _PHASE45_DIRECT_FAST_ACTIONS,
)


def test_now_playing_in_phase45_fast_path():
    assert "NOW_PLAYING" in _PHASE45_DIRECT_FAST_ACTIONS
    assert "NOW_PLAYING" in _DIRECT_FINAL_ACTIONS


def test_system_agent_includes_now_playing():
    assert "NOW_PLAYING" in SystemAgent.SYSTEM_ACTIONS


def test_liked_songs_request_detection():
    assert em._spotify_is_liked_songs_request("liked songs")
    assert em._spotify_is_liked_songs_request("my liked songs")
    assert not em._spotify_is_liked_songs_request("workout")


def test_now_playing_empty_state(monkeypatch):
    monkeypatch.setattr(em, "_get_active_player", lambda: None)
    monkeypatch.setattr(
        em, "_MEDIA_STATE",
        {"source": None, "title": None, "mpv_sock": None},
    )
    out = em.now_playing()
    assert out["action"] == "NOW_PLAYING"
    assert "Nothing is playing" in out["content"]


def test_now_playing_prefers_live_playerctl(monkeypatch):
    monkeypatch.setattr(
        em, "_MEDIA_STATE",
        {"source": "spotify", "title": "liked songs (playlist)", "mpv_sock": None},
    )
    monkeypatch.setattr(em, "_get_active_player", lambda: "spotify")
    monkeypatch.setattr(
        em, "_spotify_live_meta",
        lambda player="spotify": ("▶ Playing", "OTB ORTIZ", "Real One"),
    )
    out = em.now_playing()
    assert "OTB ORTIZ — Real One" in out["content"]
    assert "liked songs (playlist)" not in out["content"]
