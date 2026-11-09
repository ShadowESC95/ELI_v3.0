"""Media effector — Spotify / YouTube-mpv / MPRIS transport + audio ducking.

The first executor family lifted onto the effector layer. Deterministic control:
the executor result is authoritative (Phase 45). Extracted verbatim from
executor_enhanced.py; primitives come from eli.execution.effectors._base.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, Optional
from eli.utils.log import get_logger
log = get_logger(__name__)

from eli.execution.effectors._base import _allow_or_block, _open_in_browser, _run_argv
from eli.execution.effectors.system_helpers import volume_fallback as _volume_fallback
import re  # module-level: _PLAYLIST_RE is compiled at import time


def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)

# ── media state ──
MEDIA_DUCK = str(os.environ.get("ELI_MEDIA_DUCK", "1")).strip().lower() not in ("0","false","no","off")
MEDIA_PLAYER = (os.environ.get("ELI_MEDIA_PLAYER", "spotify") or "spotify").strip()
_BROWSER_PLAYERS = {"firefox", "Firefox", "chromium", "Chromium", "chrome"}
_DUCKED_MEDIA = False
_DUCK_LOCK = threading.Lock()
_DUCK_TIMER = None
_MEDIA_STATE: Dict[str, Any] = {
    "source": None, "title": None, "mpv_sock": None, "mpv_headless": False,
}
_TARGET_ALIASES: dict = {
    # streaming services → browser player
    "netflix":    "browser",
    "youtube":    "browser",
    "prime":      "browser",
    "disney":     "browser",
    "disneyplus": "browser",
    "hulu":       "browser",
    "twitch":     "browser",
    "brow":       "browser",
    "browser":    "browser",
    # explicit players
    "spotify":    "spotify",
    "vlc":        "vlc",
    "mpv":        "mpv",
}


def _get_active_player(command: Optional[str] = None) -> Optional[str]:
    """The player a command with no named player goes to: the one that is playing (or, for "play",
    the one that is paused), as playerctl_backend ranks them by their live status. A fixed list put
    Spotify first whenever it was open, so "pause" paused an already-paused Spotify while a browser
    video played, and went to a Spotify that had just been closed."""
    from eli.integrations.mpris import playerctl_backend as pb
    try:
        return pb.get_active_player(command=command)
    except Exception:
        log.debug("active player not read", exc_info=True)
        return None


def _playerctl(cmd: str, player: Optional[str] = None) -> Dict[str, Any]:
    """Run a media control command via the shared playerctl_backend."""
    from eli.integrations.mpris import playerctl_backend as pb

    dispatch = {
        "play": pb.play,
        "pause": pb.pause,
        "stop": pb.stop,
        "next": pb.next_track,
        "previous": pb.previous_track,
        "play-pause": pb.play_pause,
    }
    fn = dispatch.get(cmd)
    if not fn:
        return {
            "ok": False,
            "error": f"unsupported media command: {cmd}",
            "content": f"unsupported media command: {cmd}",
            "response": f"unsupported media command: {cmd}",
            "cause": "user_input",
        }
    return _with_cause(fn(player))


# Why a transport command failed, in the cause words action_outcomes keeps: the player named, or any
# player, not running is a missing resource ("Failed to pause spotify: No players found" was put down
# as "an error inside ELI"), a missing playerctl a missing dependency.
_NOT_RUNNING = re.compile(r"no (?:media )?players? (?:found|matching|is currently running)|"
                          r"no media player is currently running|not running|was not found", re.I)
_NOT_INSTALLED = re.compile(r"not installed|command not found|no such file", re.I)


def _with_cause(result: Any) -> Any:
    if not isinstance(result, dict) or result.get("ok") or result.get("cause"):
        return result
    said = " ".join(str(result.get(k) or "") for k in ("error", "response", "content"))
    if _NOT_INSTALLED.search(said):
        result["cause"] = "dependency"
    elif _NOT_RUNNING.search(said):
        result["cause"] = "missing_resource"
    return result


def _resolve_media_target(target: str | None, command: Optional[str] = None) -> str | None:
    """Map a user-supplied target string to an MPRIS player name or None."""
    if not target:
        return None
    alias = _TARGET_ALIASES.get(target.lower().strip())
    if alias == "browser":
        # The browser player that is playing (or paused, for "play"), not the first one listed.
        try:
            from eli.integrations.mpris import playerctl_backend as pb
            found = pb.resolve_player_target("browser", command=command)
            if found:
                return found
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return "firefox"   # fallback name
    return alias   # e.g. "spotify", "vlc"


def _targets_mpv(target: str | None) -> bool:
    """True when controls should go to the headless YouTube mpv: it's the active ELI
    source and alive, and the user didn't explicitly name a different player."""
    if target and "youtube" not in str(target).lower():
        return False
    return _MEDIA_STATE.get("source") == "mpv" and _mpv_alive()


def play_media(target: str | None = None) -> Dict[str, Any]:
    """Play/resume media."""
    if _targets_mpv(target):
        _mpv_ipc(["set_property", "pause", False])
        title = _MEDIA_STATE.get("title") or "YouTube"
        return {"ok": True, "action": "PLAY_MEDIA", "content": f"▶ Resumed — {title}",
                "response": f"▶ Resumed — {title}"}
    result = _playerctl("play", _resolve_media_target(target, "play"))
    result["action"] = "PLAY_MEDIA"
    return result


def _yt_resolve_watch_url(query: str) -> str | None:
    """Return the first YouTube watch URL for a search query.
    Uses a plain urllib scrape — no yt-dlp or API key required.
    Returns None if the network request fails or no video is found.
    """
    try:
        import urllib.request as _ureq
        import urllib.parse as _up2
        import re as _re2
        url = f"https://www.youtube.com/results?search_query={_up2.quote_plus(query)}"
        req = _ureq.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
        with _ureq.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = _re2.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', html)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return None


def _yt_autoplay_enabled() -> bool:
    """Whether direct YouTube playback should continue into related tracks.

    On by default -- a single track that stops dead is what was reported. The
    setting exists so someone who wants exactly one track can say so, and so
    the scrape can be skipped entirely when it is not wanted.
    """
    import os as _os
    env = (_os.environ.get("ELI_YT_AUTOPLAY") or "").strip().lower()
    if env in {"0", "false", "off", "no"}:
        return False
    if env in {"1", "true", "on", "yes"}:
        return True
    try:
        from eli.core.config import get as _cfg_get
        return bool(_cfg_get("youtube_autoplay", True))
    except Exception:
        return True


def _yt_mix_url(watch_url: str | None) -> str | None:
    """Turn a plain YouTube watch URL into a Mix/radio URL so playback AUTOPLAYS
    related songs continuously instead of stopping after one video. YouTube's
    `&list=RD<videoId>` is the auto-generated "Mix" (song radio) seeded from the
    video — the user's reported "YT doesn't autoplay the next songs" + "play
    something related" both come from this."""
    if not watch_url:
        return watch_url
    import re as _re3
    m = _re3.search(r"[?&]v=([A-Za-z0-9_-]{11})", watch_url)
    if m:
        vid = m.group(1)
        return f"https://www.youtube.com/watch?v={vid}&list=RD{vid}"
    return watch_url


from eli.integrations.media.youtube_playback import (
    yt_apply_browser_autoplay as _yt_apply_browser_autoplay,
    yt_resolve_watch_url_ytdlp as _yt_resolve_watch_url_ytdlp,
)


