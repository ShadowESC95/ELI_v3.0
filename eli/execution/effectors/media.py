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


def _get_active_player() -> Optional[str]:
    """Get the best active MPRIS2 player via playerctl. Returns None if none found."""
    pass  # shutil already imported at module level
    if not shutil.which("playerctl"):
        return None
    try:
        r = subprocess.run(["playerctl", "--list-all"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        players = [p.strip() for p in r.stdout.splitlines() if p.strip()]
        # Priority: spotify first, then whatever is running
        for preferred in ("spotify", "Spotify", "vlc", "mpv", "firefox", "chromium"):
            for p in players:
                if p.lower().startswith(preferred.lower()):
                    return p
        return players[0] if players else None
    except Exception:
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
        }
    return fn(player)


def _resolve_media_target(target: str | None) -> str | None:
    """Map a user-supplied target string to an MPRIS player name or None."""
    if not target:
        return None
    alias = _TARGET_ALIASES.get(target.lower().strip())
    if alias == "browser":
        # Return first available browser player from playerctl
        try:
            import subprocess as _sp
            r = _sp.run(["playerctl", "--list-all"], capture_output=True, text=True, timeout=3)
            for p in r.stdout.splitlines():
                for bp in _BROWSER_PLAYERS:
                    if p.strip().lower().startswith(bp.lower()):
                        return p.strip()
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
    result = _playerctl("play", _resolve_media_target(target) or _get_active_player())
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


def play_specific(query: str, target: str | None = None, *, browser: bool = False) -> Dict[str, Any]:
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

    player = _resolve_media_target(target) or _get_active_player()

    from eli.integrations.media import spotify_intent as _si

    def _clean(q: str) -> str:
        q = _si.sanitize_media_query(q)
        q = _re.sub(
            r"^(?:a\s+song\s+|the\s+song\s+|the\s+track\s+|a\s+track\s+|me\s+a?\s*|some\s+)",
            "", q, flags=_re.I,
        )
        return q.strip()

    query = _clean(query)
    _by_m = _re.match(r"^(.+?)\s+by\s+(.+)$", query, _re.I)

    # ── 1. Spotify target ─────────────────────────────────────────────────────
    is_spotify = "spotify" in t or (player and "spotify" in (player or "").lower())
    is_youtube = "youtube" in t or t in ("yt",)
    is_yt_web  = is_youtube and bool(_re.search(r"\bweb(?:site)?\b", t))
    is_yt_browser = (
        is_yt_web
        or bool(_re.search(r"youtube\s*(?:\.com|\s+com)\b", t, _re.I))
    )

    if (not is_spotify and not is_youtube
            and _spotify_running()
            and (_si.playlist_name(query) or _si.prefers_spotify_music_context(query))):
        is_spotify = True

    if is_spotify and not is_youtube:
        import time as _time
        search_q = query
        if _by_m and not _si.album_request(query)[0]:
            search_q = f"{_by_m.group(1).strip()} {_by_m.group(2).strip()}"

        # ── Liked Songs ──
        _pl_name = "" if _by_m else _spotify_playlist_name(search_q)
        # Checked directly against search_q too, not just the extracted
        # _pl_name — playlist_name() only fires when the literal word
        # "playlist" is present, so "my liked songs" (no "playlist" in it)
        # never reached this branch at all and fell through to a bare
        # keyword search of the words "my liked songs" in Spotify's track tab.
        _is_liked = (not _by_m) and (
            _spotify_is_liked_songs_request(search_q)
            or (_pl_name and _spotify_is_liked_songs_request(_pl_name))
        )
        if _is_liked:
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

        # ── Named playlist ──
        if _pl_name and not _spotify_is_liked_songs_request(_pl_name):
            _hit = _spotify_try_open_and_play(
                _spotify_resolve_playlist_uri(_pl_name),
                label=f"the “{_pl_name}” playlist", kind="playlist",
            )
            if _hit:
                return _hit

        # ── Album (optionally by artist) ──
        _album, _album_artist = _si.album_request(query)
        if _album:
            _hit = _spotify_try_open_and_play(
                _spotify_resolve_album_uri(_album, _album_artist),
                label=f"the “{_album}” album" + (f" by {_album_artist}" if _album_artist else ""),
                kind="album",
            )
            if _hit:
                return _hit
            # Fall back to album search tab + play top result
            if not _spotify_running():
                _ensure_spotify_running()
                _spotify_wait_running(timeout=8.0)
            _before = _spotify_live_meta("spotify")[1:]
            if _spotify_search(f"{_album} {_album_artist or ''}".strip(), prefer="albums"):
                _time.sleep(2.4)
                _spotify_clear_track_repeat()
                if _spotify_playback_confirmed(_before, hint=_album_artist or ""):
                    _set_now_playing("spotify", f"{_album} (album)")
                    msg = f"Playing the “{_album}” album on Spotify."
                    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                            "kind": "album", "content": msg, "response": msg}
                # Retry play once after album search UI settles
                _time.sleep(1.2)
                if _spotify_playback_confirmed(_before, hint=_album_artist or ""):
                    _set_now_playing("spotify", f"{_album} (album)")
                    msg = f"Playing the “{_album}” album on Spotify."
                    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                            "kind": "album", "content": msg, "response": msg}
                # Unconfirmed — fall through to the generic track search below
                # rather than claiming success, same as the original behaviour.

        # ── Artist discography / "songs by X" ──
        _artist = _si.artist_songs_request(query)
        if _artist:
            _hit = _spotify_try_open_and_play(
                _spotify_resolve_artist_uri(_artist),
                label=f"songs by {_artist}", kind="artist",
            )
            if _hit:
                return _hit

        # ── Named playlist (search fallback only if URI scrape failed above) ──
        if _pl_name and not _spotify_is_liked_songs_request(_pl_name):
            _pl_opened = _spotify_search(_pl_name, prefer="playlists")
            if not _pl_opened:
                try:
                    _ensure_spotify_running()
                    _spotify_open_uri(f"spotify:search:{_pl_name}/playlists")
                    for _ in range(8):
                        _time.sleep(1.0)
                        if _spotify_running():
                            break
                    _pl_opened = _spotify_search(_pl_name, prefer="playlists")
                except Exception:
                    _pl_opened = False
            if _pl_opened:
                _before = _spotify_live_meta("spotify")[1:]
                _time.sleep(1.8)
                _spotify_clear_track_repeat()
                if _spotify_playback_confirmed(_before):
                    _set_now_playing("spotify", f"{_pl_name} (playlist)")
                    msg = f"Playing the “{_pl_name}” playlist on Spotify."
                    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                            "kind": "playlist", "content": msg, "response": msg}
                msg = (f"I opened Spotify's playlist results for “{_pl_name}” but "
                       f"playback didn't start — pick the playlist in Spotify, or "
                       f"try a more specific name.")
                return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                        "search_only": True, "kind": "playlist",
                        "content": msg, "response": msg}
            msg = (f"I couldn't reach Spotify to play the “{_pl_name}” playlist — "
                   f"is it installed and running?")
            return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                    "search_only": True, "target": "spotify", "kind": "playlist",
                    "content": msg, "response": msg}

        # ── Track / generic search (tracks tab, not playlists) ──
        _track_q = search_q
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
    p = _resolve_media_target(target) or _get_active_player()
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
    result = _playerctl("pause", _resolve_media_target(target) or _get_active_player())
    result["action"] = "PAUSE_MEDIA"
    return result


