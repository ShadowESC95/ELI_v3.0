"""Playing a named song on Spotify without window focus or keystrokes.

Under GNOME 50 (Wayland only) Spotify is a native Wayland window: xdotool and wmctrl cannot see
it, so the type-into-search path waited 12 s for a window that never appeared, refused to type
(it cannot confirm focus) twice, then opened an https search page that only shows results. Every
"play X" took 16 s and played nothing. Over MPRIS, OpenUri("spotify:search:<query>") makes the
desktop client play its top result (checked live on Spotify 1.2.82), and success is only claimed
when the song that starts is the one asked for.
"""
from __future__ import annotations

import pytest

from eli.integrations.media import cross_platform as cp


@pytest.mark.parametrize("query,artist,title,ok", [
    ("trouble orlo", "Orlo", "Trouble", True),
    ("trouble by orlo", "Orlo", "Trouble", True),
    ("trouble orlo", "Orlo", "Mockingbird", False),         # one shared word is not the song
    ("edge of the map quiet machine", "Quiet Machine", "Edge Of The Map", True),
    ("lose yourself orlo", "Orlo", "Lose Yourself - Original Demo Version", True),
    ("evil by orlo", "Quiet Machine", "Edge Of The Map", False),
    ("anything", "", "", False),
    ("juicy notorious big", "The Notorious B.I.G.", "Juicy", True),    # dotted initials
    ("dont stop me now queen", "Queen", "Don't Stop Me Now", True),
    # live: the right track started and was refused, "songs" and "and" not being in its name
    ("songs veridian and red tamsin", "Veridian", "North & South (feat. Red tamsin)", True),
    ("songs by orlo", "Orlo", "Mockingbird", True),         # only the artist was asked for
    ("some music by queen", "Queen", "Under Pressure", True),
    ("songs by orlo", "Quiet Machine", "Edge Of The Map", False),
])
def test_the_playing_song_must_be_the_one_asked_for(query, artist, title, ok):
    assert cp.query_matches_track(query, artist, title) is ok


def _as_track(meta):
    head, artist, title = meta[:3]
    status = "playing" if head.startswith("▶") else "paused" if head.startswith("⏸") else ""
    return {"status": status, "artist": artist, "title": title, "album": meta[3] if len(meta) > 3 else ""}


