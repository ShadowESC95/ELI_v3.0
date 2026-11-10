"""play_pause is status-aware (regression, 2026-07-04).

The dashboard's now-playing pause button used playerctl's `play-pause` toggle, which
resumes reliably but frequently no-ops on *pausing* Spotify — so play worked, pause
didn't. Fix: play_pause queries the player's status and issues an EXPLICIT pause (when
playing) or play (when paused), the reliable path voice control uses. This locks in that
a playing player gets paused (not toggled), and a paused one gets resumed.
"""
import eli.integrations.mpris.playerctl_backend as mp
import pytest


def _track(monkeypatch, status):
    calls = []
    monkeypatch.setattr(mp, "get_player_status",
                        lambda p=None: {"ok": True, "player": "spotify", "status": status})
    monkeypatch.setattr(mp, "pause", lambda p=None: (calls.append(("pause", p)), {"ok": True})[1])
    monkeypatch.setattr(mp, "play", lambda p=None: (calls.append(("play", p)), {"ok": True})[1])
    return calls


def test_play_pause_pauses_when_playing(monkeypatch):
    calls = _track(monkeypatch, "playing")
    mp.play_pause("spotify")
    assert calls == [("pause", "spotify")], calls


def test_play_pause_plays_when_paused(monkeypatch):
    calls = _track(monkeypatch, "paused")
    mp.play_pause("spotify")
    assert calls == [("play", "spotify")], calls


def test_play_pause_falls_back_to_toggle_when_status_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(mp, "get_player_status", lambda p=None: {"ok": False, "error": "no player"})
    monkeypatch.setattr(mp, "_playerctl",
                        lambda cmd, player=None, extra=None: (calls.append((cmd, player)), {"ok": True})[1])
    mp.play_pause("spotify")
    assert calls and calls[0][0] == "play-pause", calls


# After "next", ELI names the track that is now playing, or none.
#
# Live: "next" -> "⏭ Next track — spotify (Orlo — Trouble)", the track it had just skipped:
# the player had not switched yet when ELI read what was playing.
class _Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def player(monkeypatch):
    state = {"track": "old", "switch_after": 3, "reads": 0}
    tracks = {"old": ("Orlo", "Trouble"), "new": ("Orlo", "Evil")}

    def metadata(p, key):
        state["reads"] += 1
        if state.get("sent") and state["reads"] >= state["switch_after"]:
            state["track"] = state.get("next_track", state["track"])
        artist, title = tracks[state["track"]]
        return {"mpris:trackid": f"/track/{state['track']}", "xesam:artist": artist, "artist": artist,
                "xesam:title": title, "title": title}.get(key, "")

    def run(argv, timeout=8, input_bytes=None):
        if argv[-1] in ("next", "previous"):
            state["sent"] = True
            state["reads"] = 0
        return True, "Playing" if argv[-1] == "status" else "", ""

    monkeypatch.setattr(mp, "_has", lambda tool: True)
    monkeypatch.setattr(mp, "get_active_player", lambda command=None: "spotify")
    monkeypatch.setattr(mp, "_metadata", metadata)
    monkeypatch.setattr(mp, "_run", run)
    monkeypatch.setattr(mp, "time", _Clock())
    return state


def test_next_names_the_track_it_moved_to(player):
    player["next_track"] = "new"
    r = mp.next_track()
    assert r["ok"] and "Evil" in r["content"] and "Trouble" not in r["content"]


def test_a_track_that_never_changed_is_not_named(player):
    r = mp.next_track()
    assert r["ok"] and "Trouble" not in r["content"]


# A bare "pause" goes to what is playing. A fixed list put Spotify first whenever it was open, so
# "pause" paused an already-paused Spotify while a browser video played.
@pytest.fixture
def two_players(monkeypatch):
    from eli.execution.effectors import media
    status = {"spotify": "Paused", "firefox.instance_1_9": "Playing"}
    sent = []

    def run(argv, timeout=8, input_bytes=None):
        if argv[:2] == ["playerctl", "--list-all"]:
            return True, "\n".join(status), ""
        if len(argv) >= 4 and argv[1] == "-p":
            if argv[3] == "status":
                return True, status.get(argv[2], ""), ""
            if argv[3] in ("pause", "play", "stop", "next", "previous"):
                sent.append((argv[3], argv[2]))
                return True, "", ""
        return True, "", ""

    import subprocess

    class _Listed:
        returncode, stdout, stderr = 0, "\n".join(status), ""

    # playerctl is there, as far as anything in ELI can tell
    import shutil
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(media.subprocess, "run", lambda argv, **kw: _Listed() if "--list-all" in argv
                        else subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(mp, "_has", lambda tool: True)
    monkeypatch.setattr(mp, "_run", run)
    monkeypatch.setattr(mp, "_metadata", lambda p, key: "")
    monkeypatch.setattr(mp, "_switched", lambda p, before: True, raising=False)
    monkeypatch.setattr(media, "_MEDIA_STATE", {"source": None, "title": None, "mpv_sock": None})
    return status, sent


def test_a_bare_pause_goes_to_the_player_that_is_playing(two_players):
    from eli.execution.effectors import media
    _, sent = two_players
    out = media.pause_media()
    assert out["ok"] is True
    assert sent == [("pause", "firefox.instance_1_9")]


def test_a_bare_play_goes_to_the_paused_player(two_players):
    from eli.execution.effectors import media
    status, sent = two_players
    status["firefox.instance_1_9"] = "Stopped"
    media.play_media()
    assert sent == [("play", "spotify")]


def test_a_pause_with_nothing_running_says_why_in_the_record(two_players):
    """"Failed to pause spotify: No players found" was put down as "an error inside ELI"."""
    from eli.execution.effectors import media
    status, sent = two_players
    status.clear()
    out = media.pause_media("spotify")
    assert out["ok"] is False and out["cause"] == "missing_resource"