def _yt_browser_play_url(query: str) -> str:
    """Resolve a song query to a browser URL that autoplays and continues (Mix)."""
    import urllib.parse as _up2

    watch = _yt_resolve_watch_url(query) or _yt_resolve_watch_url_ytdlp(query)
    if watch and _yt_autoplay_enabled():
        watch = _yt_mix_url(watch)
    if watch:
        return _yt_apply_browser_autoplay(watch)
    return f"https://www.youtube.com/results?search_query={_up2.quote_plus(query)}"


def _spotify_open_uri(uri: str) -> bool:
    from eli.integrations.media.cross_platform import spotify_open_uri as _cp_open
    return _cp_open(uri)


def _spotify_search(query: str, prefer: str | None = None) -> bool:
    from eli.integrations.media.cross_platform import spotify_search as _cp_search
    return _cp_search(query, prefer=prefer)


_PLAYLIST_RE = re.compile(
    r"^\s*(?:my|the|a)?\s*(?P<name>.+?)\s*(?:play\s*list|playlist)\s*$", re.I
)


def _spotify_playlist_name(query: str) -> str:
    """The playlist name in "play my workout playlist", else "".

    A song request and a playlist request need different Spotify contexts: a
    track plays once and stops, a playlist keeps going. There was no way to
    ask for the second one at all.
    """
    m = _PLAYLIST_RE.match(str(query or ""))
    if not m:
        return ""
    name = (m.group("name") or "").strip(" .,:;-")
    # "play my playlist" names nothing; treat it as no playlist rather than
    # searching Spotify for the empty string.
    return name if len(name) >= 2 else ""


def _spotify_loop_status() -> str:
    from eli.integrations.media.cross_platform import spotify_loop_status as _cp_loop
    return _cp_loop()


def _spotify_clear_track_repeat() -> bool:
    from eli.integrations.media.cross_platform import spotify_clear_track_repeat as _cp_clear
    return _cp_clear()


def _spotify_play() -> bool:
    from eli.integrations.media.cross_platform import spotify_play as _cp_play
    return _cp_play()


def _spotify_is_playing() -> bool:
    from eli.integrations.media.cross_platform import spotify_is_playing as _cp_playing
    return _cp_playing()


def _spotify_wait_playing(timeout: float = 8.0, interval: float = 0.25) -> bool:
    from eli.integrations.media.cross_platform import spotify_wait_playing as _cp_wait
    return _cp_wait(timeout=timeout, interval=interval)


def _spotify_wait_running(timeout: float = 8.0) -> bool:
    from eli.integrations.media.cross_platform import spotify_wait_running as _cp_wait
    return _cp_wait(timeout=timeout)


def _spotify_running() -> bool:
    from eli.integrations.media.cross_platform import spotify_running as _cp_running
    return _cp_running()


def _spotify_resolve_playlist_uri(name: str) -> str | None:
    from eli.integrations.media.cross_platform import spotify_resolve_playlist_uri as _cp
    return _cp(name)


def _spotify_resolve_album_uri(name: str, artist: str | None = None) -> str | None:
    from eli.integrations.media.cross_platform import spotify_resolve_album_uri as _cp
    return _cp(name, artist)


def _spotify_resolve_artist_uri(name: str) -> str | None:
    from eli.integrations.media.cross_platform import spotify_resolve_artist_uri as _cp
    return _cp(name)




def _spotify_resolve_track_uri(query: str) -> str | None:
    from eli.integrations.media.cross_platform import spotify_resolve_track_uri as _cp
    return _cp(query)


def _spotify_open_liked_songs() -> bool:
    from eli.integrations.media.cross_platform import spotify_open_liked_songs as _cp
    return _cp()



def _spotify_try_open_and_play(uri: str, *, label: str, kind: str) -> Dict[str, Any] | None:
    """Open a concrete Spotify URI and start playback. None → try another path.

    Lower false-positive risk than the generic search-tab fallbacks below —
    opening a *resolved* spotify:<kind>:<id> URI genuinely queues that exact
    target, unlike a blind spotify:search: with nothing selected — but
    _spotify_play() is still only a transport-status check, so a before/after
    change is required for the same reason it is everywhere else in this
    file: a stale already-playing track reports identical "Playing" status.
    """
    import time as _time
    if not uri:
        return None
    if not _spotify_running():
        _ensure_spotify_running()
        _spotify_wait_running(timeout=8.0)
    _before = _spotify_live_meta()[1:]
    if not _spotify_open_uri(uri):
        return None
    _time.sleep(0.9)
    _spotify_clear_track_repeat()
    if not _spotify_play():
        return None
    _, artist, track = _spotify_live_meta()
    if (artist, track) == _before and (artist or track):
        return None
    meta = f"{artist} — {track}" if artist and track else (track or label)
    _set_now_playing("spotify", meta)
    msg = f"Playing {label} on Spotify."
    if track:
        msg = f"Playing {label} on Spotify — now: {meta}."
    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
            "kind": kind, "content": msg, "response": msg}




def _spotify_is_liked_songs_request(name: str) -> bool:
    """True when the user means Spotify's Liked Songs collection, not a search.

    Delegates to spotify_intent.is_liked_songs() — this file used to carry its
    own separate copy of the same regex, which silently went stale when the
    real one was broadened to also recognise "my liked playlist"/"liked
    playlist" phrasing (bare "liked", not just "liked song(s)"); a fix there
    had zero effect here until the duplicate was removed.
    """
    from eli.integrations.media.spotify_intent import is_liked_songs as _si_is_liked
    return _si_is_liked(name)


def _spotify_live_meta(player: str = "spotify") -> tuple[str, str, str]:
    from eli.integrations.media.cross_platform import spotify_live_meta as _cp_meta
    return _cp_meta(player)


def _now_playing_matches_query(query: str) -> bool:
    """spotify_is_playing() only confirms *something* is playing — it can't
    tell a resumed, stale track from the one just requested. Opening a
    spotify:search: URI then hitting MPRIS Play resumes whatever Spotify had
    queued if nothing in the new search got selected, which reports as a
    false "Playing X" success. Compare live playerctl metadata against the
    request before trusting it. Shared with spotify_search_type_and_play's
    own verification — see cross_platform.track_query_matches_now_playing."""
    from eli.integrations.media.cross_platform import track_query_matches_now_playing as _cp_match
    return _cp_match(query)


def _spotify_playback_confirmed(before: tuple[str, str], hint: str = "") -> bool:
    """Shared confirmation for the album/playlist fallback blocks, which had
    the same bare `if _spotify_play():` false-positive the track block had
    (fixed via _now_playing_matches_query) — status alone can't tell a
    resumed stale track from a real new one. An album/playlist name doesn't
    literally appear in a track's own artist/title metadata the way a track
    query does, so `hint` (the artist, when known) is checked when given, and
    a before/after change is always required as the baseline signal that
    something actually happened rather than the old track just continuing."""
    if not _spotify_play():
        return False
    _, artist, track = _spotify_live_meta("spotify")
    if not (artist or track):
        return False
    if (artist, track) == before:
        return False
    if hint and not _now_playing_matches_query(hint):
        return False
    return True


def _mpv_socket_path() -> str:
    from eli.integrations.media.cross_platform import mpv_socket_path as _cp_sock
    return _cp_sock()