def _linux_spotify(monkeypatch, metas, *, bus=True):
    opened = []
    monkeypatch.setattr(cp.pc, "LINUX", True)
    monkeypatch.setattr(cp.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setattr(cp, "_session_bus_reachable", lambda: bus)
    monkeypatch.setattr(cp, "spotify_launch_if_needed", lambda: True)
    monkeypatch.setattr(cp, "spotify_wait_running", lambda timeout=8.0: True)
    monkeypatch.setattr(cp, "spotify_open_uri", lambda uri: opened.append(uri) or True)
    it = iter(metas)
    monkeypatch.setattr(cp, "spotify_live_track", lambda player="spotify": _as_track(next(it, metas[-1])))
    monkeypatch.setattr(cp.time, "sleep", lambda *_: None)
    return opened


class _Spotify:
    """Spotify on Linux as the live client behaves: a search opened over MPRIS plays its top result,
    the results tab of a search only shows them. `top` is what each search's top result is."""

    def __init__(self, monkeypatch, top, playing=None):
        self.top, self.opened, self.clock = top, [], [0.0]
        self.now = dict(playing or {"status": "paused", "artist": "Copper Tides", "title": "Low Tide",
                                    "album": "Shorelines"})
        monkeypatch.setattr(cp.pc, "LINUX", True)
        monkeypatch.setattr(cp.shutil, "which", lambda c: f"/usr/bin/{c}")
        monkeypatch.setattr(cp, "_session_bus_reachable", lambda: True)
        for name in ("spotify_launch_if_needed", "spotify_running", "spotify_play"):
            monkeypatch.setattr(cp, name, lambda: True)
        monkeypatch.setattr(cp, "spotify_wait_running", lambda timeout=8.0: True)
        monkeypatch.setattr(cp, "spotify_shuffle_state", lambda: False)
        monkeypatch.setattr(cp, "spotify_clear_track_repeat", lambda: False)
        monkeypatch.setattr(cp, "spotify_open_uri", self.open)
        monkeypatch.setattr(cp, "spotify_live_track", lambda player="spotify": dict(self.now))
        monkeypatch.setattr(cp.time, "monotonic", lambda: self.clock[0])
        monkeypatch.setattr(cp.time, "sleep", lambda s=0: self.clock.__setitem__(0, self.clock[0] + (s or 0.01)))

        def _no_network(*a, **k):
            raise AssertionError("Spotify is not looked up on the web")
        monkeypatch.setattr("urllib.request.urlopen", _no_network)

    def open(self, uri):
        self.opened.append(uri)
        words = uri[len("spotify:search:"):]
        if uri.startswith("spotify:search:") and "/" not in words and words in self.top:
            self.now = {"status": "playing", **self.top[words]}
        return True


_HARBOUR = {"artist": "Orlo", "title": "Lantern Song", "album": "The Harbour Years (Deluxe)"}
_OTHER = {"artist": "Orlo", "title": "Harbour Lights", "album": "Greatest Hits"}


def test_an_album_is_started_by_spotifys_search_and_judged_by_its_album(monkeypatch):
    """Every album failed: its search was opened on the albums tab, which only shows results, and the
    open.spotify.com lookup before it (a web call) finds nothing. "orlos" is Orlo's: the artist is Orlo."""
    from eli.execution.effectors import media as ex
    sp = _Spotify(monkeypatch, {"the harbour years orlo": _HARBOUR})
    out = ex.play_specific("orlos album the harbour years", "spotify")
    assert out["ok"] is True and out["played"] is True and out["kind"] == "album"
    assert sp.opened == ["spotify:search:the harbour years orlo"]
    assert "Lantern Song" in out["response"] and "The Harbour Years (Deluxe)" in out["response"]
    assert "orlos" not in out["response"].lower()


def test_an_album_search_that_starts_another_record_is_searched_by_its_album(monkeypatch):
    """The top result for an album's words may be a song of the artist's from another record: turned
    down as soon as it starts, and the search is made again held to the album's name."""
    from eli.execution.effectors import media as ex
    sp = _Spotify(monkeypatch, {"the harbour years orlo": _OTHER,
                                "album:the harbour years artist:orlo": _HARBOUR})
    out = ex.play_specific("the album the harbour years by orlo", "spotify")
    assert out["played"] is True and "Lantern Song" in out["response"]
    assert sp.opened == ["spotify:search:the harbour years orlo", "spotify:search:album:the harbour years artist:orlo"]
    assert sp.clock[0] < 5, "a wrong record that started is not waited on"


def test_an_album_spotify_cannot_find_is_said_with_what_is_playing(monkeypatch):
    from eli.execution.effectors import media as ex
    _Spotify(monkeypatch, {"the harbour years orlo": _OTHER, "album:the harbour years artist:orlo": _OTHER})
    out = ex.play_specific("the album the harbour years by orlo", "spotify")
    assert out["ok"] is False and out["played"] is False
    assert "Greatest Hits" in out["response"] and "not it" in out["response"]


def test_the_top_result_is_asked_for_over_mpris_and_confirmed(monkeypatch):
    opened = _linux_spotify(monkeypatch, [("⏸ Paused", "Orlo", "Mockingbird"),
                                          ("▶ Playing", "Orlo", "Trouble")])
    assert cp.spotify_play_top_search_result("trouble orlo") is True
    # the spotify: form, which plays; the https search page only shows results
    assert opened == ["spotify:search:trouble orlo"]


def test_a_different_song_starting_is_not_success(monkeypatch):
    clock = iter(range(0, 1000))
    monkeypatch.setattr(cp.time, "monotonic", lambda: next(clock))
    _linux_spotify(monkeypatch, [("▶ Playing", "Coldplay", "Trouble")])
    assert cp.spotify_play_top_search_result("trouble orlo", timeout=6) is False


def test_without_a_session_bus_nothing_is_launched(monkeypatch):
    launched = []
    _linux_spotify(monkeypatch, [("", "", "")], bus=False)
    monkeypatch.setattr(cp, "spotify_launch_if_needed", lambda: launched.append(1) or True)
    assert cp.spotify_play_top_search_result("trouble orlo") is False
    assert not launched


def test_typing_is_not_tried_on_a_window_the_x_tools_cannot_see(monkeypatch):
    from eli.system import portable_app_control as pac
    from eli.utils import platform_compat as platc
    typed = []
    monkeypatch.setattr(cp, "spotify_launch_if_needed", lambda: True)
    monkeypatch.setattr(cp, "spotify_wait_running", lambda timeout=8.0: True)
    monkeypatch.setattr(pac, "display_server", lambda: "wayland")
    monkeypatch.setattr(pac, "window_exists", lambda name: False)
    monkeypatch.setattr(pac, "focus_app", lambda name: {"ok": True})
    monkeypatch.setattr(platc, "type_text", lambda text: typed.append(text) or True)
    monkeypatch.setattr(cp.time, "sleep", lambda *_: None)
    assert cp.spotify_search_type_and_play("trouble orlo") is False
    assert not typed


def test_the_reply_names_what_is_playing_and_never_asks_the_user_to_press_play(monkeypatch):
    from eli.execution.effectors import media as ex
    monkeypatch.setattr(cp, "spotify_launch_if_needed", lambda: True)
    monkeypatch.setattr(cp, "spotify_play_top_search_result", lambda q, timeout=10.0, launch=True, accept=None: False)
    monkeypatch.setattr(cp, "spotify_search_type_and_play", lambda q, launch=True: False)
    monkeypatch.setattr(ex, "_spotify_running", lambda: True)
    monkeypatch.setattr(ex, "_spotify_search", lambda q, prefer=None: True)
    monkeypatch.setattr(ex, "_spotify_play", lambda: True)
    monkeypatch.setattr(ex, "_spotify_clear_track_repeat", lambda: False)
    monkeypatch.setattr(ex, "_now_playing_matches_query", lambda q: False)
    monkeypatch.setattr(ex, "_spotify_live_meta", lambda: ("▶ Playing", "Coldplay", "Trouble"))
    monkeypatch.setattr(ex.time, "sleep", lambda *_: None)
    r = ex.play_specific("trouble by orlo", "spotify")
    text = r["response"].lower()
    assert "coldplay" in text and "press play" not in text


# ── the song asked for, by its title and artist ──────────────────────────────────────────────────────
@pytest.mark.parametrize("title,artist,got_artist,got_title,ok", [
    ("trouble", "orlo", "Orlo", "Trouble", True),
    ("trouble", "orlo", "Orlo", "Crack the Bottle", False),      # the artist's best-known song
    ("evil", "orlo", "Orlo", "Evil Deeds", False),               # a title that holds the words and more
    ("evil", "orlo", "Orlo", "Evil - Remastered 2011", True),    # what a version adds is not more
    ("sailors lantern", "orlo", "Orlo", "Sailor's Lantern", True),
    ("north and south", "veridian", "Veridian", "North & South (feat. Red tamsin)", True),
    ("trouble", "orlo", "Coldplay", "Trouble", False),
    ("music", "orlo", "Orlo", "Music", True),                    # a title that is a kind word
])
def test_a_song_is_judged_by_its_title_and_its_artist(title, artist, got_artist, got_title, ok):
    assert cp.track_matches(title, artist, {"artist": got_artist, "title": got_title}) is ok


_ITS_BEST_KNOWN = {"artist": "Orlo", "title": "Crack the Bottle", "album": "Hits"}
_TROUBLE = {"artist": "Orlo", "title": "Trouble", "album": "Harbour Years"}


def test_a_song_the_words_do_not_find_is_searched_for_by_its_title_and_artist(monkeypatch):
    """"play trouble by <artist>" played the artist's best-known song: Spotify's top result for the
    words. It is turned down when it starts and the search is held to the title and the artist."""
    from eli.execution.effectors import media as ex
    sp = _Spotify(monkeypatch, {"trouble orlo": _ITS_BEST_KNOWN, "track:trouble artist:orlo": _TROUBLE})
    out = ex.play_specific("trouble by orlo", "spotify")
    assert out["played"] is True and out["response"] == "Playing “Orlo — Trouble” on Spotify."
    assert sp.opened == ["spotify:search:trouble orlo", "spotify:search:track:trouble artist:orlo"]


def test_a_song_spotify_cannot_find_is_said_and_nothing_else_is_started(monkeypatch):
    """When neither search starts it, the reply names the song asked for and what is playing, and the
    track paused before is not resumed: play was pressed after a results tab, and it undid a pause."""
    from eli.execution.effectors import media as ex
    sp = _Spotify(monkeypatch, {"trouble orlo": _ITS_BEST_KNOWN})
    pressed = []
    monkeypatch.setattr(cp, "spotify_play", lambda: pressed.append(1) or True)
    out = ex.play_specific("trouble by orlo", "spotify")
    assert out["ok"] is False and out["mismatch"] is True
    assert "Orlo — Crack the Bottle" in out["response"] and "“trouble” by orlo" in out["response"]
    assert not pressed and all("/" not in u for u in sp.opened)
