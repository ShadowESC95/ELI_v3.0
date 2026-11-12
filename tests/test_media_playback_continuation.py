"""Behaviour locks for media playback: YouTube 403 recovery and Spotify context.

Observed live at 2.3.15:

  * "play drift along by the tide on youtube" failed with mpv rc=3 and
    `[ffmpeg] https: HTTP error 403 Forbidden` on a googlevideo URL carrying
    `c=ANDROID_VR`. A 403 there does not mean the video is unavailable — it
    means yt-dlp returned a stream URL bound to the client that requested it.
    There was one attempt and no recovery.
  * Asking for a song on Spotify played it and then repeated rather than
    continuing, and there was no way to ask for a playlist at all — the query
    went to Spotify's *search results* context, which has no continuation.

The client ladder lives in eli.integrations.media.youtube_playback (its one copy) — android
first, yt-dlp default last, overridable via ELI_YT_PLAYER_CLIENTS. The ladder is tested here only.
"""
import pytest

from eli.integrations.media import cross_platform as cp
from eli.integrations.media.spotify_intent import playlist_name
from eli.integrations.media.youtube_playback import yt_is_client_bound_failure


# ── YouTube: recover from a client-bound refusal, change nothing else ──────
def test_the_default_client_is_still_in_the_ladder(monkeypatch):
    """The ladder ends on whatever yt-dlp picks by itself, so playback that worked before still works."""
    from eli.integrations.media import yt_player_clients
    monkeypatch.delenv("ELI_YT_PLAYER_CLIENTS", raising=False)
    clients = yt_player_clients()
    assert "" in clients and len(clients) >= 2, "no recovery clients at all"
    assert clients[0] == "android" and clients[-1] == ""


def test_client_order_is_overridable(monkeypatch):
    from eli.integrations.media import yt_player_clients
    monkeypatch.setenv("ELI_YT_PLAYER_CLIENTS", "default,android,tv")
    assert yt_player_clients() == ["", "android", "tv"]


@pytest.mark.parametrize("tail", [
    "[ffmpeg] https: HTTP error 403 Forbidden",
    "HTTP Error 403: Forbidden",
    "server returned 403",
])
def test_403_is_retried(tail):
    assert yt_is_client_bound_failure(tail) is True


@pytest.mark.parametrize("tail", [
    "ERROR: Video unavailable",
    "Playlist ytsearch1: empty playlist",
    "ERROR: Private video. Sign in if you've been granted access",
])
def test_permanent_failures_are_not_retried(tail):
    """Retrying these just burns seconds before the fallback they were always
    going to get."""
    assert yt_is_client_bound_failure(tail) is False


# ── Spotify: a playlist is a different context from a song ─────────────────
@pytest.mark.parametrize("query,expected", [
    ("my workout playlist", "workout"),
    ("the chill playlist", "chill"),
    ("discover weekly playlist", "discover weekly"),
    ("a rainy day play list", "rainy day"),
])
def test_playlist_requests_are_recognised(query, expected):
    assert playlist_name(query) == expected


@pytest.mark.parametrize("query", [
    "the first light by quiet machine",
    "drift along by the tide",
    "my playlist",          # names nothing
    "",
])
def test_song_requests_are_not_mistaken_for_playlists(query):
    assert playlist_name(query) == ""


@pytest.fixture
def _spotify_loop(monkeypatch):
    """Spotify's loop setting as playerctl reports it, with every playerctl call recorded (none runs)."""
    state = {"loop": "None"}
    calls = []

    def run(argv, *, timeout=5.0):
        calls.append(list(argv))
        if argv[-1] == "loop":
            return True, state["loop"], ""
        if argv[-2:] == ["loop", "None"]:
            state["loop"] = "None"
        return True, "", ""

    monkeypatch.setattr(cp.pc, "LINUX", True)
    monkeypatch.setattr(cp.pc, "MACOS", False)
    monkeypatch.setattr(cp.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(cp, "_run", run)
    return state, calls


@pytest.mark.parametrize("loop", ["Playlist", "None"])
def test_repeat_one_is_only_cleared_when_actually_set(_spotify_loop, loop):
    """Repeat-one was NOT the cause on the reporting machine, so this must
    never overwrite a loop setting the user chose."""
    state, calls = _spotify_loop
    state["loop"] = loop
    assert cp.spotify_clear_track_repeat() is False
    assert ["playerctl", "-p", "spotify", "loop", "None"] not in calls
    assert state["loop"] == loop


def test_repeat_one_is_cleared_when_it_is_set(_spotify_loop):
    state, calls = _spotify_loop
    state["loop"] = "Track"
    assert cp.spotify_clear_track_repeat() is True
    assert ["playerctl", "-p", "spotify", "loop", "None"] in calls