def _yt_player_clients() -> list:
    """yt-dlp YouTube clients to try, in order.

    A googlevideo "HTTP error 403 Forbidden" during playback does not mean the
    video is unavailable — it means yt-dlp resolved a stream URL that is bound
    to the client which requested it, and ffmpeg (inside mpv) cannot reproduce
    that request. The live failure carried `c=ANDROID_VR`, whose URLs are tied
    to the requesting client and reliably 403 when handed to another fetcher.

    The empty string means "whatever yt-dlp picks by default" and it goes
    FIRST, deliberately. Measured against live YouTube on yt-dlp 2026.03.17,
    the default client resolved and fetched (HTTP 206) while web_safari, tv,
    ios, mweb and web_embedded did not resolve at all — pinning a "preferred"
    client would have broken working playback for everyone to chase an
    intermittent failure. So the default path is never altered; the ladder is
    only ever walked after a 403 has actually happened, and it lists the
    clients that were observed to resolve.

    Which clients work shifts as YouTube changes, so the order is overridable
    with ELI_YT_PLAYER_CLIENTS (comma-separated) without touching code. A
    stale yt-dlp is the other common cause and no client can compensate for
    it, which is why the failure message says so.
    """
    raw = (os.environ.get("ELI_YT_PLAYER_CLIENTS") or "").strip()
    if raw:
        return [("" if c.strip().lower() == "default" else c.strip())
                for c in raw.split(",") if c.strip()]
    return ["android", "tv", "ios", "mweb", ""]


def _yt_is_client_bound_failure(stderr_tail: str) -> bool:
    """True when the failure is the kind a different client can fix.

    A 403 is worth retrying with another client. "Video unavailable", a
    geo-block or an empty search result are not — retrying those just burns
    seconds before the browser fallback the user was always going to get.
    """
    low = str(stderr_tail or "").lower()
    if "empty playlist" in low or "unavailable" in low or "private video" in low:
        return False
    return "403" in low or "forbidden" in low


def _mpv_alive() -> bool:
    from eli.integrations.media.cross_platform import mpv_alive as _cp_alive
    p = _MEDIA_STATE.get("mpv_sock") or _mpv_socket_path()
    return _cp_alive(p)


def _mpv_ipc(command: list, *, want_response: bool = False):
    """Send one JSON IPC command to the headless YouTube mpv. Returns True/data, or
    False/None on failure. Never raises."""
    from eli.integrations.media.cross_platform import mpv_ipc_send
    p = _MEDIA_STATE.get("mpv_sock") or _mpv_socket_path()
    return mpv_ipc_send(command, sock_path=p, want_response=want_response)


def _mpv_numeric(val: Any) -> bool:
    """True for real mpv numbers — bool must not count (bool is a int subclass)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _mpv_load_confirmed(sock_path: str) -> bool:
    """True once mpv has actually opened a stream (not merely spawned)."""
    from eli.integrations.media.youtube_playback import mpv_load_confirmed
    return mpv_load_confirmed(sock_path)


def _prune_mpv_logs(keep: int = 5) -> None:
    """Keep only the newest `keep` mpv failure logs so /tmp cannot grow without bound."""
    import glob as _glob
    try:
        logs = sorted(_glob.glob(str(__import__("eli.core.paths", fromlist=["temp_path"]).temp_path("mpv_*.log"))),
                      key=os.path.getmtime, reverse=True)
        for stale in logs[keep:]:
            try:
                os.unlink(stale)
            except OSError:
                log.debug("suppressed exception", exc_info=True)
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def _mpv_quit() -> None:
    """Stop the headless YouTube mpv if it's running."""
    if _mpv_alive():
        _mpv_ipc(["quit"])


def _set_now_playing(
    source: str,
    title: str | None,
    *,
    mpv_sock: str | None = None,
    mpv_headless: bool = False,
) -> None:
    # Switching to a different source: stop the old mpv so two things don't
    # play at once (e.g. YouTube + Spotify).
    if _MEDIA_STATE.get("source") == "mpv" and source != "mpv":
        _mpv_quit()
    _MEDIA_STATE.update({
        "source": source,
        "title": title,
        "mpv_sock": mpv_sock,
        "mpv_headless": bool(mpv_headless),
    })


def _known_target(t: str) -> bool:
    """A player or service ELI can play on itself (streaming platforms are taken before this)."""
    t = str(t or "").strip().lower()
    return bool(t) and ("spotify" in t or "youtube" in t or t in ("yt",) or t in _TARGET_ALIASES)


def _open_named_site(target: str, asked: Dict[str, Any]) -> Dict[str, Any]:
    """"play X on <site>" where ELI has no player for the site: open the site itself, say what was and
    was not done, and play nothing anywhere else. A Spotify track or a YouTube video used to play
    instead, and the reply never mentioned the site."""
    name = str(target or "").strip()
    what = asked.get("seed") or asked.get("search") or ""
    host = name if re.match(r"^[\w-]+(?:\.[\w-]+)+$", name) else ""
    url, why = (f"https://{host}/" if host else ""), ""
    if not url:
        try:
            from eli.runtime import site_address
            found = site_address.named(name)
            url, why = str(found.get("url") or ""), str(found.get("why") or "")
        except Exception as failure:
            why = str(failure)
    if not url:
        msg = (f"I played nothing: I have no player for {name}, and no address for it"
               + (f" ({why})" if why else "") + ". Give me its address and I'll open it.")
        return {"ok": False, "action": "PLAY_MEDIA", "played": False, "target": name,
                "error": "unknown_site", "cause": "missing_resource", "content": msg, "response": msg}
    opened = _execute_impl("OPEN_URL", {"url": url})
    if not (isinstance(opened, dict) and opened.get("ok")):
        reason = str((opened or {}).get("error") or (opened or {}).get("response") or "the browser did not open")
        msg = f"I played nothing: I have no player for {name}, and opening {url} failed ({reason})."
        return {"ok": False, "action": "PLAY_MEDIA", "played": False, "target": name, "url": url,
                "error": "site_not_opened", "content": msg, "response": msg}
    msg = (f"Opened {url}. I have no player for {name} and don't know its search page, so I didn't search "
           f"for “{what}” there or play anything; it's open for you to pick.")
    return {"ok": True, "action": "PLAY_MEDIA", "played": False, "search_only": True, "target": name,
            "url": url, "content": msg, "response": msg}


def _service_of(target: Any) -> str:
    t = str(target or "").strip().lower()
    if not t:
        return ""
    if "spotify" in t:
        return "spotify"
    if "youtube" in t or t == "yt":
        return "youtube"
    try:
        from eli.execution.media_runtime import normalize_streaming_target
        return normalize_streaming_target(t) or t
    except Exception:
        return t


def _played_where(asked: Dict[str, Any]) -> tuple:
    """Where the person has had things like this played, from the record of what ran (the evidence
    ledger, read through action_outcomes): plays they sent somewhere themselves ("on youtube") that
    started. ({service: weight} for this same request, {service: weight} for its kind), each play
    weighted by how recent it is, as the record weighs every run. A bare title has no kind to learn
    from: one show title played on YouTube says nothing about the next bare title."""
    from eli.integrations.media.spotify_intent import MUSIC_KINDS, VIDEO_KINDS, media_request
    try:
        from eli.runtime import action_outcomes as _ao
        runs = _ao._runs()
    except Exception:
        log.debug("the record of plays is unreadable", exc_info=True)
        return {}, {}
    now = time.time()
    same: Dict[str, float] = {}
    alike: Dict[str, float] = {}
    for run in runs:
        if run.get("action") != "PLAY_MEDIA" or not run.get("worked"):
            continue
        args, result = run.get("args") or {}, run.get("result") or {}
        where = _service_of(args.get("target") or args.get("service"))
        if not where or result.get("played") is False:
            continue
        theirs = media_request(str(args.get("query") or ""), args.get("kind"))
        weight = _ao._weight(run.get("ts") or 0.0, now)
        if theirs["search"].strip().lower() == asked["search"].strip().lower():
            same[where] = same.get(where, 0.0) + weight
        kinds = (theirs["kind"], asked["kind"])
        if asked["kind"] != "title" and (kinds[0] == kinds[1] or all(k in MUSIC_KINDS for k in kinds)
                                         or all(k in VIDEO_KINDS for k in kinds)):
            alike[where] = alike.get(where, 0.0) + weight
    return same, alike


