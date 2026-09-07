"""Tests for Spotify intent parsing (no network)."""
from eli.integrations.media import spotify_intent as si


def test_sanitize_strips_chat_log_prefix():
    q = "🧑 You [11:51:07]: play the liar and a thief album diabolic"
    cleaned = si.sanitize_media_query(q)
    assert "the liar and a thief album diabolic" == cleaned
    assert "🧑" not in cleaned


def test_liked_songs_playlist_name():
    assert si.playlist_name("my liked songs playlist") == "liked songs"
    assert si.is_liked_songs("liked songs")


def test_album_by_artist():
    name, artist = si.album_request("a liar and a thief album by diabolic")
    assert name == "a liar and a thief"
    assert artist == "diabolic"


def test_album_without_artist():
    name, artist = si.album_request("the marshall mathers lp album")
    assert name == "marshall mathers lp"
    assert artist is None


def test_artist_songs_request():
    assert si.artist_songs_request("songs by diabolic") == "diabolic"


def test_prefers_spotify_for_album_phrasing():
    assert si.prefers_spotify_music_context("the marshall mathers lp album")
