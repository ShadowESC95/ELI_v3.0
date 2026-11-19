"""Regression: 'play X by Y on spotify' must open ONLY Spotify — never YouTube.

User report: requesting a Spotify song opened the search in BOTH YouTube and
Spotify and never actually played. Root cause was play_specific()'s Spotify
branch only returning when the dbus search reported success; on any failure it
fell through into the YouTube sections and opened a second platform.

These tests assert that an explicit Spotify target stays on Spotify in both the
success and the unreachable-Spotify paths.
"""
from __future__ import annotations

import shutil
import subprocess

# The media layer lives in effectors/media.py (the executor re-exports it), so patch it there.
from eli.execution.effectors import media as ex
from eli.integrations.media import cross_platform as cp


def _install_capture(monkeypatch, *, run_rc=0, run_stdout="Playing"):
    calls = {"popen": [], "run": []}

    class _P:
        pid = 1

    class _R:
        returncode = run_rc
        stdout = run_stdout
        stderr = ""

    def fake_popen(argv, *a, **k):
        calls["popen"].append(list(argv) if isinstance(argv, (list, tuple)) else [str(argv)])
        return _P()

    def fake_run(argv, *a, **k):
        calls["run"].append(list(argv) if isinstance(argv, (list, tuple)) else [str(argv)])
        return _R()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ex.time, "sleep", lambda *_a, **_k: None)
    return calls


def _has_youtube(calls):
    blob = " ".join(" ".join(c) for c in calls["popen"] + calls["run"]).lower()
    return ("youtube" in blob) or ("ytsearch" in blob) or ("ytdl" in blob)


def test_spotify_target_plays_and_never_opens_youtube(monkeypatch):
    # Spotify reachable; status reports Playing AND live metadata matches the
    # requested track -> honest played=True. Status alone ("Playing") isn't
    # enough on its own — that's also true of a stale, already-playing track
    # that the request never touched; live metadata is what actually confirms
    # the right song loaded (see test_now_playing_matches_query regression).
    monkeypatch.setattr(shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in
                        {"xdg-open", "dbus-send", "playerctl"} else None)
    calls = _install_capture(monkeypatch, run_rc=0, run_stdout="Playing")
    monkeypatch.setattr(cp, "spotify_live_meta",
                         lambda player="spotify": ("▶ Playing", "The Notorious B.I.G.", "Juicy"))

    res = ex.play_specific("juicy by notorious big", "spotify")

    assert res["action"] == "PLAY_MEDIA"
    assert res.get("played") is True
    assert not _has_youtube(calls), "Spotify request must not open YouTube"
    # what played, as fields the record keeps (the reply's wording is not what is read back)
    assert res["track"]["source"] == "spotify" and res["track"]["confirmed"] is True
    assert res["track"].get("title") == "Juicy" and res["track"].get("artist") == "The Notorious B.I.G."


def test_spotify_unreachable_reports_search_only_not_youtube(monkeypatch):
    # Every dbus/playerctl call fails and Spotify is not running -> must NOT
    # fall through to YouTube; must return an honest search_only/failure result.
    monkeypatch.setattr(shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in
                        {"xdg-open", "dbus-send", "playerctl"} else None)
    calls = _install_capture(monkeypatch, run_rc=1, run_stdout="")

    res = ex.play_specific("juicy by notorious big", "spotify")

    assert res["action"] == "PLAY_MEDIA"
    assert res.get("played") is not True
    assert res.get("search_only") is True
    assert res.get("target") == "spotify"