def _where_to_play(asked: Dict[str, Any]) -> Optional[str]:
    """The service for a request that names none, or None to ask. From what this person has done
    before (the same request, then the same kind of thing), then from what is asked: music goes to
    Spotify when it is running, else YouTube; a show or a film never goes to Spotify. A bare title
    could be a song or a show, so with Spotify running it is asked about rather than sent to whichever
    player was first in a list."""
    from eli.integrations.media.spotify_intent import MUSIC_KINDS, VIDEO_KINDS
    for pool in _played_where(asked):
        if pool:
            best = max(pool, key=pool.get)
            if sum(1 for v in pool.values() if v == pool[best]) == 1:
                return best
    spotify_up = _spotify_running()
    if asked["kind"] in VIDEO_KINDS:
        return None
    if asked["kind"] in MUSIC_KINDS:
        return "spotify" if spotify_up else "youtube"
    return None if spotify_up else "youtube"


def _ask_where(query: str, asked: Dict[str, Any]) -> Dict[str, Any]:
    from eli.integrations.media.spotify_intent import VIDEO_KINDS
    what = asked.get("search") or query
    places = (["YouTube"] if asked["kind"] in VIDEO_KINDS or not _spotify_running() else ["Spotify", "YouTube"])
    msg = (f"Where should I play “{what}”: " + " or ".join(places)
           + (", or another site?" if asked["kind"] in VIDEO_KINDS else "?"))
    return {"ok": True, "action": "PLAY_MEDIA", "played": False, "asks_user": True, "kind": asked["kind"],
            "choices": [{"n": i, "text": place, "command": f"play {what} on {place.lower()}"}
                        for i, place in enumerate(places, 1)],
            "awaiting": {"command": f"play {what} on {{answer}}", "action": "PLAY_MEDIA", "needs": "service"},
            "content": msg, "response": msg}


def _play_on_spotify(asked: Dict[str, Any]) -> Dict[str, Any]:
    """Spotify, by what kind of thing is asked for (spotify_intent.media_request)."""
    import time as _time
    kind = asked["kind"]

    # ── Liked Songs ──
    if kind == "liked":
        try:
            _before = _spotify_live_meta("spotify")[1:]
            if _spotify_open_liked_songs():
                _time.sleep(0.9)
                _spotify_clear_track_repeat()
                if _spotify_playback_confirmed(_before):
                    _set_now_playing("spotify", "Liked Songs")
                    msg = "Playing your Liked Songs on Spotify."
                    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                            "kind": "liked_songs", "content": msg, "response": msg}
                msg = ("I opened your Liked Songs on Spotify but couldn't confirm "
                       "it started playing — press play in Spotify.")
                return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                        "search_only": True, "kind": "liked_songs",
                        "content": msg, "response": msg}
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    # ── Named playlist, or a style or mood: a playlist keeps going, a track stops ──
    if kind in ("playlist", "genre"):
        name = asked["title"] if kind == "playlist" else asked["search"]
        if kind == "playlist":
            _hit = _spotify_try_open_and_play(
                _spotify_resolve_playlist_uri(name), label=f"the “{name}” playlist", kind="playlist")
            if _hit:
                return _hit
        return _spotify_playlist_search(name, kind)

    # ── Album (optionally by artist): judged by the playing track's album ──
    if kind == "album":
        from eli.integrations.media.cross_platform import spotify_shuffle_held_off
        with spotify_shuffle_held_off():
            return _spotify_album(asked)

    # ── Artist ──
    if kind == "artist":
        _hit = _spotify_try_open_and_play(
            _spotify_resolve_artist_uri(asked["artist"]), label=f"songs by {asked['artist']}", kind="artist")
        if _hit:
            return _hit
        return _spotify_play_track(asked["search"])

    # ── Songs like a song: its own track starts it, Spotify's autoplay picks what follows ──
    if kind == "similar":
        out = _spotify_play_track(asked["seed"])
        if out.get("played"):
            now = str(_MEDIA_STATE.get("title") or asked["seed"])
            msg = (f"Started “{now}” on Spotify for songs like it; Spotify itself picks what follows "
                   "(its Autoplay setting), as I can't ask it for similar songs directly.")
            out = {**out, "kind": "similar", "seed": asked["seed"], "content": msg, "response": msg}
        return out

    return _spotify_play_track(asked["search"])


def _spotify_playlist_search(name: str, kind: str) -> Dict[str, Any]:
    import time as _time
    _pl_opened = _spotify_search(name, prefer="playlists")
    if not _pl_opened:
        try:
            _ensure_spotify_running()
            _spotify_open_uri(f"spotify:search:{name}/playlists")
            for _ in range(8):
                _time.sleep(1.0)
                if _spotify_running():
                    break
            _pl_opened = _spotify_search(name, prefer="playlists")
        except Exception:
            _pl_opened = False
    label = f"the “{name}” playlist" if kind == "playlist" else f"a “{name}” playlist"
    if _pl_opened:
        _before = _spotify_live_meta("spotify")[1:]
        _time.sleep(1.8)
        _spotify_clear_track_repeat()
        if _spotify_playback_confirmed(_before):
            _, artist, track = _spotify_live_meta("spotify")
            _set_now_playing("spotify", f"{name} (playlist)")
            msg = f"Playing {label} on Spotify" + (f" — now: {artist} — {track}." if track else ".")
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "kind": kind, "content": msg, "response": msg}
        msg = (f"I opened Spotify's playlist results for “{name}” but "
               f"playback didn't start — pick a playlist in Spotify, or "
               f"try a more specific name.")
        return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                "search_only": True, "kind": kind,
                "content": msg, "response": msg}
    msg = (f"I couldn't reach Spotify to play {label} — "
           f"is it installed and running?")
    return {"ok": False, "action": "PLAY_MEDIA", "played": False,
            "search_only": True, "target": "spotify", "kind": kind,
            "content": msg, "response": msg}


def _spotify_album(asked: Dict[str, Any]) -> Dict[str, Any]:
    import time as _time
    from eli.integrations.media.cross_platform import album_matches, spotify_live_track
    _album, _album_artist = asked["title"], asked["artist"]
    label = f"the “{_album}” album" + (f" by {_album_artist}" if _album_artist else "")

    def _it_is() -> bool:
        return _spotify_play() and album_matches(_album, _album_artist, spotify_live_track())

    _hit = _spotify_try_open_and_play(
        _spotify_resolve_album_uri(_album, _album_artist or None), label=label, kind="album")
    if _hit and album_matches(_album, _album_artist, spotify_live_track()):
        return _hit
    if not _spotify_running():
        _ensure_spotify_running()
        _spotify_wait_running(timeout=8.0)
    if _spotify_search(asked["search"], prefer="albums"):
        _time.sleep(2.4)
        _spotify_clear_track_repeat()
        if _it_is():
            _set_now_playing("spotify", f"{_album} (album)")
            msg = f"Playing {label} on Spotify."
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "kind": "album", "content": msg, "response": msg}
        # Retry play once after album search UI settles
        _time.sleep(1.2)
        if _it_is():
            _set_now_playing("spotify", f"{_album} (album)")
            msg = f"Playing {label} on Spotify."
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "kind": "album", "content": msg, "response": msg}
    now = spotify_live_track()
    playing = (f" Spotify is playing “{now['artist']} — {now['title']}”"
               + (f" from {now['album']}" if now["album"] else "") + ", which is not it."
               if now["status"] == "playing" and now["title"] else "")
    msg = f"I couldn't start {label} on Spotify: no track of it began playing.{playing}"
    return {"ok": False, "action": "PLAY_MEDIA", "played": False, "search_only": True,
            "target": "spotify", "kind": "album", "content": msg, "response": msg}


