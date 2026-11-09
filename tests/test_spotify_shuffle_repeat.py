"""Shuffle/repeat media control tests."""
# The media layer lives in effectors/media.py (the executor re-exports it), so patch it there.
from eli.execution.effectors import media as ex


def test_shuffle_media_uses_cross_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eli.integrations.media.cross_platform.spotify_shuffle",
        lambda p, mode="Toggle": calls.append((p, mode)) or {"ok": True, "content": "🔀 Shuffle on — spotify",
                                                             "response": "🔀 Shuffle on — spotify"},
    )
    monkeypatch.setattr(ex, "_get_active_player", lambda *a, **k: "spotify")
    out = ex.shuffle_media()
    assert out["action"] == "SHUFFLE_MEDIA"
    assert out["ok"] is True
    assert calls == [("spotify", "Toggle")]      # no state asked for: toggled


def test_repeat_media_uses_cross_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eli.integrations.media.cross_platform.spotify_set_loop",
        lambda p: calls.append(p) or {"ok": True, "content": "🔁 Repeat track — spotify",
                                      "response": "🔁 Repeat track — spotify"},
    )
    monkeypatch.setattr(ex, "_get_active_player", lambda *a, **k: "spotify")
    out = ex.repeat_media()
    assert out["action"] == "REPEAT_MEDIA"
    assert out["ok"] is True
    assert calls == ["spotify"]
