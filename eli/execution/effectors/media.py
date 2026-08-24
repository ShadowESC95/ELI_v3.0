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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from eli.utils.log import get_logger
log = get_logger(__name__)

from eli.execution.effectors._base import _allow_or_block, _open_in_browser, _run_argv
import re  # module-level: _PLAYLIST_RE is compiled at import time


def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)


def _browser_user_dir(*a, **k):
    from eli.execution.executor_enhanced import _browser_user_dir as _f
    return _f(*a, **k)


def _volume_fallback(*a, **k):
    from eli.execution.executor_enhanced import _volume_fallback as _f
    return _f(*a, **k)

# ── media state ──
MEDIA_DUCK = str(os.environ.get("ELI_MEDIA_DUCK", "1")).strip().lower() not in ("0","false","no","off")
MEDIA_PLAYER = (os.environ.get("ELI_MEDIA_PLAYER", "spotify") or "spotify").strip()
_BROWSER_PLAYERS = {"firefox", "Firefox", "chromium", "Chromium", "chrome"}
_DUCKED_MEDIA = False
_DUCK_LOCK = threading.Lock()
_DUCK_TIMER = None
_MEDIA_STATE: Dict[str, Any] = {"source": None, "title": None, "mpv_sock": None}
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
    """Run a targeted playerctl command. Self-contained, no external imports."""
    pass  # shutil already imported at module level
    if not shutil.which("playerctl"):
        return {"ok": False, "error": "playerctl not installed — run: sudo apt install playerctl",
                "content": "playerctl not installed", "response": "playerctl not installed"}
    p = player or _get_active_player()
    if not p:
        return {"ok": False, "error": "No media player running",
                "content": "No media player running", "response": "No media player running"}
    try:
        r = subprocess.run(["playerctl", "-p", p, cmd], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            labels = {"play": "▶ Playing", "pause": "⏸ Paused", "stop": "⏹ Stopped",
                      "next": "⏭ Next track", "previous": "⏮ Previous track"}
            msg = f"{labels.get(cmd, cmd)} — {p}"
            return {"ok": True, "player": p, "content": msg, "response": msg}
        else:
            msg = f"playerctl {cmd} failed: {r.stderr.strip() or 'unknown error'}"
            return {"ok": False, "player": p, "error": r.stderr.strip(), "content": msg, "response": msg}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"playerctl {cmd} timed out", "content": "Command timed out", "response": "Command timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e), "content": str(e), "response": str(e)}


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
    return alias


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


def _spotify_open_uri(uri: str) -> bool:
    """OpenUri on a running Spotify via MPRIS dbus. Accepts both spotify: URIs
    and https://open.spotify.com/... URLs (Spotify opens the latter in-app)."""
    import subprocess as _sp2
    try:
        r = _sp2.run(
            ["dbus-send", "--print-reply",
             "--dest=org.mpris.MediaPlayer2.spotify",
             "/org/mpris/MediaPlayer2",
             "org.mpris.MediaPlayer2.Player.OpenUri",
             f"string:{uri}"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _spotify_search(query: str, prefer: str | None = None) -> bool:
    """Open a Spotify search via dbus.

    prefer='playlists' targets the **Playlists** filter tab
    (open.spotify.com/search/<q>/playlists), so the top result is a PLAYLIST that
    contains the requested song — playing it keeps continuation thematic, instead
    of the 'All' tab which plays one track then drifts to unrelated songs
    (user-reported). Falls back to the classic spotify:search: URI if the
    filtered URL is rejected.
    """
    import urllib.parse as _up2
    if prefer in ("playlist", "playlists"):
        if _spotify_open_uri(f"https://open.spotify.com/search/{_up2.quote(query)}/playlists"):
            return True
        # Fall back to the unfiltered search URI.
        return _spotify_open_uri(f"spotify:search:{_up2.quote(query)}")
    return _spotify_open_uri(f"spotify:search:{_up2.quote(query)}")


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
    """MPRIS LoopStatus: "None", "Track", "Playlist", or "" when unknown."""
    import subprocess as _sp2
    try:
        cp = _sp2.run(["playerctl", "-p", "spotify", "loop"],
                      capture_output=True, text=True, timeout=5)
        if cp.returncode == 0:
            return (cp.stdout or "").strip()
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return ""


def _spotify_clear_track_repeat() -> bool:
    """Turn repeat-one off so a track can advance. True when a change was made.

    Repeat-one makes every request look like "it just repeats the same song".
    It was NOT the cause on the reporting machine (LoopStatus read back as
    None), so this only ever acts when repeat-one is genuinely set — it never
    changes a setting the user chose for a playlist.
    """
    if _spotify_loop_status() != "Track":
        return False
    import subprocess as _sp2
    try:
        _sp2.run(["playerctl", "-p", "spotify", "loop", "None"],
                 capture_output=True, timeout=5)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
        return False
    return _spotify_loop_status() != "Track"


def _spotify_play() -> bool:
    """Start/resume Spotify playback (no Web API). After a spotify:search: OpenUri
    the search results are the active context, so Play starts the top match.

    Returns True only if playback actually reaches the 'Playing' state — a Play
    call that returns 0 but leaves Spotify paused/stopped is reported as False so
    callers can be honest about search-only outcomes.
    """
    import subprocess as _sp2
    try:
        _sp2.run(["playerctl", "-p", "spotify", "play"], capture_output=True, timeout=5)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        _sp2.run(
            ["dbus-send", "--print-reply",
             "--dest=org.mpris.MediaPlayer2.spotify",
             "/org/mpris/MediaPlayer2",
             "org.mpris.MediaPlayer2.Player.Play"],
            capture_output=True, timeout=5,
        )
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return _spotify_is_playing()


def _spotify_is_playing() -> bool:
    """True iff Spotify's MPRIS PlaybackStatus is 'Playing'."""
    import subprocess as _sp2
    try:
        r = _sp2.run(["playerctl", "-p", "spotify", "status"],
                     capture_output=True, timeout=5, text=True)
        if r.returncode == 0 and "playing" in (r.stdout or "").strip().lower():
            return True
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return False


def _spotify_running() -> bool:
    import subprocess as _sp2
    try:
        r = _sp2.run(["pgrep", "-x", "spotify"], capture_output=True, timeout=4)
        return r.returncode == 0
    except Exception:
        return False


def _mpv_socket_path() -> str:
    # Mirrors media_runtime._mpv_socket so reader (here) and writer agree.
    env = os.environ.get("ELI_YOUTUBE_MPV_IPC")
    if env:
        return env
    try:
        from eli.utils import platform_compat as _pc
        if _pc.WINDOWS:
            return r"\\.\pipe\eli_youtube_mpv"
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    import tempfile
    return os.path.join(tempfile.gettempdir(), "eli_youtube_mpv.sock")


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
    return ["", "android", "android_vr"]


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
    import socket as _sock
    p = _MEDIA_STATE.get("mpv_sock") or _mpv_socket_path()
    if not os.path.exists(p):
        return False
    try:
        with _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(p)
        return True
    except Exception:
        return False


def _mpv_ipc(command: list, *, want_response: bool = False):
    """Send one JSON IPC command to the headless YouTube mpv. Returns True/data, or
    False/None on failure. Never raises."""
    import socket as _sock
    import json as _json
    p = _MEDIA_STATE.get("mpv_sock") or _mpv_socket_path()
    if not os.path.exists(p):
        return None if want_response else False
    try:
        with _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(p)
            s.sendall((_json.dumps({"command": command}) + "\n").encode())
            out = None
            if want_response:
                buf = s.recv(8192).decode("utf-8", "ignore")
                for line in buf.splitlines():
                    try:
                        j = _json.loads(line)
                        if isinstance(j, dict) and "data" in j:
                            out = j["data"]
                            break
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
        return out if want_response else True
    except Exception:
        return None if want_response else False


def _mpv_load_confirmed(sock_path: str) -> bool:
    """True once mpv has actually RESOLVED AND OPENED a media file on `sock_path`.

    Deliberately queries the socket we just spawned rather than going through
    _mpv_ipc(), which resolves its path from _MEDIA_STATE — during verification that
    still points at the *previous* track, so it would answer the wrong question.

    `duration` is the honest signal: mpv only knows it after yt-dlp has resolved the
    URL and the demuxer has opened the stream. Process liveness alone is not enough —
    a slow yt-dlp failure stays alive for several seconds before exiting, which is
    exactly how a track that never played got announced as playing.
    """
    import json as _json
    import socket as _sock
    if not sock_path or not os.path.exists(sock_path):
        return False
    try:
        with _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(sock_path)
            s.sendall((_json.dumps({"command": ["get_property", "duration"]}) + "\n").encode())
            buf = s.recv(8192).decode("utf-8", "ignore")
        for line in buf.splitlines():
            try:
                j = _json.loads(line)
            except Exception:
                continue
            if isinstance(j, dict) and isinstance(j.get("data"), (int, float)):
                return True
    except Exception:
        return False
    return False


def _prune_mpv_logs(keep: int = 5) -> None:
    """Keep only the newest `keep` mpv failure logs so /tmp cannot grow without bound."""
    import glob as _glob
    try:
        logs = sorted(_glob.glob(os.path.join(tempfile.gettempdir(), "eli_mpv_*.log")),
                      key=os.path.getmtime, reverse=True)
        for stale in logs[keep:]:
            try:
                os.unlink(stale)
            except OSError:
                pass
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def _mpv_quit() -> None:
    """Stop the headless YouTube mpv if it's running."""
    if _mpv_alive():
        _mpv_ipc(["quit"])


def _set_now_playing(source: str, title: str | None, *, mpv_sock: str | None = None) -> None:
    # Switching to a different source: stop the old headless mpv so two things don't
    # play at once (e.g. YouTube audio + Spotify).
    if _MEDIA_STATE.get("source") == "mpv" and source != "mpv":
        _mpv_quit()
    _MEDIA_STATE.update({"source": source, "title": title, "mpv_sock": mpv_sock})


def play_specific(query: str, target: str | None = None) -> Dict[str, Any]:
    """Play a specific song/artist/genre.

    Dispatch priority:
      1. Explicit spotify target  → Spotify (dbus if open, else xdg-open URI)
      2. Explicit youtube target  → yt-dlp+mpv if available, else browser watch URL
      3. "youtube web/website"    → browser watch URL (never mpv)
      4. "X by Y" (no target)     → yt-dlp+mpv
      5. Generic fallback         → browser watch URL
    """
    import subprocess as _sp
    import urllib.parse
    import re as _re

    t = (target or "").lower().strip()
    player = _resolve_media_target(target) or _get_active_player()

    def _clean(q: str) -> str:
        q = q.strip()
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
    is_yt_browser = is_yt_web or bool(_re.search(r"youtube\.com", t, _re.I))

    # "play my workout playlist" with no platform named would be searched for
    # on YouTube as if it were a song title. A playlist request belongs to the
    # music player that is actually running. This only fires when NO platform
    # was named, so an explicit target is still never overridden.
    if (not is_spotify and not is_youtube
            and _spotify_playlist_name(query) and _spotify_running()):
        is_spotify = True

    if is_spotify and not is_youtube:
        import time as _time
        search_q = (
            f"{_by_m.group(1).strip()} {_by_m.group(2).strip()}" if _by_m else query
        )
        # No Web API: open the search results (this sets the active context to the
        # matching TRACKS), let results load, then issue Play so the TOP SONG match
        # starts. Playing the specific requested song is the intended behaviour — the
        # earlier 'playlists' filter approach opened a LIST of playlists where Play
        # had nothing to start, which is why playback failed.
        # A playlist request needs a different Spotify context from a song
        # request: a track plays once and stops (which is what "it just repeats
        # instead of going onto similar songs" describes), while a playlist
        # keeps going on its own. There was previously no way to ask for one.
        # play_specific() receives (query, target) only -- there is no args dict
        # in this scope, so the request is read from the phrasing, which is how
        # users say it anyway ("play my workout playlist").
        _pl_name = "" if _by_m else _spotify_playlist_name(search_q)
        if _pl_name:
            _pl_opened = _spotify_search(_pl_name, prefer="playlists")
            if not _pl_opened:
                try:
                    _open_in_browser(
                        f"spotify:search:{urllib.parse.quote(_pl_name)}")
                    for _ in range(8):
                        _time.sleep(1.0)
                        if _spotify_running():
                            break
                    _pl_opened = _spotify_search(_pl_name, prefer="playlists")
                except Exception:
                    _pl_opened = False
            if _pl_opened:
                _time.sleep(1.8)     # the playlists tab is slower than tracks
                _spotify_clear_track_repeat()
                if _spotify_play():
                    _set_now_playing("spotify", f"{_pl_name} (playlist)")
                    msg = f"Playing the \u201c{_pl_name}\u201d playlist on Spotify."
                    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                            "kind": "playlist", "content": msg, "response": msg}
                # Opened but nothing started: the playlists tab is a LIST of
                # playlists, and Play has nothing to start until one is opened.
                # Say that rather than claiming playback.
                msg = (f"I opened Spotify's playlist results for \u201c{_pl_name}\u201d but "
                       f"playback didn't start \u2014 the results are a list of playlists, so "
                       f"open one and I'll take it from there.")
                return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                        "search_only": True, "kind": "playlist",
                        "content": msg, "response": msg}
            msg = (f"I couldn't reach Spotify to play the \u201c{_pl_name}\u201d playlist \u2014 "
                   f"is it installed and running?")
            return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                    "search_only": True, "target": "spotify", "kind": "playlist",
                    "content": msg, "response": msg}

        _opened = _spotify_search(search_q)
        if not _opened:
            # Spotify not running yet — launch with the search URI, wait, retry.
            try:
                _open_in_browser(f"spotify:search:{urllib.parse.quote(search_q)}")
                for _ in range(8):
                    _time.sleep(1.0)
                    if _spotify_running():
                        break
                _opened = _spotify_search(search_q)
            except Exception:
                _opened = False
        if _opened:
            _time.sleep(1.6)            # let the results view populate
            # Repeat-one turns every request into "it plays the same song over
            # and over". Only touched when it is actually set.
            _spotify_clear_track_repeat()
            if _spotify_play():
                _set_now_playing("spotify", search_q)
                msg = f"Playing “{search_q}” on Spotify."
                return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                        "content": msg, "response": msg}
            msg = (f"I opened the Spotify search for “{search_q}” but Spotify didn’t start "
                   f"playback — press play in Spotify, or check that playerctl/dbus can "
                   f"reach it.")
            return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                    "search_only": True, "content": msg, "response": msg}
        # Spotify was the EXPLICITLY requested target. Even if we couldn't reach it
        # (not installed / not running / dbus refused), do NOT silently fall through
        # to YouTube — that opens a second platform the user never asked for.
        msg = (f"I couldn’t reach Spotify to play “{search_q}” — is it installed and "
               f"running? Open it and try again, or say “play {search_q} on youtube”.")
        return {"ok": False, "action": "PLAY_MEDIA", "played": False,
                "search_only": True, "target": "spotify",
                "content": msg, "response": msg}

    # ── 2. "youtube web/website/.com" → browser only (never mpv) ─────────────
    if is_yt_browser:
        watch = _yt_mix_url(_yt_resolve_watch_url(query))
        url = watch or f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
        _open_in_browser(url)
        msg = f"Opening YouTube in browser: {query} (continuous mix)"
        return {"ok": True, "action": "PLAY_MEDIA", "content": msg, "response": msg}

    # ── 3. YouTube target (plain) or "X by Y" → yt-dlp+mpv ──────────────────
    yt_search = (
        f"{_by_m.group(1).strip()} {_by_m.group(2).strip()} official audio"
        if _by_m else query
    )

    _yt_direct_err = ""          # why direct mpv playback failed, if it did
    _yt_have_tools = bool(shutil.which("yt-dlp") and shutil.which("mpv"))
    if _yt_have_tools:
        _mpv_err_log = None
        try:
            ipc = _mpv_socket_path()
            _mpv_quit()                      # stop any previous headless YouTube audio
            # mpv's stderr is the ONLY record of why a play attempt failed (yt-dlp
            # signature breakage, geo-block, dead network). Sending it to DEVNULL used
            # to destroy that evidence, so a failure was both unnoticed and
            # un-diagnosable after the fact. Keep it on disk for the post-mortem.
            try:
                _mpv_err_log = tempfile.NamedTemporaryFile(
                    prefix="eli_mpv_", suffix=".log", delete=False, mode="w+",
                )
                _err_target = _mpv_err_log
            except Exception:
                # A read-only or full temp dir must not cost us playback entirely —
                # degrade to DEVNULL (no post-mortem) rather than skipping mpv.
                log.debug("suppressed exception", exc_info=True)
                _err_target = _sp.DEVNULL
            # One shot with whatever client yt-dlp chose is what produced the
            # 403: mpv exited rc=3 and the user got the browser fallback with
            # no explanation. Walk the ladder instead, and only re-try when the
            # failure is actually client-bound.
            _yt_clients = _yt_player_clients()
            _yt_used_client = ""
            proc = None
            # Popen returns as soon as the process is SPAWNED, which says nothing about
            # whether it plays — that is how "Playing … on YouTube" got reported for a
            # track that never started. Neither the IPC socket (mpv opens it within
            # ~0.1s regardless) nor mere liveness is proof: a slow yt-dlp failure stays
            # alive for seconds before exiting. Only `duration` becoming known means the
            # stream was really resolved and opened — measured at ~4s for a normal track
            # and ~6s for a livestream (which reports 0.0, still numeric, so it counts).
            # `media-title` is NOT usable here: mpv echoes the raw ytsearch string back
            # within 0.5s, long before anything is loaded. Poll for a DECISION and never
            # assert more than was observed:
            #   died          → report the failure and fall back
            #   duration known→ genuine playback, return as soon as it lands
            #   neither       → still resolving; say so rather than claim it plays
            _verify_s = float(os.environ.get("ELI_YT_VERIFY_SECONDS", "8.0"))
            _confirmed = False
            _rc = None
            for _yt_client in _yt_clients:
                # ytsearch1: resolves exactly ONE result and mpv stops when it
                # ends -- "youtube does not play automatically" is that. The
                # browser fallback already turns a watch URL into a Mix
                # (&list=RD<id>, YouTube's auto-generated song radio); do the
                # same for direct playback so mpv is handed a playlist and keeps
                # going. Falls back to ytsearch1 when the video id cannot be
                # resolved, so playback never depends on the scrape succeeding.
                _yt_target = f"ytdl://ytsearch1:{yt_search}"
                if _yt_autoplay_enabled():
                    _mix = _yt_mix_url(_yt_resolve_watch_url(yt_search))
                    if _mix and "list=RD" in _mix:
                        _yt_target = _mix
                _yt_cmd = ["mpv", _yt_target,
                           f"--input-ipc-server={ipc}",
                           "--ytdl-format=bestaudio/best"]
                if "list=RD" in _yt_target:
                    # Without this mpv takes only the first entry of a playlist
                    # URL, which would reproduce the very problem being fixed.
                    _yt_cmd.append("--ytdl-raw-options=yes-playlist=")
                if _yt_client:
                    # mpv splits --ytdl-raw-options on commas, so exactly one
                    # client per attempt — a comma-joined list would be parsed
                    # as a second, unknown option and mpv would refuse to start.
                    _yt_cmd.append(
                        f"--ytdl-raw-options=extractor-args=youtube:player_client={_yt_client}"
                    )
                _yt_cmd.append("--title=ELI-YouTube")
                _yt_used_client = _yt_client or "yt-dlp default"
                proc = _sp.Popen(
                    _yt_cmd,
                    stdout=_sp.DEVNULL, stderr=_err_target, start_new_session=True,
                )
                # NB: `_time` is bound only inside the Spotify branch above, which makes it a
                # function-local Python cannot resolve here — use the module-level `time`.
                _deadline = time.monotonic() + max(0.0, _verify_s)
                while time.monotonic() < _deadline:
                    if proc.poll() is not None:
                        break
                    if _mpv_load_confirmed(ipc):
                        _confirmed = True
                        break
                    time.sleep(0.1)
                _rc = proc.poll()
                if _confirmed or _rc is None:
                    break        # playing, or still resolving — either way, stop trying
                # Died. Only another client can fix a client-bound refusal.
                _probe = ""
                if _mpv_err_log is not None:
                    try:
                        _mpv_err_log.flush()
                        _mpv_err_log.seek(0)
                        _probe = _mpv_err_log.read()
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                if not _yt_is_client_bound_failure(_probe):
                    break
                log.info(
                    f"[MEDIA] YouTube client {_yt_used_client!r} was refused (403) "
                    f"for {yt_search!r} — retrying with the next client"
                )
            if _rc is not None:
                _tail = ""
                if _mpv_err_log is not None:
                    try:
                        _mpv_err_log.flush()
                        _mpv_err_log.seek(0)
                        _tail = " | ".join(
                            ln.strip() for ln in _mpv_err_log.read().splitlines()[-4:] if ln.strip()
                        )
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                log.warning(
                    f"[MEDIA] direct YouTube playback failed for {yt_search!r} "
                    f"(mpv rc={_rc}); stderr: {_tail or 'none captured'} "
                    f"[full log: {getattr(_mpv_err_log, 'name', 'not captured')}]"
                )
                _prune_mpv_logs()
                # The full stderr belongs in the log, NOT in `response` — this string is
                # spoken by TTS, and reading four lines of ytdl_hook diagnostics aloud is
                # useless to the user. Give them the one fact that changes what they do next.
                _yt_direct_err = (
                    "no match found on YouTube" if "empty playlist" in _tail.lower()
                    else "YouTube refused the stream for every player client I tried "
                         "(this usually means yt-dlp is out of date — `pipx upgrade yt-dlp`)"
                    if _yt_is_client_bound_failure(_tail)
                    else "mpv could not start playback"
                )
                # Fall through to the browser fallback below rather than claiming
                # playback that demonstrably is not happening.
            else:
                if _mpv_err_log is not None:
                    try:
                        _mpv_err_log.close()
                        os.unlink(_mpv_err_log.name)  # still running, keep no litter
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                # Register the source either way: mpv is alive, so NOW_PLAYING can go and
                # read the real state on demand — and it verifies liveness itself, so a
                # process that dies after this point still reports honestly.
                _title = (f"{_by_m.group(1).strip()} by {_by_m.group(2).strip()}"
                          if _by_m else query)
                _set_now_playing("mpv", _title, mpv_sock=ipc)
                _fallback_what = (f"'{_by_m.group(1).strip()}' by {_by_m.group(2).strip()}"
                                  if _by_m else f"'{query}'")
                if _confirmed:
                    # mpv resolved the real title; prefer it over the raw query so the
                    # confirmation reflects what is actually playing.
                    _resolved = _mpv_ipc(["get_property", "media-title"], want_response=True)
                    _what = (_resolved.strip()
                             if isinstance(_resolved, str) and _resolved.strip()
                             else _fallback_what)
                    msg = f"Playing {_what} on YouTube (audio, in the background)."
                    return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                            "content": msg, "response": msg}
                # Alive but nothing loaded yet within the window — usually a slow
                # yt-dlp resolve. Saying "playing" here would be the original defect in
                # slower clothing, so state exactly what is known and point at the
                # action that can settle it.
                log.info(
                    f"[MEDIA] mpv still resolving {yt_search!r} after {_verify_s:.1f}s "
                    f"— reporting as unconfirmed rather than claiming playback"
                )
                msg = (f"Starting {_fallback_what} on YouTube — it is still resolving, so "
                       f"I have not confirmed playback yet. Ask what's playing in a moment.")
                return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                        "pending": True, "content": msg, "response": msg}
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        finally:
            try:
                if _mpv_err_log is not None and not _mpv_err_log.closed:
                    _mpv_err_log.close()
            except Exception:
                log.debug("suppressed exception", exc_info=True)

    # ── 4. No yt-dlp/mpv (or mpv failed) → resolve watch URL and open in browser ─
    # Direct in-app playback needs yt-dlp + mpv. Be HONEST that this is a fallback,
    # not real playback (no-fake-actions) — and say what would actually unlock "play"
    # for THIS user: an install hint is wrong when the tools are present and mpv died,
    # so report the real reason in that case instead.
    _play_hint = (
        f" — direct playback failed ({_yt_direct_err})" if _yt_direct_err
        else "" if _yt_have_tools
        else (" — for direct playback install mpv + yt-dlp "
              "(sudo apt install mpv && pipx install yt-dlp)")
    )
    watch = _yt_mix_url(_yt_resolve_watch_url(yt_search))
    if watch:
        _open_in_browser(watch)
        _what = (f"'{_by_m.group(1).strip()}' by {_by_m.group(2).strip()}"
                 if _by_m else f"'{query}'")
        msg = f"Opening {_what} in the browser{_play_hint}."
        return {"ok": True, "action": "PLAY_MEDIA", "played": False,
                "search_only": True, "content": msg, "response": msg}

    # ── 5. Last resort: YouTube search page ──────────────────────────────────
    encoded = urllib.parse.quote_plus(query)
    _open_in_browser(f"https://www.youtube.com/results?search_query={encoded}")
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
    # Pause ONLY if it was actually playing; remember we ducked it.
    global _DUCKED_MEDIA
    if not MEDIA_DUCK:
        return
    if _DUCKED_MEDIA:
        return
    rc, out, _ = _playerctl_p("status")
    if rc == 0 and out.lower() == "playing":
        _playerctl_p("pause")
        _DUCKED_MEDIA = True


