"""YouTube via mpv — visible video window by default."""
from __future__ import annotations

from eli.integrations.media.youtube_playback import build_mpv_youtube_argv


def test_mpv_youtube_visible_by_default():
    argv, _ = build_mpv_youtube_argv("never gonna give you up", ipc_path="/tmp/test.sock")
    joined = " ".join(argv)
    assert "--no-video" not in argv
    assert "--force-window=immediate" in argv
    assert "bestvideo+bestaudio/best" in joined


def test_mpv_youtube_headless_when_requested():
    argv, _ = build_mpv_youtube_argv(
        "never gonna give you up",
        ipc_path="/tmp/test.sock",
        headless=True,
    )
    joined = " ".join(argv)
    assert "--no-video" in argv
    assert "--force-window=immediate" not in argv
    assert "bestaudio/best" in joined


class _ResultsPage:
    """A results page holding one video, as urlopen hands it back."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return b'<html>... "videoId":"abcdefghijk" ...</html>'


def test_the_results_page_is_read_only_with_the_net_toggle_on(monkeypatch):
    """The mpv attempt's Mix and the browser fallback both read YouTube's results page. The reader
    used urlopen itself, so with ELI offline it still went out; it goes through the Net toggle now."""
    import urllib.request
    from eli.core import netguard
    from eli.integrations.media import youtube_playback as yp
    asked = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, *a, **k: asked.append(getattr(req, "full_url", req)) or _ResultsPage())
    monkeypatch.setattr(netguard, "should_block_network", lambda: True)
    assert yp.yt_resolve_watch_url("a quiet song") is None
    build_mpv_youtube_argv("a quiet song", ipc_path="/tmp/test.sock")
    assert asked == [], "nothing is fetched while the network is off"
    monkeypatch.setattr(netguard, "should_block_network", lambda: False)
    assert yp.yt_resolve_watch_url("a quiet song") == "https://www.youtube.com/watch?v=abcdefghijk"
    assert len(asked) == 1 and "search_query=a+quiet+song" in asked[0]


def test_yt_dlp_and_mpv_are_not_started_with_the_net_toggle_off(monkeypatch):
    """Both fetch from YouTube themselves, past ELI's own guard: offline, a play still searched and
    streamed through them."""
    import subprocess
    from eli.core import netguard
    from eli.integrations.media import media_deps, youtube_playback as yp
    started = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(media_deps, "yt_dlp_argv", lambda: ["yt-dlp"])
    monkeypatch.setattr(media_deps, "missing_youtube_tools", lambda: [])
    monkeypatch.setattr(netguard, "should_block_network", lambda: True)
    assert yp.yt_resolve_watch_url_ytdlp("a quiet song") is None
    out = yp.attempt_youtube_mpv("a quiet song", ipc_path="/tmp/test.sock")
    assert out["played"] is False and out["reason"] == "offline" and "offline" in out["error"]
    assert started == []
