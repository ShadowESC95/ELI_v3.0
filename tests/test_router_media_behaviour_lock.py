from eli.execution import router_enhanced as router
import pytest
from eli.execution.router_enhanced import _eli_mqc_clean_query as clean


def _route(text: str) -> dict:
    fn = getattr(router, "route_command", None) or getattr(router, "route", None)
    assert callable(fn), "router has no route_command/route callable"
    out = fn(text)
    assert isinstance(out, dict), f"router returned non-dict for {text!r}: {out!r}"
    assert "action" in out, f"router output missing action for {text!r}: {out!r}"
    return out


def _assert_action(text: str, action: str):
    out = _route(text)
    assert out["action"] == action, f"{text!r} -> {out!r}"
    return out


def _assert_arg(text: str, key: str, expected):
    out = _route(text)
    args = out.get("args") or {}
    assert args.get(key) == expected, f"{text!r} expected args[{key!r}]={expected!r}, got {out!r}"
    return out


def test_direct_media_controls_do_not_need_wake_word():
    _assert_action("pause", "PAUSE_MEDIA")
    _assert_action("resume", "PLAY_MEDIA")
    _assert_action("next", "NEXT_MEDIA")
    _assert_action("previous", "PREVIOUS_MEDIA")
    _assert_action("volume up", "VOLUME")
    _assert_action("volume down", "VOLUME")
    _assert_action("vol up", "VOLUME")
    _assert_action("vol down", "VOLUME")


def test_bare_or_incomplete_play_does_not_launch_garbage():
    """
    Lock the important behaviour:
    incomplete play/media phrases must NOT launch garbage media.

    The exact user-facing NOOP wording may come from either the media
    normalizer or the voice/fuzzy incomplete-command path.
    """
    for text in ["play", "play the", "play the watcher by", "play ghetto gospel on"]:
        out = _route(text)
        assert out["action"] == "NOOP"

        args = out.get("args") or {}
        msg = f"{args.get('message', '')} {args.get('response', '')}".lower()

        assert any(
            phrase in msg
            for phrase in [
                "what to play",
                "artist name",
                "service",
                "incomplete command",
                "keep speaking",
                "connector",
            ]
        ), (text, out)

def test_app_open_close_routes_are_preserved():
    _assert_arg("open spotify", "name", "spotify")
    _assert_action("open spotify", "OPEN_APP")

    out = _assert_action("open youtube", "OPEN_URL")
    assert (out.get("args") or {}).get("url", "").startswith("https://")

    _assert_arg("close spotify", "name", "spotify")
    _assert_action("close spotify", "CLOSE_APP")

    _assert_arg("close youtube", "name", "youtube")
    _assert_action("close youtube", "CLOSE_APP")

    _assert_arg("vera close youtube", "name", "youtube")
    _assert_action("vera close youtube", "CLOSE_APP")


def test_targeted_youtube_mpv_controls_are_preserved():
    _assert_action("pause youtube", "PAUSE_MEDIA")
    _assert_arg("pause youtube", "target", "youtube")

    _assert_action("youtube pause", "PAUSE_MEDIA")
    _assert_arg("youtube pause", "target", "youtube")

    _assert_action("pause mpv", "PAUSE_MEDIA")
    _assert_arg("pause mpv", "target", "mpv")

    _assert_action("pause and tv", "PAUSE_MEDIA")
    _assert_arg("pause and tv", "target", "mpv")


def test_youtube_and_spotify_play_queries_are_preserved():
    _assert_action("play youtube dr dre the watcher", "PLAY_MEDIA")
    _assert_arg("play youtube dr dre the watcher", "target", "youtube")
    _assert_arg("play youtube dr dre the watcher", "query", "dr dre the watcher")

    _assert_action("play the watcher by dr dre", "PLAY_MEDIA")
    _assert_arg("play the watcher by dr dre", "target", "spotify")
    _assert_arg("play the watcher by dr dre", "query", "the watcher by dr dre")

    _assert_action("play dmx on spotify", "PLAY_MEDIA")
    _assert_arg("play dmx on spotify", "target", "spotify")
    _assert_arg("play dmx on spotify", "query", "dmx")

    _assert_action("spotify play tupac all eyez", "PLAY_MEDIA")
    _assert_arg("spotify play tupac all eyez", "target", "spotify")
    _assert_arg("spotify play tupac all eyez", "query", "tupac all eyez")

    _assert_action("on spotify play tupac all eyez", "PLAY_MEDIA")
    _assert_arg("on spotify play tupac all eyez", "target", "spotify")
    _assert_arg("on spotify play tupac all eyez", "query", "tupac all eyez")

    _assert_action("play the far shore by quiet machine on spotify", "PLAY_MEDIA")
    _assert_arg("play the far shore by quiet machine on spotify", "target", "spotify")
    _assert_arg("play the far shore by quiet machine on spotify", "query", "the far shore by quiet machine")

    _assert_action("play the lighthouse by quiet machine on youtube", "PLAY_MEDIA")
    _assert_arg("play the lighthouse by quiet machine on youtube", "target", "youtube")
    _assert_arg("play the lighthouse by quiet machine on youtube", "query", "the lighthouse by quiet machine")

    _assert_action("play al jazeera on youtube.com", "PLAY_MEDIA")
    _assert_arg("play al jazeera on youtube.com", "target", "youtube website")
    _assert_arg("play al jazeera on youtube.com", "query", "al jazeera")