def _duck_media_resume():
    global _DUCKED_MEDIA
    if not MEDIA_DUCK:
        return
    if not _DUCKED_MEDIA:
        return
    _playerctl_p("play")
    _DUCKED_MEDIA = False


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


def next_media() -> Dict[str, Any]:
    """Skip to next track."""
    result = _playerctl("next")
    result["action"] = "NEXT_MEDIA"
    return result


def previous_media(target: str | None = None) -> Dict[str, Any]:
    """Go to previous track.

    Spotify's MPRIS implementation of 'Previous' behaves like a media key:
    if playback position > 3s it seeks to 0 (restart current track) instead
    of going to the previous track. We force position=0 before sending
    Previous so the command always skips back regardless of current position.
    """
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
    result = _playerctl("shuffle Toggle", _resolve_media_target(target) or _get_active_player())
    result["action"] = "SHUFFLE_MEDIA"
    return result


def repeat_media(target: str | None = None) -> Dict[str, Any]:
    """Cycle repeat mode (None → Track → Playlist) via playerctl."""
    result = _playerctl("loop Track", _resolve_media_target(target) or _get_active_player())
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
        msg = f"{head}: {title} — YouTube audio (headless mpv)."
        return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}
    if src == "spotify":
        head = "▶ Playing" if _spotify_is_playing() else "⏸ Paused"
        msg = f"{head}: {title} — Spotify."
        return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}
    # Fall back to whatever MPRIS player is active.
    try:
        p = _get_active_player()
        if p:
            import subprocess as _sp3
            r = _sp3.run(["playerctl", "-p", p, "metadata", "--format",
                          "{{artist}} — {{title}}"], capture_output=True, text=True, timeout=3)
            meta = (r.stdout or "").strip()
            if meta and meta != "—":
                return {"ok": True, "action": "NOW_PLAYING",
                        "content": f"▶ {meta} — {p}.", "response": f"▶ {meta} — {p}."}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    msg = "Nothing is playing right now."
    return {"ok": True, "action": "NOW_PLAYING", "content": msg, "response": msg}