def test_spotify_stale_track_resuming_is_not_reported_as_the_requested_song(monkeypatch):
    """Regression (live session, 2026-10-01): "play evil by orlo on spotify"
    opened a spotify:search: URI then hit MPRIS Play. Spotify had an unrelated
    track already queued from earlier and just resumed it — playerctl status
    correctly says "Playing", but it's the wrong track. The old code trusted
    status alone and told the user "Playing 'evil by orlo' on Spotify" when
    nothing of the sort had happened. Now it must report search_only instead."""
    monkeypatch.setattr(shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in
                        {"xdg-open", "dbus-send", "playerctl"} else None)
    calls = _install_capture(monkeypatch, run_rc=0, run_stdout="Playing")
    monkeypatch.setattr(cp, "spotify_live_meta",
                         lambda player="spotify": ("▶ Playing", "Edge Of The Map", "Quiet Machine"))

    res = ex.play_specific("evil by orlo", "spotify")

    assert res["action"] == "PLAY_MEDIA"
    assert res.get("played") is not True, "must not claim success for an unrelated stale track"
    assert res.get("search_only") is True
    assert not _has_youtube(calls), "must not fall through to YouTube either"


# ── YouTube continuous play: watch URL becomes a Mix/radio so it autoplays ────
def test_yt_mix_url_adds_radio_playlist():
    from eli.integrations.media.youtube_playback import yt_mix_url
    assert yt_mix_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == \
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ"
    # extra params preserved-but-reseeded to the video id
    assert yt_mix_url("https://www.youtube.com/watch?v=abc123XYZ_-&t=5").endswith("list=RDabc123XYZ_-")
    # non-watch URLs and None pass through unchanged
    assert yt_mix_url(None) is None
    assert "list=RD" not in yt_mix_url("https://www.youtube.com/results?search_query=x")