def test_implied_song_by_artist_defaults_to_spotify():
    _assert_action("all eyez on me by tupac", "PLAY_MEDIA")
    _assert_arg("all eyez on me by tupac", "target", "spotify")
    _assert_arg("all eyez on me by tupac", "query", "all eyez on me by tupac")


# Punctuation between words is not part of the song.
#
# Live: "play evil; by orlo on spotify" searched "evil; orlo".
@pytest.mark.parametrize("said,query", [
    ("evil; by orlo", "evil by orlo"),
    ('"lose yourself" by orlo', "lose yourself by orlo"),
    ("hey, soul sister by train", "hey soul sister by train"),
    ("evil... by orlo", "evil by orlo"),
    ("stan?", "stan"),
])
def test_separating_punctuation_goes(said, query):
    assert clean(said) == query


@pytest.mark.parametrize("name", ["so what by p!nk", "AC/DC back in black", "mr. brightside by the killers",
                                  "the notorious b.i.g. juicy", "what's my age again by blink-182"])
def test_punctuation_inside_a_name_stays(name):
    assert clean(name) == name


@pytest.mark.parametrize("said,query,target", [
    ("play golden hour playlist spotify", "golden hour playlist", "spotify"),
    ("no, play golden hour on youtube", "golden hour", "youtube"),
    ("play golden hour in youtube", "golden hour", "youtube"),
])
def test_a_play_that_names_something_on_an_app_plays_it(said, query, target):
    """All three were read as "resume Spotify/YouTube" (MEDIA_CONTROL) and the song was dropped."""
    out = _route(said)
    assert out["action"] == "PLAY_MEDIA", out
    assert out["args"]["query"].lower() == query and out["args"]["target"] == target


def test_a_bare_transport_word_to_an_app_is_still_transport():
    assert _route("mute vlc")["action"] == "MEDIA_CONTROL"
    assert _route("skip this song on spotify")["action"] == "MEDIA_CONTROL"
    assert _route("play songs from the eighties")["args"].get("target") is None   # "from" names no service here


@pytest.mark.parametrize("said,state,player_word", [("shuffle on", "on", "On"), ("shuffle off", "off", "Off"),
                                                    ("turn shuffle off", "off", "Off")])
def test_shuffle_on_or_off_asks_for_that_state(said, state, player_word, monkeypatch):
    """Both were a toggle with no arguments: "shuffle off" with shuffle already off turned it on. Then
    the router said "mode" and the action read "state", so the state asked for never reached it."""
    out = _route(said)
    assert out["action"] == "SHUFFLE_MEDIA" and out["args"].get("state") == state
    from eli.execution.effectors import media
    import eli.integrations.media.cross_platform as cross
    sent = []
    monkeypatch.setattr(media, "_resolve_media_target", lambda target, what: "spotify")
    monkeypatch.setattr(cross, "spotify_shuffle", lambda p, mode="Toggle": sent.append(mode) or {"ok": True})
    media._h_shuffle_media("SHUFFLE_MEDIA", out["args"])
    assert sent == [player_word]


def test_a_bare_shuffle_still_toggles():
    assert "state" not in _route("shuffle")["args"]


@pytest.mark.parametrize("said,target", [("play golden hour in example.ch", "example.ch"),
                                         ("play golden hour at example.ch", "example.ch")])
def test_a_site_named_after_in_or_at_is_where_to_play(said, target):
    out = _route(said)
    assert out["action"] == "PLAY_MEDIA" and out["args"]["target"] == target and out["args"]["query"] == "golden hour"


@pytest.mark.parametrize("said,kind", [("play the album random access memories on spotify", "album"),
                                       ("play songs similar to golden hour on spotify", "similar"),
                                       ("play the film arrival on netflix", "film")])
def test_what_kind_of_thing_a_play_asks_for_is_passed_on(said, kind):
    assert _route(said)["args"].get("kind") == kind


def test_a_play_that_names_no_kind_says_none():
    assert "kind" not in _route("play golden hour on spotify")["args"]
