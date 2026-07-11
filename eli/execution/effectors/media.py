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

# ── media state ──
MEDIA_DUCK = str(os.environ.get("ELI_MEDIA_DUCK", "1")).strip().lower() not in ("0","false","no","off")
MEDIA_PLAYER = (os.environ.get("ELI_MEDIA_PLAYER", "spotify") or "spotify").strip()
_BROWSER_PLAYERS = {"firefox", "Firefox", "chromium", "Chromium", "chrome"}
_DUCKED_MEDIA = False
_DUCK_LOCK = threading.Lock()
_DUCK_TIMER = None


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


def _mpv_alive() -> bool:
    import socket as _sock
    p = _MEDIA_STATE.get("mpv_sock") or _mpv_socket_path()
    if not os.path.exists(p):
        return False
    try:
        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(p)
        s.close()
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
        s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
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
        s.close()
        return out if want_response else True
    except Exception:
        return None if want_response else False


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

    if shutil.which("yt-dlp") and shutil.which("mpv"):
        try:
            ipc = _mpv_socket_path()
            _mpv_quit()                      # stop any previous headless YouTube audio
            _sp.Popen(
                ["mpv", f"ytdl://ytsearch1:{yt_search}",
                 f"--input-ipc-server={ipc}",
                 "--ytdl-format=bestaudio/best",
                 "--title=ELI-YouTube"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True,
            )
            _title = (f"{_by_m.group(1).strip()} by {_by_m.group(2).strip()}"
                      if _by_m else query)
            _set_now_playing("mpv", _title, mpv_sock=ipc)
            msg = (f"Playing '{_by_m.group(1).strip()}' by {_by_m.group(2).strip()} on "
                   f"YouTube (audio, in the background)." if _by_m
                   else f"Playing '{query}' on YouTube (audio, in the background).")
            return {"ok": True, "action": "PLAY_MEDIA", "played": True,
                    "content": msg, "response": msg}
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    # ── 4. No yt-dlp/mpv → resolve watch URL and open in browser ─────────────
    # Direct in-app playback needs yt-dlp + mpv; without them we can only open the
    # browser. Be HONEST that this is the missing-tools fallback, not real playback
    # (no-fake-actions) — and tell the user exactly what unlocks "play".
    _play_hint = (" — for direct playback install mpv + yt-dlp "
                  "(sudo apt install mpv && pipx install yt-dlp)")
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

__all__ = ['MEDIA_DUCK', 'MEDIA_PLAYER', '_BROWSER_PLAYERS', '_DUCKED_MEDIA', '_DUCK_LOCK', '_DUCK_TIMER', '_get_active_player', '_playerctl', '_resolve_media_target', '_targets_mpv', 'play_media', '_yt_resolve_watch_url', '_yt_mix_url', '_spotify_open_uri', '_spotify_search', '_spotify_play', '_spotify_is_playing', '_spotify_running', '_mpv_socket_path', '_mpv_alive', '_mpv_ipc', '_mpv_quit', '_set_now_playing', 'play_specific', '_playerctl_run', '_playerctl_p', '_duck_media_pause', '_duck_media_resume', '_duck_schedule_resume', '_spotify_player_name', '_spotify_launch', '_spotify_ctl']