def test_youtube_dot_com_uses_autoplay_mix_in_browser(monkeypatch):
    from eli.integrations.media import youtube_playback as yp
    opened = []
    # the one results-page reader: media.py's own copy of it, which this patch did not reach, is gone
    monkeypatch.setattr(yp, "yt_resolve_watch_url", lambda q: "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    monkeypatch.setattr("eli.integrations.media.media_deps.yt_dlp_argv", lambda *a, **k: [])
    monkeypatch.setattr(ex, "_open_in_browser", lambda url: opened.append(url))
    monkeypatch.setattr(yp, "yt_autoplay_enabled", lambda: True)

    res = ex.play_specific("real night owl", "youtube website")

    assert res["action"] == "PLAY_MEDIA"
    assert opened, "browser was not opened"
    url = opened[0]
    assert "list=RD" in url
    assert "autoplay=1" in url
    assert "start_radio=1" in url


def test_spotify_playback_confirmed_rejects_unchanged_stale_track(monkeypatch):
    """Regression: the album/playlist search-tab fallbacks had the same bare
    `if _spotify_play():` false-positive the track fallback had — a stale,
    already-playing track reports identical "Playing" status whether or not
    anything new actually got selected. Used by both fallbacks now."""
    monkeypatch.setattr(ex, "_spotify_play", lambda: True)
    monkeypatch.setattr(ex, "_spotify_live_meta",
                         lambda *a, **k: ("▶ Playing", "Quiet Machine", "Harbour Streets"))
    before = ("Quiet Machine", "Harbour Streets")
    assert ex._spotify_playback_confirmed(before) is False


def test_spotify_playback_confirmed_accepts_a_real_change(monkeypatch):
    monkeypatch.setattr(ex, "_spotify_play", lambda: True)
    monkeypatch.setattr(ex, "_spotify_live_meta",
                         lambda *a, **k: ("▶ Playing", "Pink Floyd", "Breathe"))
    before = ("", "")
    assert ex._spotify_playback_confirmed(before) is True


def test_spotify_playback_confirmed_checks_hint_when_given(monkeypatch):
    """An album play must verify the live artist matches the requested album's
    artist, not just that *some* different track started."""
    monkeypatch.setattr(ex, "_spotify_play", lambda: True)
    monkeypatch.setattr(ex, "_spotify_live_meta",
                         lambda *a, **k: ("▶ Playing", "Some Other Artist", "Unrelated Track"))
    before = ("", "")
    assert ex._spotify_playback_confirmed(before, hint="pink floyd") is False


def test_spotify_playback_confirmed_false_when_play_itself_fails(monkeypatch):
    monkeypatch.setattr(ex, "_spotify_play", lambda: False)
    assert ex._spotify_playback_confirmed(("", "")) is False


def test_my_liked_songs_without_the_word_playlist_opens_liked_collection(monkeypatch):
    """Regression (live session): "play my liked songs in spotify" has no
    literal "playlist" in it, so playlist_name() extracted nothing and the
    liked-songs branch (gated behind a truthy extracted name) never ran —
    the phrase got typed verbatim into Spotify's track search, which then
    reported a false "Playing" success by resuming whatever track was
    already queued from before. Must open the real Liked Songs collection."""
    monkeypatch.setattr(shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in
                        {"xdg-open", "dbus-send", "playerctl"} else None)
    _install_capture(monkeypatch, run_rc=0, run_stdout="Playing")

    opened = []
    monkeypatch.setattr(ex, "_spotify_open_liked_songs", lambda: opened.append(1) or True)
    live_meta = iter([("", ""), ("Some Artist", "Some Track")])
    monkeypatch.setattr(ex, "_spotify_live_meta", lambda player="spotify": ("", *next(live_meta)))

    res = ex.play_specific("my liked songs", "spotify")

    assert opened == [1], "must open the real Liked Songs collection, not search for it"
    assert res.get("kind") == "liked_songs"
    assert res.get("played") is True


def test_my_liked_playlist_opens_liked_collection_not_a_named_playlist_search(monkeypatch):
    """Regression: "play my liked playlist on spotify" extracted playlist
    name "liked" (the generic "X playlist" pattern), but is_liked_songs()
    required "liked song(s)" and rejected bare "liked" — so it searched
    Spotify for a playlist literally named "liked" instead."""
    monkeypatch.setattr(shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in
                        {"xdg-open", "dbus-send", "playerctl"} else None)
    _install_capture(monkeypatch, run_rc=0, run_stdout="Playing")

    opened = []
    searched = []
    monkeypatch.setattr(ex, "_spotify_open_liked_songs", lambda: opened.append(1) or True)
    monkeypatch.setattr(ex, "_spotify_search", lambda q, prefer=None: searched.append(q) or True)
    live_meta = iter([("", ""), ("Some Artist", "Some Track")])
    monkeypatch.setattr(ex, "_spotify_live_meta", lambda player="spotify": ("", *next(live_meta)))

    res = ex.play_specific("my liked playlist", "spotify")

    assert opened == [1]
    assert searched == [], "must not fall through to a named-playlist search for 'liked'"
    assert res.get("kind") == "liked_songs"


def test_artist_album_name_phrasing_reaches_the_album_block_not_track_search(monkeypatch):
    """Regression (live session): "play veridians album salt and a stone" and
    "play the copper tides album ox" both fell through to the generic
    track-search fallback, typing the whole phrase verbatim and playing an
    unrelated top result — album_request() didn't recognise "ARTIST album
    ALBUMNAME" phrasing at all (fixed separately in spotify_intent). This
    confirms play_specific() actually routes through the album path now
    that the parser recognises it, rather than falling to track search."""
    monkeypatch.setattr(shutil, "which",
                        lambda c: f"/usr/bin/{c}" if c in
                        {"xdg-open", "dbus-send", "playerctl"} else None)
    _install_capture(monkeypatch, run_rc=0, run_stdout="Playing")

    track_search_calls = []
    album_uri_calls = []
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_resolve_track_uri", lambda q: None)
    monkeypatch.setattr(ex, "_spotify_resolve_album_uri",
                         lambda name, artist=None: album_uri_calls.append((name, artist)) or None)
    monkeypatch.setattr(ex, "_spotify_search",
                         lambda q, prefer=None: track_search_calls.append((q, prefer)) or False)
    monkeypatch.setattr(
        "eli.integrations.media.cross_platform.spotify_search_type_and_play",
        lambda q: False,
    )

    ex.play_specific("the copper tides album ox", "spotify")

    assert album_uri_calls == [("ox", "copper tides")], (
        "must resolve as an album request (name='ox', artist='copper tides')"
    )
    # The artist is searched in the form that finds both readings of an "s" said without an
    # apostrophe ("veridians album ..." is Veridian's; "copper tides" is the name): search matches
    # word beginnings, so "copper tide" finds Copper Tides, and "veridian" finds Veridian.
    assert ("ox copper tide", "albums") in track_search_calls, (
        "the album search-tab fallback must search for the album name + "
        "artist, not get skipped"
    )


# ── a site ELI has no player for ────────────────────────────────────────────────────────────────────
def _nothing_plays(monkeypatch):
    """Every player ELI has is a failure if it is reached."""
    def _never(*a, **k):
        raise AssertionError("no other service may be used")
    monkeypatch.setattr(ex, "_play_on_spotify", _never)
    monkeypatch.setattr(ex, "_spotify_play_track", _never)
    monkeypatch.setattr("eli.integrations.media.youtube_playback.attempt_youtube_mpv", _never)
    monkeypatch.setattr(ex, "_open_in_browser", _never)


def test_play_on_an_unknown_site_opens_that_site_and_plays_nothing_else(monkeypatch):
    """"play X on <site>" with no player for the site played a Spotify track or a YouTube video and
    never mentioned the site."""
    _nothing_plays(monkeypatch)
    opened = []
    monkeypatch.setattr(ex, "_execute_impl",
                        lambda action, args: opened.append((action, args)) or {"ok": True, "action": action})
    out = ex.play_specific("the lighthouse keeper", "movy.xy")
    assert opened == [("OPEN_URL", {"url": "https://movy.xy/"})]
    assert out["played"] is False and "movy.xy" in out["response"]
    assert "the lighthouse keeper" in out["response"]


def test_play_on_a_site_with_no_known_address_says_so(monkeypatch):
    _nothing_plays(monkeypatch)
    import eli.runtime.site_address as sa
    monkeypatch.setattr(sa, "named", lambda name, **k: {"why": "network access is switched off"})
    monkeypatch.setattr(ex, "_execute_impl", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing to open")))
    out = ex.play_specific("the lighthouse keeper", "movy")
    assert out["ok"] is False and out["error"] == "unknown_site"
    assert "network access is switched off" in out["response"]


# ── where an untargeted request goes ────────────────────────────────────────────────────────────────
def _record(monkeypatch, runs):
    import eli.runtime.action_outcomes as ao
    import time as _t
    now = _t.time()
    monkeypatch.setattr(ao, "_runs", lambda **k: [
        {"action": "PLAY_MEDIA", "worked": True, "ts": now - i, "args": args, "result": {"played": True}}
        for i, args in enumerate(runs)])


def test_a_bare_title_with_spotify_open_is_asked_about(monkeypatch):
    """Where a 'play X' went depended only on whether Spotify was running: a show's title played a
    song of that name."""
    _record(monkeypatch, [])
    monkeypatch.setattr(ex, "_spotify_running", lambda: True)
    _nothing_plays(monkeypatch)
    out = ex.play_specific("puffin cove", None)
    assert out["asks_user"] is True and out["played"] is False
    assert {c["command"] for c in out["choices"]} == {"play puffin cove on spotify", "play puffin cove on youtube"}
    assert out["awaiting"]["command"] == "play puffin cove on {answer}"


def test_a_show_never_goes_to_spotify(monkeypatch):
    _record(monkeypatch, [])
    monkeypatch.setattr(ex, "_spotify_running", lambda: True)
    _nothing_plays(monkeypatch)
    out = ex.play_specific("puffin cove season 2 episode 3", None)
    assert out["asks_user"] is True
    assert all("spotify" not in c["command"] for c in out["choices"])


def test_where_the_person_played_the_same_thing_is_where_it_goes(monkeypatch):
    _record(monkeypatch, [{"query": "puffin cove", "target": "youtube"}])
    monkeypatch.setattr(ex, "_spotify_running", lambda: True)
    from eli.integrations.media.spotify_intent import media_request
    assert ex._where_to_play(media_request("puffin cove")) == "youtube"
    # a kind of thing follows where that kind was played: music the person sent to YouTube
    _record(monkeypatch, [{"query": "silver lanterns by veridian", "target": "youtube"}])
    assert ex._where_to_play(media_request("harbour lights by copper tides")) == "youtube"
    # a bare title learns nothing from another bare title
    _record(monkeypatch, [{"query": "north and south", "target": "youtube"}])
    assert ex._where_to_play(media_request("puffin cove")) is None


# ── kinds of request on Spotify ─────────────────────────────────────────────────────────────────────
def test_songs_similar_to_a_song_start_from_that_song(monkeypatch):
    """'songs similar to X' was searched as the title of one track and could never be accepted."""
    asked = []

    def _track(q):
        asked.append(q)
        ex._MEDIA_STATE["title"] = "Veridian — Silver Lanterns"
        return {"ok": True, "action": "PLAY_MEDIA", "played": True, "content": "x", "response": "x"}

    monkeypatch.setattr(ex, "_spotify_play_track", _track)
    out = ex.play_specific("songs similar to silver lanterns", "spotify")
    assert asked == ["silver lanterns"]
    assert out["played"] is True and out["kind"] == "similar" and out["seed"] == "silver lanterns"


def test_a_style_is_played_as_a_playlist_not_a_title(monkeypatch):
    searched = []
    monkeypatch.setattr(ex, "_spotify_play_track",
                        lambda q: (_ for _ in ()).throw(AssertionError("a style is not a track title")))
    monkeypatch.setattr(ex, "_spotify_search", lambda q, prefer=None: searched.append((q, prefer)) or True)
    monkeypatch.setattr(ex, "_spotify_live_meta", lambda *a, **k: ("▶ Playing", "Quiet Machine", "Rain"))
    monkeypatch.setattr(ex, "_spotify_playback_confirmed", lambda before, hint="": True)
    monkeypatch.setattr(ex, "_spotify_clear_track_repeat", lambda: False)
    monkeypatch.setattr(ex.time, "sleep", lambda *_a, **_k: None)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *_a, **_k: None)
    out = ex.play_specific("some lo-fi", "spotify")
    assert searched == [("lo-fi", "playlists")]
    assert out["played"] is True and out["kind"] == "genre"


def test_an_album_asked_for_with_its_artist_reaches_the_album_and_not_a_track_search(monkeypatch):
    """'play the album T by A' never reached the album path, and a failed album fell into a track
    search of the raw words, 'album' included."""
    album_uri = []
    monkeypatch.setattr(ex, "_spotify_resolve_album_uri",
                        lambda name, artist=None: album_uri.append((name, artist)) or None)
    monkeypatch.setattr(ex, "_spotify_search", lambda q, prefer=None: False)
    monkeypatch.setattr(ex, "_spotify_running", lambda: True)
    monkeypatch.setattr(ex, "_spotify_play_track",
                        lambda q: (_ for _ in ()).throw(AssertionError("an album is not a track search")))
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_shuffle_state", lambda: None)
    monkeypatch.setattr("eli.integrations.media.cross_platform.spotify_live_track",
                        lambda player="spotify": {"status": "", "artist": "", "title": "", "album": ""})
    out = ex.play_specific("the album salt and a stone by veridian (the full album)", "spotify")
    assert album_uri == [("salt and a stone", "veridian")]
    assert out["ok"] is False and out["kind"] == "album"