def _spotify_play_track(track_q: str) -> Dict[str, Any]:
    """One track (or the top track for these words) on Spotify, accepted only when what starts is
    what was asked for. Shuffle is held off meanwhile: with it on, Spotify started some other track
    of the results, which was rightly refused, and the request failed after half a minute."""
    from eli.integrations.media.cross_platform import spotify_shuffle_held_off
    with spotify_shuffle_held_off():
        return _spotify_play_track_now(track_q)


def _spotify_play_track_now(track_q: str) -> Dict[str, Any]:
    import time as _time
    _track_q = track_q
    _launch_attempted = False
    _spotify_up = True
    # Linux: ask Spotify over MPRIS to play its top search result. No focus or keystrokes, so
    # it is the path that works on Wayland; it is only accepted when the song that starts is
    # the one asked for.
    try:
        from eli.integrations.media.cross_platform import (
            spotify_launch_if_needed as _sp_launch, spotify_play_top_search_result as _sp_top)
        _launch_attempted = True
        _spotify_up = bool(_sp_launch())  # once for every path below
        if _spotify_up and _sp_top(_track_q, launch=False):
            _, _np_artist, _np_title = _spotify_live_meta()
            _np = f"{_np_artist} — {_np_title}" if _np_artist and _np_title else _track_q
            _set_now_playing("spotify", _np)
            msg = f"Playing “{_np}” on Spotify."
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "content": msg, "response": msg}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    # Then type into Spotify's own search box (X11, or an XWayland window) — open.spotify.com's
    # search page is a JS shell with no server-rendered track data, so it can't be scraped.
    try:
        from eli.integrations.media.cross_platform import spotify_search_type_and_play as _sp_type_play
        if _spotify_up and _sp_type_play(_track_q, launch=not _launch_attempted):
            _set_now_playing("spotify", _track_q)
            msg = f"Playing “{_track_q}” on Spotify."
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "content": msg, "response": msg}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    # Type-and-play above already launched and waited 8s. If Spotify still
    # isn't up, a second launch + 8s wait just delays the "couldn't reach it"
    # answer — it was only here because the type-and-play path was put in
    # front of this older fallback without telling it a launch had happened.
    if not _spotify_running() and not _launch_attempted:
        _ensure_spotify_running()
        _spotify_wait_running(timeout=8.0)
    _opened = _spotify_search(_track_q, prefer="tracks")
    if not _opened:
        try:
            _spotify_open_uri(f"spotify:search:{_track_q}")
            for _ in range(8):
                _time.sleep(1.0)
                if _spotify_running():
                    break
            _opened = _spotify_search(_track_q, prefer="tracks")
        except Exception:
            _opened = False
    if _opened:
        _time.sleep(1.6)
        _spotify_clear_track_repeat()
        if _spotify_play() and _now_playing_matches_query(_track_q):
            _set_now_playing("spotify", _track_q)
            msg = f"Playing “{_track_q}” on Spotify."
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "content": msg, "response": msg}
        # The same request played a moment later, live, so it gets a second go before being
        # called a failure.
        try:
            from eli.integrations.media.cross_platform import spotify_play_top_search_result as _sp_again
            if _sp_again(_track_q, launch=False):
                _, _np_artist, _np_title = _spotify_live_meta()
                _np = f"{_np_artist} — {_np_title}" if _np_artist and _np_title else _track_q
                _set_now_playing("spotify", _np)
                msg = f"Playing “{_np}” on Spotify."
                return {"ok": True, "action": "PLAY_MEDIA", "played": True, "content": msg, "response": msg}
        except (ImportError, OSError, subprocess.SubprocessError):
            log.debug("second Spotify try failed", exc_info=True)
        _np_head, _np_artist, _np_title = _spotify_live_meta()
        if _np_head.startswith("▶") and (_np_artist or _np_title):
            # Whatever is playing, it is not what was asked for: Spotify's top result for a
            # different song, or an earlier track that carried on. Say which, claim nothing.
            msg = (f"Spotify is playing “{_np_artist} — {_np_title}”, not “{_track_q}”. It didn't "
                   f"find that after two tries; say “play {_track_q} on youtube” for YouTube.")
            return {"ok": False, "action": "PLAY_MEDIA", "played": False, "mismatch": True,
                    "search_only": True, "target": "spotify", "content": msg, "response": msg}
        msg = (f"Spotify didn't start anything for “{_track_q}”: it showed its search results "
               f"but no song began playing, and its media controls didn't respond to play.")
        return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                "search_only": True, "target": "spotify",
                "content": msg, "response": msg}
    msg = (f"I couldn't reach Spotify to play “{_track_q}” — is it installed and "
           f"running? Open it and try again, or say “play {_track_q} on youtube”.")
    return {"ok": False, "action": "PLAY_MEDIA", "played": False,
            "search_only": True, "target": "spotify",
            "content": msg, "response": msg}