__all__ = ['MEDIA_DUCK', 'MEDIA_PLAYER', '_BROWSER_PLAYERS', '_DUCKED_MEDIA', '_DUCK_LOCK', '_DUCK_TIMER', '_get_active_player', '_playerctl', '_resolve_media_target', '_targets_mpv', 'play_media', '_yt_resolve_watch_url', '_yt_mix_url', '_spotify_open_uri', '_spotify_search', '_spotify_play', '_spotify_is_playing', '_spotify_running', '_mpv_socket_path', '_mpv_alive', '_mpv_ipc', '_mpv_quit', '_set_now_playing', 'play_specific', '_playerctl_run', '_playerctl_p', '_duck_media_pause', '_duck_media_resume', '_duck_schedule_resume', '_spotify_player_name', '_spotify_launch', '_spotify_ctl', 'stop_media', 'pause_media', 'next_media', 'previous_media', 'shuffle_media', 'repeat_media', 'now_playing']


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
    }
    mapped = cmd_map.get(cmd)
    if mapped:
        # Forward target into sub-handler args
        forwarded = dict(args)
        forwarded["_target"] = target
        # Call targeted versions directly when target present
        if target and mapped == "PAUSE_MEDIA":
            return pause_media(target=target)
        if target and mapped in ("PLAY_MEDIA",):
            return play_media(target=target)
        if target and mapped == "STOP_MEDIA":
            return stop_media(target=target)
        return _execute_impl(mapped, forwarded)
    # Fallback: playerctl generic
    try:
        subprocess.run(["playerctl", cmd], timeout=5, capture_output=True)
        msg = f"Media: {cmd}"
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_skip_youtube_ad(a, args):
    try:
        from eli.tools.media.youtube import YouTubeController
        _cfg_obj = type("_Cfg", (), {"browser": (args or {}).get("browser", "chromium"), "browser_user_dir": str(_browser_user_dir())})()
        _yt = YouTubeController(_cfg_obj)
        result = _yt.skip_ad()
        msg = "YouTube ad skipped." if result.ok else f"Ad skip failed: {result.data.get('error', 'unknown')}"
        return {"ok": result.ok, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_volume(a, args):
    direction = (args.get("direction") or "").strip().lower()
    level = args.get("level")
    delta = int(args.get("delta", 10))
    return _volume_fallback(direction=direction, delta=delta, level=level)


from eli.execution.effectors.registry import register as _register


def _h_play_media(action, args):
    query = (args.get("query") or args.get("song") or args.get("artist") or "").strip()
    target = (args.get("target") or "").strip() or None
    if query:
        return play_specific(query, target)
    return play_media(target)


def _h_shuffle_media(action, args):
    target = (args.get("target") or args.get("app") or args.get("player") or "").strip() or None
    return shuffle_media(target=target)


def _h_repeat_media(action, args):
    target = (args.get("target") or args.get("app") or args.get("player") or "").strip() or None
    return repeat_media(target=target)


for _action, _fn in {
    "PLAY_MEDIA": _h_play_media,
    "STOP_MEDIA": lambda a, args: stop_media(),
    "PAUSE_MEDIA": lambda a, args: pause_media(),
    "NOW_PLAYING": lambda a, args: now_playing(),
    "NEXT_MEDIA": lambda a, args: next_media(),
    "PREVIOUS_MEDIA": lambda a, args: previous_media(),
    "SHUFFLE_MEDIA": _h_shuffle_media,
    "REPEAT_MEDIA": _h_repeat_media,
}.items():
    _register(_action, _fn)

_register("MEDIA_CONTROL", _h_media_control)
_register("SKIP_YOUTUBE_AD", _h_skip_youtube_ad)
_register("VOLUME", _h_volume)