def next_media(target: str | None = None) -> Dict[str, Any]:
    """Skip to next track."""
    if _targets_mpv(target):
        _mpv_ipc(["playlist-next", "weak"])
        return {"ok": True, "action": "NEXT_MEDIA", "content": "⏭ Next — YouTube",
                "response": "⏭ Next — YouTube"}
    p = _resolve_media_target(target) or _get_active_player()
    result = _playerctl("next", p)
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
    p = _resolve_media_target(target) or _get_active_player()
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


def shuffle_media(target: str | None = None) -> Dict[str, Any]:
    """Toggle shuffle mode via playerctl."""
    from eli.integrations.media.cross_platform import spotify_shuffle
    p = _resolve_media_target(target) or _get_active_player() or "spotify"
    result = spotify_shuffle(p)
    result["action"] = "SHUFFLE_MEDIA"
    return result


def repeat_media(target: str | None = None) -> Dict[str, Any]:
    """Cycle repeat mode (None → Track → Playlist) via playerctl."""
    from eli.integrations.media.cross_platform import spotify_set_loop
    p = _resolve_media_target(target) or _get_active_player() or "spotify"
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
            return shuffle_media(target=target)
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
        return play_specific(query, target, browser=browser)
    return play_media(target)


def _h_shuffle_media(action, args):
    target = (args.get("target") or args.get("app") or args.get("player") or "").strip() or None
    return shuffle_media(target=target)


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