def play_specific(query: str, target: str | None = None, *, browser: bool = False,
                  kind: str | None = None) -> Dict[str, Any]:
    """Play a specific song/artist/genre.

    Dispatch priority:
      0. Explicit streaming platform (netflix/prime/disney/…) → native app (browser only if browser=True)
      1. Explicit spotify target  → Spotify native app (dbus/URI, never web)
      2. Explicit youtube target  → yt-dlp+mpv (visible video window by default)
      3. "youtube web/website/.com"    → browser watch URL (never mpv)
      4. "X by Y" (no target)     → yt-dlp+mpv
      5. Generic fallback         → browser watch URL
    """
    import urllib.parse
    import re as _re

    t = (target or "").lower().strip()

    # ── 0. Named streaming platform — never fall through to YouTube ───────────
    try:
        from eli.execution.media_runtime import (
            _play_on_streaming,
            normalize_streaming_target,
            _streaming_display_name,
        )
        canon = normalize_streaming_target(target or t)
        if canon:
            msg = _play_on_streaming(target or t, query, browser=browser)
            if msg:
                return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                        "search_only": True, "target": canon,
                        "content": msg, "response": msg}
            label = _streaming_display_name(canon)
            fail = f"Couldn't open {label}."
            return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                    "target": canon, "content": fail, "response": fail}
    except Exception:
        log.debug("[MEDIA] streaming platform dispatch failed", exc_info=True)

    from eli.integrations.media import spotify_intent as _si

    # What is asked for, read once: its kind and its parts (spotify_intent.media_request).
    asked = _si.media_request(query, kind)

    # ── A place ELI has no player for: go there, and play nothing anywhere else ──
    if t and not _known_target(t):
        return _open_named_site(target, asked)

    if not t:
        where = _where_to_play(asked)
        if where is None:
            return _ask_where(query, asked)
        if where not in ("spotify", "youtube"):
            return play_specific(query, where, browser=browser, kind=kind)
        t = where
    # What YouTube is asked for: the parts, said the way its search helpers read them.
    if asked["kind"] == "similar":
        query = asked["seed"]
    elif asked["kind"] == "album":
        query = f"the album {asked['title']}" + (f" by {asked['artist']}" if asked["artist"] else "")
    elif asked["kind"] in ("track", "artist", "genre"):
        query = asked["search"]
    else:
        query = asked["title"] or asked["search"]
    _by_m = _re.match(r"^(.+?)\s+by\s+(.+)$", f"{asked['title']} by {asked['artist']}", _re.I) \
        if asked["kind"] == "track" and asked["artist"] else None

    # ── 1. Spotify ──────────────────────────────────────────────────────────────
    is_spotify = "spotify" in t
    is_youtube = "youtube" in t or t in ("yt",)
    is_yt_web  = is_youtube and bool(_re.search(r"\bweb(?:site)?\b", t))
    is_yt_browser = (
        is_yt_web
        or bool(_re.search(r"youtube\s*(?:\.com|\s+com)\b", t, _re.I))
    )

    if is_spotify and not is_youtube:
        return _play_on_spotify(asked)

    # ── 2. "youtube web/website/.com" → browser only (never mpv) ─────────────
    if is_yt_browser:
        from eli.integrations.media.spotify_intent import youtube_search_query as _yt_q
        yt_q = _yt_q(query)
        url = _yt_browser_play_url(yt_q)
        if not _open_in_browser(url):
            msg = (
                "Couldn't open YouTube in the browser — Firefox may be hung. "
                "Close Firefox completely (or set ELI_BROWSER=chromium) and try again."
            )
            return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                    "target": "youtube", "content": msg, "response": msg}
        msg = f"Opening YouTube in browser: {yt_q} (autoplay mix)"
        return {"ok": True, "action": "PLAY_MEDIA", "content": msg, "response": msg}

    # ── 3. YouTube target (plain) or "X by Y" → yt-dlp+mpv ──────────────────
    from eli.integrations.media.spotify_intent import (
        youtube_search_query as _yt_q,
        youtube_mpv_query as _yt_mpv_q,
    )
    _by_pair = (_by_m.group(1).strip(), _by_m.group(2).strip()) if _by_m else None
    yt_search = _yt_mpv_q(query, by_artist=_by_pair)

    _yt_direct_err = ""
    _yt_have_tools = False
    _missing_yt: list[str] = []
    try:
        from eli.integrations.media.media_deps import youtube_mpv_ready, missing_youtube_tools
        _yt_have_tools = youtube_mpv_ready()
        _missing_yt = missing_youtube_tools()
    except Exception:
        _missing_yt = ["mpv", "yt-dlp"]
    if _yt_have_tools:
        try:
            from eli.integrations.media.youtube_playback import (
                attempt_youtube_mpv,
                yt_mpv_headless,
            )
            ipc = _mpv_socket_path()
            _mpv_quit()
            _headless = yt_mpv_headless()
            _yt_result = attempt_youtube_mpv(
                yt_search, ipc_path=ipc, headless=_headless,
            )
            _fallback_what = (f"'{_by_m.group(1).strip()}' by {_by_m.group(2).strip()}"
                              if _by_m else f"'{query}'")
            if _yt_result.get("played"):
                _title = (f"{_by_m.group(1).strip()} by {_by_m.group(2).strip()}"
                          if _by_m else query)
                _set_now_playing(
                    "mpv", _title, mpv_sock=ipc,
                    mpv_headless=_yt_result.get("headless", _headless),
                )
                _resolved = _mpv_ipc(["get_property", "media-title"], want_response=True)
                _what = (_resolved.strip()
                         if isinstance(_resolved, str) and _resolved.strip()
                         else _fallback_what)
                if _yt_result.get("headless", _headless):
                    msg = f"Playing {_what} on YouTube (audio, in the background)."
                else:
                    msg = f"Playing {_what} on YouTube in mpv — video on screen."
                return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                        "content": msg, "response": msg}
            if _yt_result.get("pending"):
                _title = (f"{_by_m.group(1).strip()} by {_by_m.group(2).strip()}"
                          if _by_m else query)
                _set_now_playing(
                    "mpv", _title, mpv_sock=ipc,
                    mpv_headless=_yt_result.get("headless", _headless),
                )
                msg = (f"Starting {_fallback_what} on YouTube in mpv — it is still resolving, so "
                       f"I have not confirmed playback yet. Ask what's playing in a moment.")
                return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                        "pending": True, "content": msg, "response": msg}
            _yt_direct_err = str(_yt_result.get("error") or "mpv could not start playback")
            if _yt_result.get("reason") == "missing_tools":
                _missing_yt = list(_yt_result.get("missing_tools") or _missing_yt)
        except Exception:
            log.warning("[MEDIA] YouTube mpv path raised unexpectedly", exc_info=True)
            _yt_direct_err = "internal error during YouTube playback"
    elif _missing_yt and is_youtube:
        names = " and ".join(_missing_yt)
        msg = (
            f"I can't play that on YouTube yet — {names} "
            f"{'is' if len(_missing_yt) == 1 else 'are'} not available on this machine."
        )
        return {
            "ok": False,
            "action": "PLAY_MEDIA",
            "played": False,
            "target": "youtube",
            "reason": "missing_tools",
            "missing_tools": _missing_yt,
            "content": msg,
            "response": msg,
        }
    # Direct in-app playback needs yt-dlp and mpv. Be honest that this is a fallback, not playback
    # (no-fake-actions), and say what would unlock "play" for this user; an install hint is wrong
    # when the tools are present and mpv died, so report the real reason.
    _play_hint = (
        f" — direct playback failed ({_yt_direct_err})" if _yt_direct_err
        else "" if _yt_have_tools
        else (" — say yes if I offer to install mpv or yt-dlp")
    )
    watch = _yt_browser_play_url(yt_search)
    if "watch?v=" in watch:
        if not _open_in_browser(watch):
            msg = (
                "Couldn't open YouTube in the browser — Firefox may be hung. "
                "Close Firefox or set ELI_BROWSER=chromium, then try again."
            )
            return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                    "target": "youtube", "content": msg, "response": msg}
        _what = (f"'{_by_m.group(1).strip()}' by {_by_m.group(2).strip()}"
                 if _by_m else f"'{query}'")
        msg = f"Opening {_what} in the browser{_play_hint}."
        return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                "search_only": True, "content": msg, "response": msg}

    # ── 5. Last resort: YouTube search page ──────────────────────────────────
    encoded = urllib.parse.quote_plus(query)
    if not _open_in_browser(f"https://www.youtube.com/results?search_query={encoded}"):
        msg = (
            "Couldn't open YouTube in the browser — Firefox may be hung. "
            "Close Firefox or set ELI_BROWSER=chromium, then try again."
        )
        return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                "target": "youtube", "content": msg, "response": msg}
    msg = f"Opening a YouTube search for '{query}' in the browser{_play_hint}."
    return {"ok": True, "action": "PLAY_MEDIA", "played": False,
            "search_only": True, "content": msg, "response": msg}


def _playerctl_run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def _playerctl_p(*args):
    if MEDIA_PLAYER:
        return _playerctl_run(["playerctl", "-p", MEDIA_PLAYER, *args])
    return _playerctl_run(["playerctl", *args])


def _duck_media_pause():
    # Canonical implementation lives in audio_stt (shared with wake-word ducking).
    try:
        from eli.perception.audio_stt import media_player_duck_pause
        media_player_duck_pause()
    except Exception:
        log.debug("[MEDIA_DUCK] pause skipped", exc_info=True)


def _duck_media_resume():
    try:
        from eli.perception.audio_stt import media_player_duck_resume
        media_player_duck_resume()
    except Exception:
        log.debug("[MEDIA_DUCK] resume skipped", exc_info=True)


def _duck_schedule_resume(deadline_s: float):
    # Reset timer each time wake window is extended.
    global _DUCK_TIMER
    if not MEDIA_DUCK:
        return
    with _DUCK_LOCK:
        if _DUCK_TIMER is not None:
            try:
                _DUCK_TIMER.cancel()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            _DUCK_TIMER = None
        delay = max(0.0, float(deadline_s) - time.time())
        t = threading.Timer(delay, _duck_media_resume)
        t.daemon = True
        _DUCK_TIMER = t
        t.start()


