"""How play_specific reaches Spotify: its own search first, then typing, then the results page. Nothing is
looked up on open.spotify.com (a web call, and its page carries no results)."""
# The media layer lives in effectors/media.py (the executor re-exports it), so patch it there.
from eli.execution.effectors import media as ex


def test_play_specific_asks_spotify_for_its_top_result_first(monkeypatch):
    """Spotify plays its top search result over MPRIS before anything types into its window: that
    path needs no window focus, so it is the one that works on Wayland."""
    typed = []
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_launch_if_needed", lambda: True)
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_play_top_search_result",
                        lambda q, timeout=10.0, launch=True, accept=None: True)
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_search_type_and_play",
                        lambda q, launch=True: typed.append(q) or True)
    monkeypatch.setattr(ex, "_spotify_live_meta", lambda *a, **k: ("▶ Playing", "Veridian", "Silver lanterns"))

    result = ex.play_specific("silver lanterns by veridian", target="spotify")

    assert result["ok"] is True and result["played"] is True and not typed


def test_play_specific_types_into_spotify_when_the_top_result_is_not_it(monkeypatch):
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_launch_if_needed", lambda: True)
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_play_top_search_result",
                        lambda q, timeout=10.0, launch=True, accept=None: False)
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_search_type_and_play",
                        lambda q, launch=True: True)
    # what the player then reports: the song asked for, by its title and artist
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_live_track", lambda player="spotify": {
        "status": "playing", "artist": "Veridian", "title": "Silver Lanterns", "album": ""})

    result = ex.play_specific("silver lanterns by veridian", target="spotify")

    assert result["ok"] is True and result["played"] is True
    assert result["response"] == "Playing “Veridian — Silver Lanterns” on Spotify."


def test_play_specific_falls_back_to_search_when_no_track_uri_resolves(monkeypatch):
    """When both keystroke automation and URI resolution fail, the oldest
    search-then-play fallback must still run unchanged."""
    monkeypatch.setattr(
        "eli.integrations.media.cross_platform.spotify_search_type_and_play",
        lambda q: False,
    )
    monkeypatch.setattr(ex, "_spotify_running", lambda: True)
    monkeypatch.setattr(ex, "_spotify_search", lambda q, prefer=None: True)
    monkeypatch.setattr(ex, "_spotify_clear_track_repeat", lambda: True)
    monkeypatch.setattr(ex, "_spotify_play", lambda: False)
    monkeypatch.setattr(ex.time, "sleep", lambda _s: None)

    result = ex.play_specific("silver lanterns by veridian", target="spotify")

    assert result["played"] is False
    assert result["search_only"] is True


def test_next_media_uses_mpv_when_youtube_is_active(monkeypatch):
    monkeypatch.setattr(ex, "_targets_mpv", lambda target: True)
    monkeypatch.setattr(ex, "_mpv_ipc", lambda cmd, **kw: True)
    out = ex.next_media()
    assert out["action"] == "NEXT_MEDIA"
    assert "YouTube" in out["content"]