def _spotify_player_name():
    # Pick a player name that actually exists (flatpak spotify usually exposes "spotify")
    r = _run_argv(["playerctl", "-l"], timeout=30)
    if r.get("ok") and r.get("stdout"):
        players = [p.strip() for p in r["stdout"].splitlines() if p.strip()]
        for p in players:
            if "spotify" in p.lower():
                return p
    return "spotify"


def _spotify_launch():
    """
    Launch Spotify and VERIFY it actually stays running.
    """
    SPOTIFY_CMD = os.environ.get("ELI_SPOTIFY_CMD", "").strip() or "spotify"
    argv = shlex.split(SPOTIFY_CMD)
    if not argv:
        return {"ok": False, "error": "ELI_SPOTIFY_CMD is empty"}

    if not _allow_or_block(argv[0]):
        return {"ok": False, "error": f"command not allowed: {argv[0]} (ELI_ALLOWED_CMDS)"}

    # If using Flatpak, verify the app exists/installed first.
    appid = None
    if len(argv) >= 3 and argv[0] == "flatpak" and argv[1] == "run":
        appid = argv[2]
        chk = subprocess.run(
            ["flatpak", "info", appid],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if chk.returncode != 0:
            return {
                "ok": False,
                "error": f"Flatpak app not installed or not available: {appid}",
                "stderr": (chk.stderr or "")[-2000:],
            }

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "cmd": argv}

    # Give it a moment, then verify it's actually running.
    time.sleep(1.8)

    running = False
    evidence = {}

    # Prefer flatpak ps if we're launching flatpak
    if appid:
        ps = subprocess.run(
            ["flatpak", "ps"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        evidence["flatpak_ps_rc"] = ps.returncode
        evidence["flatpak_ps_tail"] = (ps.stdout or "")[-4000:]
        if ps.returncode == 0 and appid in (ps.stdout or ""):
            running = True
    else:
        # Fallback: check playerctl list (best-effort)
        r = _run_argv(["playerctl", "-l"], timeout=30)
        evidence["playerctl"] = r
        if r.get("ok") and "spotify" in (r.get("stdout","").lower()):
            running = True

    return {
        "ok": bool(running),
        "launched": True,
        "running": bool(running),
        "pid": getattr(proc, "pid", None),
        "cmd": argv,
        "evidence": evidence,
    }


def _spotify_ctl(command: str, auto_launch: bool = True):
    """
    command: play|pause|stop|status|next|previous|play-pause
    """
    player = _spotify_player_name()
    r = _run_argv(["playerctl", "-p", player, command], timeout=30)

    # If play fails because spotify isn't running/registered, optionally launch and retry
    if (not r.get("ok")) and auto_launch and command in {"play", "play-pause"}:
        launch = _spotify_launch()
        time.sleep(2)
        player = _spotify_player_name()
        r2 = _run_argv(["playerctl", "-p", player, command], timeout=30)
        return {"ok": r2.get("ok", False), "launch": launch, "result": r2, "player": player}

    return {"ok": r.get("ok", False), "result": r, "player": player}


# ── public transport API ──
def stop_media(target: str | None = None) -> Dict[str, Any]:
    """Stop media. Spotify doesn't support MPRIS Stop so uses pause."""
    if _targets_mpv(target):
        _mpv_quit()
        _MEDIA_STATE.update({"source": None, "title": None, "mpv_sock": None})
        return {"ok": True, "action": "STOP_MEDIA", "content": "⏹ Stopped — YouTube",
                "response": "⏹ Stopped — YouTube"}
    p = _resolve_media_target(target, "stop") or _get_active_player("stop")
    # Spotify ignores Stop — use pause instead
    cmd = "pause" if (p and "spotify" in p.lower()) else "stop"
    result = _playerctl(cmd, p)
    result["action"] = "STOP_MEDIA"
    return result


def pause_media(target: str | None = None) -> Dict[str, Any]:
    """Pause currently playing media."""
    if _targets_mpv(target):
        _mpv_ipc(["set_property", "pause", True])
        return {"ok": True, "action": "PAUSE_MEDIA", "content": "⏸ Paused — YouTube",
                "response": "⏸ Paused — YouTube"}
    # No player named: the media backend picks the one that is playing.
    result = _playerctl("pause", _resolve_media_target(target, "pause"))
    result["action"] = "PAUSE_MEDIA"
    return result


def next_media(target: str | None = None) -> Dict[str, Any]:
    """Skip to next track."""
    if _targets_mpv(target):
        _mpv_ipc(["playlist-next", "weak"])
        return {"ok": True, "action": "NEXT_MEDIA", "content": "⏭ Next — YouTube",
                "response": "⏭ Next — YouTube"}
    result = _playerctl("next", _resolve_media_target(target, "next"))
    result["action"] = "NEXT_MEDIA"
    return result


def previous_media(target: str | None = None) -> Dict[str, Any]:
    """Go to previous track.

    Spotify's MPRIS implementation of 'Previous' behaves like a media key:
    if playback position > 3s it seeks to 0 (restart current track) instead
    of going to the previous track. We force position=0 before sending
    Previous so the command always skips back regardless of current position.
    """
    if _targets_mpv(target):
        _mpv_ipc(["playlist-prev", "weak"])
        return {"ok": True, "action": "PREVIOUS_MEDIA", "content": "⏮ Previous — YouTube",
                "response": "⏮ Previous — YouTube"}
    p = _resolve_media_target(target, "previous") or _get_active_player("previous")
    if p and "spotify" in p.lower():
        try:
            subprocess.run(
                ["playerctl", "-p", p, "position", "0"],
                capture_output=True, timeout=2,
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    result = _playerctl("previous", p)
    result["action"] = "PREVIOUS_MEDIA"
    return result


def shuffle_media(target: str | None = None, state: Any = None) -> Dict[str, Any]:
    """Shuffle: set on or off when `state` names one ("shuffle on"), else toggled; the result says the
    state the player reports afterwards (cross_platform.spotify_shuffle)."""
    from eli.integrations.media.cross_platform import shuffle_wanted, spotify_shuffle
    p = _resolve_media_target(target, "shuffle") or _get_active_player("shuffle") or "spotify"
    wanted = shuffle_wanted(state)
    result = spotify_shuffle(p, "Toggle" if wanted is None else ("On" if wanted else "Off"))
    result["action"] = "SHUFFLE_MEDIA"
    return result


def repeat_media(target: str | None = None) -> Dict[str, Any]:
    """Cycle repeat mode (None → Track → Playlist) via playerctl."""
    from eli.integrations.media.cross_platform import spotify_set_loop
    p = _resolve_media_target(target, "loop") or _get_active_player("loop") or "spotify"
    result = spotify_set_loop(p)
    result["action"] = "REPEAT_MEDIA"
    return result


def now_playing() -> Dict[str, Any]:
    """Report what ELI is currently playing and from where."""
    src = _MEDIA_STATE.get("source")
    title = _MEDIA_STATE.get("title") or "a track"
    if src == "mpv" and _mpv_alive():
        # prefer mpv's resolved media-title; fall back to the requested query
        mt = _mpv_ipc(["get_property", "media-title"], want_response=True)
        if isinstance(mt, str) and mt.strip():
            title = mt.strip()
        paused = _mpv_ipc(["get_property", "pause"], want_response=True)
        head = "⏸ Paused" if paused is True else "▶ Playing"
        mode = "audio (background)" if _MEDIA_STATE.get("mpv_headless") else "mpv video"
        msg = f"{head}: {title} — YouTube ({mode})."
        return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}

    # Windows and macOS have no MPRIS: ask the OS what is playing (eli.integrations.media.system_session)
    from eli.utils import platform_compat as _pc_np
    if _pc_np.WINDOWS or _pc_np.MACOS:
        from eli.integrations.mpris import playerctl_backend as _pb_np
        _st = _pb_np.get_player_status()
        if _st.get("ok") and _st.get("title"):
            head = {"playing": "▶ Playing", "paused": "⏸ Paused", "stopped": "⏹ Stopped"}.get(
                str(_st.get("status")), "▶ Playing")
            meta = f"{_st['artist']} — {_st['title']}" if _st.get("artist") else _st["title"]
            msg = f"{head}: {meta} — {_st.get('player')}."
            return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}

    # Live playerctl metadata beats the cached _MEDIA_STATE title — the cache
    # stores what we *asked* Spotify to play ("liked songs (playlist)"), not
    # what is actually playing (artist — track).
    _player = "spotify" if src == "spotify" else (_get_active_player() or "")
    if _player:
        head, artist, track = _spotify_live_meta(_player)
        if track:
            meta = f"{artist} — {track}" if artist else track
            msg = f"{head}: {meta} — {_player}."
            return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}

    if src == "spotify":
        head = "▶ Playing" if _spotify_is_playing() else "⏸ Paused"
        msg = f"{head}: {title} — Spotify."
        return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}
    msg = "Nothing is playing right now."
    return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}

__all__ = ['MEDIA_DUCK', 'MEDIA_PLAYER', '_BROWSER_PLAYERS', '_DUCKED_MEDIA', '_DUCK_LOCK', '_DUCK_TIMER', '_get_active_player', '_playerctl', '_resolve_media_target', '_targets_mpv', 'play_media', '_yt_resolve_watch_url', '_yt_mix_url', '_spotify_open_uri', '_spotify_search', '_spotify_play', '_spotify_is_playing', '_spotify_running', '_mpv_socket_path', '_mpv_alive', '_mpv_ipc', '_mpv_quit', '_set_now_playing', 'play_specific', '_playerctl_run', '_playerctl_p', '_duck_media_pause', '_duck_media_resume', '_duck_schedule_resume', '_spotify_player_name', '_spotify_launch', '_spotify_ctl', 'stop_media', 'pause_media', 'next_media', 'previous_media', 'shuffle_media', 'repeat_media', 'now_playing']


def _ensure_spotify_running() -> bool:
    """Launch the native Spotify app — never a browser tab."""
    from eli.integrations.media.cross_platform import spotify_launch_if_needed
    return spotify_launch_if_needed()


# ── dispatch handlers (v3 registry) ──
# Registering here is all it takes to add a media action — no executor edit.
def _h_media_control(a, args):
    cmd = (args.get("command") or "play-pause").strip().lower()
    target = (args.get("target") or args.get("app") or args.get("player") or "").strip().lower() or None
    cmd_map = {
        "pause": "PAUSE_MEDIA", "stop": "STOP_MEDIA",
        "play": "PLAY_MEDIA", "resume": "PLAY_MEDIA",
        "next": "NEXT_MEDIA", "skip": "NEXT_MEDIA",
        "previous": "PREVIOUS_MEDIA", "prev": "PREVIOUS_MEDIA",
        "shuffle": "SHUFFLE_MEDIA", "repeat": "REPEAT_MEDIA", "loop": "REPEAT_MEDIA",
    }
    mapped = cmd_map.get(cmd)
    if mapped:
        forwarded = dict(args)
        forwarded["_target"] = target
        if target and mapped == "PAUSE_MEDIA":
            return pause_media(target=target)
        if target and mapped in ("PLAY_MEDIA",):
            return play_media(target=target)
        if target and mapped == "STOP_MEDIA":
            return stop_media(target=target)
        if target and mapped == "NEXT_MEDIA":
            return next_media(target=target)
        if target and mapped == "PREVIOUS_MEDIA":
            return previous_media(target=target)
        if mapped == "SHUFFLE_MEDIA":
            return shuffle_media(target=target, state=args.get("state"))
        if mapped == "REPEAT_MEDIA":
            return repeat_media(target=target)
        return _execute_impl(mapped, forwarded)
    # Anything else ("toggle"): the media backend, which is MPRIS on Linux and the OS's media
    # session or media keys on Windows and macOS (a bare playerctl existed only on Linux)
    res = _playerctl({"toggle": "play-pause", "playpause": "play-pause"}.get(cmd, cmd), target)
    res["action"] = a
    return res


def _h_skip_youtube_ad(a, args):
    # YouTube's own skip button, found on the screen and clicked (screen_locator: OCR or the local
    # vision model, then a click; every OS). ELI's own YouTube player (mpv) plays no ads.
    if _MEDIA_STATE.get("source") == "mpv" and _mpv_alive():
        msg = "ELI's YouTube player has no ads to skip."
        return {"ok": True, "action": a, "content": msg, "response": msg}
    try:
        from eli.perception.screen_locator import locate_on_screen
        last = {}
        for label in ("Skip Ads", "Skip Ad", "Skip"):
            last = locate_on_screen(query=label, click=True) or {}
            if last.get("clicked"):
                msg = "Skipped the ad."
                return {"ok": True, "action": a, "content": msg, "response": msg}
        why = str(last.get("error") or "").strip()
        msg = ("No skip button is on the screen yet; YouTube shows it a few seconds into an ad."
               + (f" ({why})" if why else ""))
    except Exception as e:
        msg = f"Could not look for the skip button: {e}"
    return {"ok": False, "action": a, "content": msg, "response": msg}


def _h_volume(a, args):
    direction = (args.get("direction") or "").strip().lower()
    level = args.get("level")
    delta = int(args.get("delta", 10))
    return _volume_fallback(direction=direction, delta=delta, level=level)


def _media_target(args) -> Optional[str]:
    """The player a transport command names ("pause spotify"), or None for whatever is playing."""
    args = args or {}
    return (args.get("target") or args.get("service") or args.get("player") or "").strip() or None


from eli.execution.effectors.registry import register as _register


def _h_play_media(action, args):
    query = (args.get("query") or args.get("song") or args.get("artist") or "").strip()
    target = _media_target(args)
    browser = bool(args.get("browser"))
    if query:
        # `kind` (track, album, episode, ...) when the router or the model read it; else the request's
        # own words are read (spotify_intent.media_request).
        return play_specific(query, target, browser=browser, kind=str(args.get("kind") or "") or None)
    return play_media(target)


def _h_shuffle_media(action, args):
    target = (args.get("target") or args.get("app") or args.get("player") or "").strip() or None
    return shuffle_media(target=target, state=args.get("state"))


def _h_repeat_media(action, args):
    target = (args.get("target") or args.get("app") or args.get("player") or "").strip() or None
    return repeat_media(target=target)


for _action, _fn in {
    "PLAY_MEDIA": _h_play_media,
    "STOP_MEDIA": lambda a, args: stop_media(_media_target(args)),
    "PAUSE_MEDIA": lambda a, args: pause_media(_media_target(args)),
    "NOW_PLAYING": lambda a, args: now_playing(),
    "NEXT_MEDIA": lambda a, args: next_media(_media_target(args)),
    "PREVIOUS_MEDIA": lambda a, args: previous_media(_media_target(args)),
    "SHUFFLE_MEDIA": _h_shuffle_media,
    "REPEAT_MEDIA": _h_repeat_media,
}.items():
    _register(_action, _fn)

_register("MEDIA_CONTROL", _h_media_control)
_register("SKIP_YOUTUBE_AD", _h_skip_youtube_ad)
_register("VOLUME", _h_volume)
