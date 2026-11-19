"""Cross-platform Spotify control and mpv IPC.

Linux uses MPRIS/dbus + playerctl. macOS uses AppleScript + URI schemes.
Windows uses URI schemes + virtual media keys. Android/Termux uses intents
and termux-open-url. All paths degrade gracefully — never raise.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from typing import Any

from eli.utils.log import get_logger

log = get_logger(__name__)

try:
    from eli.utils import platform_compat as pc
except Exception:  # pragma: no cover
    import sys as _sys

    pc = type("_PC", (), {
        "LINUX": _sys.platform.startswith("linux"),
        "MACOS": _sys.platform == "darwin",
        "WINDOWS": _sys.platform.startswith("win"),
        "ANDROID": False,
    })()


def _run(argv: list[str], *, timeout: float = 5.0) -> tuple[bool, str, str]:
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as exc:
        return False, "", str(exc)


def is_process_running(name: str) -> bool:
    """True when a process whose name contains `name` is running."""
    needle = (name or "").strip().lower()
    if not needle:
        return False

    if pc.LINUX or pc.MACOS:
        ok, out, _ = _run(["pgrep", "-if", needle], timeout=4)
        if ok and out:
            return True
        ok, out, _ = _run(["ps", "-A", "-o", "comm="], timeout=4)
        if ok:
            return any(needle in ln.strip().lower() for ln in out.splitlines())
        return False

    if pc.WINDOWS:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            script = (
                f"if (Get-Process -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.ProcessName -like '*{needle}*' }}) "
                f"{{ exit 0 }} else {{ exit 1 }}"
            )
            ok, _, _ = _run([ps, "-NoProfile", "-Command", script], timeout=5)
            return ok
        ok, out, _ = _run(["tasklist"], timeout=5)
        return ok and needle in out.lower()

    if pc.ANDROID:
        ok, out, _ = _run(["pidof", needle], timeout=3)
        if ok and out.strip():
            return True
        ok, out, _ = _run(["ps"], timeout=3)
        return ok and needle in out.lower()

    return False


def _macos_osascript(script: str) -> tuple[bool, str]:
    osascript = shutil.which("osascript")
    if not osascript:
        return False, "osascript not available"
    ok, out, err = _run([osascript, "-e", script], timeout=8)
    return ok, out or err


def _windows_powershell(script: str) -> tuple[bool, str]:
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return False, "PowerShell not available"
    ok, out, err = _run([ps, "-NoProfile", "-Command", script], timeout=8)
    return ok, out or err


def _open_uri_fallback(uri: str) -> bool:
    u = str(uri or "").strip()
    if not u:
        return False
    try:
        from eli.utils.platform_compat import open_app_uri, open_url
        if u.startswith("spotify:") or "open.spotify.com" in u.lower():
            if spotify_launch_if_needed():
                time.sleep(0.6)
            return bool(open_app_uri(u))
        if open_app_uri(u):
            return True
        return bool(open_url(u))
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return False


def _android_open_uri(uri: str) -> bool:
    if shutil.which("termux-open-url"):
        ok, _, _ = _run(["termux-open-url", uri], timeout=5)
        if ok:
            return True
    if shutil.which("am"):
        ok, _, _ = _run(
            ["am", "start", "-a", "android.intent.action.VIEW", "-d", uri],
            timeout=5,
        )
        return ok
    return _open_uri_fallback(uri)


def spotify_launch_if_needed() -> bool:
    """Ensure Spotify is running; launch via platform conventions if not."""
    if spotify_running():
        return True
    try:
        from eli.execution.media_runtime import open_spotify
        msg = open_spotify()      # it waits for Spotify to come up (spotify_wait_launched)
        return "Could not open" not in str(msg) and "has not come up" not in str(msg)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if pc.MACOS:
        ok, _ = _macos_osascript('tell application "Spotify" to launch')
        return ok
    if pc.WINDOWS:
        return _open_uri_fallback("spotify:")
    if pc.ANDROID:
        return _android_open_uri("spotify:")
    return False


# How long a launched Spotify has to come up (register its MPRIS name, or show as running elsewhere).
# Until a launch has been timed on this machine it gets FIRST_LAUNCH_ALLOWANCE_S, the wait the play
# paths have always given it; after that LAUNCH_MARGIN times the slowest launch seen here, since the
# slowest start (a cold disk, a login rush) is the one that comes again.
FIRST_LAUNCH_ALLOWANCE_S = 8.0
LAUNCH_MARGIN = 2.0


def _launch_record():
    from eli.core.paths import get_paths
    return get_paths().artifacts_dir / "runtime" / "launch_times.json"


def launch_deadline(app: str = "spotify") -> float:
    """Seconds to wait for `app` to come up after it is launched, from its launches timed here."""
    try:
        longest = float((json.loads(_launch_record().read_text(encoding="utf-8")).get(app) or {}).get("longest") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        longest = 0.0
    return LAUNCH_MARGIN * longest if longest > 0 else FIRST_LAUNCH_ALLOWANCE_S


def note_launch(app: str, seconds: float) -> None:
    """Keep how long a launch took to come up, for the next deadline."""
    path = _launch_record()
    try:
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        record = {}
    seen = dict(record.get(app) or {})
    seen.update({"longest": max(float(seen.get("longest") or 0.0), float(seconds)), "last": float(seconds),
                 "launches": int(seen.get("launches") or 0) + 1})
    record[app] = seen
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    except OSError:
        log.debug("launch time not kept", exc_info=True)


def spotify_ready() -> bool:
    """Spotify can take commands: on Linux its MPRIS name is on the session bus (a process alone is not
    enough: commands sent before the name is there go nowhere); elsewhere its process runs."""
    if pc.LINUX and shutil.which("playerctl"):
        ok, out, _ = _run(["playerctl", "--list-all"], timeout=3)
        return ok and any(line.strip().lower().startswith("spotify") for line in out.splitlines())
    return spotify_running()


def spotify_wait_launched(started: float | None = None) -> bool:
    """After a launch: True once Spotify is ready, within the deadline its past launches set; the time
    it took is kept. False when it did not come up in time."""
    began = time.monotonic() if started is None else float(started)
    deadline = began + launch_deadline("spotify")
    while True:
        if spotify_ready():
            note_launch("spotify", time.monotonic() - began)
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def spotify_running() -> bool:
    if pc.ANDROID:
        return is_process_running("com.spotify.music") or is_process_running("spotify")
    return is_process_running("spotify") or is_process_running("Spotify")


def spotify_search_type_and_play(query: str, *, launch: bool = True) -> bool:
    """Drive Spotify's own search box directly — type into it and play the top
    hit — instead of scraping open.spotify.com (confirmed to return a JS shell
    with no server-rendered track data on a real fetch) or blindly opening a
    search URI with nothing selected for playerctl's play to act on.

    Uses Spotify's documented desktop shortcut (Ctrl+L jumps to search) then
    Down+Enter to select the first suggestion, the same two keystrokes a
    person would use. Refuses to type anything unless active_window_matches()
    independently confirms Spotify actually has input focus — a prior version
    trusted focus_app()'s own window-raise report, and on a focus race that
    sent the query straight into whatever window the user was really looking
    at (ELI's own chat box, in one reported case) instead of Spotify.

    Success is verified against live playerctl metadata
    (track_query_matches_now_playing), not just spotify_play()'s "something
    is playing" status — status alone can't tell a resumed stale track from
    the one just requested. Spotify's search suggestions are also a live,
    debounced network call: if Down+Enter fires before they've populated it
    selects nothing, so one retry re-opens search and re-types rather than
    blindly re-pressing Down+Enter into whatever state that first, unverified
    attempt left the UI in.
    """
    from eli.system.portable_app_control import (
        active_window_matches, display_server, focus_app, window_exists)
    from eli.utils.platform_compat import key_press, type_text

    if launch and not spotify_launch_if_needed():
        return False
    spotify_wait_running(timeout=8.0)
    # Process existing isn't window existing yet — Electron apps are slow to map. Under Wayland
    # the X tools see only XWayland windows: a native Wayland Spotify never appears to them, so
    # nothing could focus it or confirm focus and the wait would only add delay.
    _win_deadline = time.monotonic() + (3.0 if display_server() == "wayland" else 12.0)
    while time.monotonic() < _win_deadline and not window_exists("spotify"):
        time.sleep(0.3)
    if display_server() == "wayland" and not window_exists("spotify"):
        log.debug("[SPOTIFY] native Wayland window: keystrokes cannot be confirmed, not typing")
        return False

    def _attempt() -> bool:
        focus_app("spotify")
        time.sleep(0.4)
        if not active_window_matches("spotify"):
            log.debug("[SPOTIFY] window focus not confirmed, refusing to type blindly")
            return False
        time.sleep(0.2)
        if not key_press("ctrl+l"):
            return False
        time.sleep(0.3)
        if not active_window_matches("spotify"):
            log.debug("[SPOTIFY] lost focus before typing, aborting")
            return False
        if not type_text(query):
            return False
        time.sleep(1.6)
        key_press("Down")
        time.sleep(0.2)
        key_press("Return")
        time.sleep(0.6)
        return spotify_play() and track_query_matches_now_playing(query)

    if _attempt():
        return True
    log.debug("[SPOTIFY] first attempt unconfirmed, retrying once")
    return _attempt()


def _session_bus_reachable() -> bool:
    ok, _, _ = _run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus",
                     "/org/freedesktop/DBus", "org.freedesktop.DBus.GetId"], timeout=3)
    return ok


def spotify_play_top_search_result(query: str, timeout: float = 10.0, *, launch: bool = True) -> bool:
    """Have Spotify play its top search result for `query`; True once the song asked for plays.

    Over MPRIS, OpenUri("spotify:search:<query>") makes the desktop client play the top result
    (the https://open.spotify.com/search/... form only shows the results). No window focus or
    keystrokes are involved, so it works on Wayland, where nothing can confirm which window has
    focus, and on X11 alike. Linux only: elsewhere a search URI opens the results without playing.
    """
    q = str(query or "").strip()
    if not q or not pc.LINUX or not shutil.which("dbus-send") or not _session_bus_reachable():
        return False
    if launch and not spotify_launch_if_needed():
        return False
    if not spotify_wait_running(timeout=8.0):
        return False
    deadline = time.monotonic() + timeout
    sent_at, resent = None, False
    while time.monotonic() < deadline:
        # The client registers on D-Bus a moment after its process starts (sending fails until
        # then), and a cold start may ignore the first request while its interface loads.
        now = time.monotonic()
        if sent_at is None:
            if spotify_open_uri(f"spotify:search:{q}"):
                sent_at = now
        elif not resent and now - sent_at > timeout / 2:
            resent = spotify_open_uri(f"spotify:search:{q}") or True
        time.sleep(0.5)
        head, artist, title = spotify_live_meta()
        if head.startswith("▶") and query_matches_track(q, artist, title):
            return True
    return False


def spotify_open_uri(uri: str) -> bool:
    """Open a spotify: or https://open.spotify.com/ URI in the Spotify app."""
    u = str(uri or "").strip()
    if not u:
        return False

    if pc.LINUX and shutil.which("dbus-send"):
        ok, _, _ = _run([
            "dbus-send", "--print-reply",
            "--dest=org.mpris.MediaPlayer2.spotify",
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player.OpenUri",
            f"string:{u}",
        ], timeout=5)
        if ok:
            return True

    if pc.MACOS:
        safe = u.replace('"', '\\"')
        ok, _ = _macos_osascript(
            f'tell application "Spotify"\n'
            f'  if not running then launch\n'
            f'  open location "{safe}"\n'
            f'end tell'
        )
        if ok:
            return True
        return _open_uri_fallback(u)

    if pc.WINDOWS:
        if _open_uri_fallback(u):
            return True
        ok, _ = _windows_powershell(f'Start-Process "{u.replace(chr(34), "")}"')
        return ok

    if pc.ANDROID:
        return _android_open_uri(u)

    return _open_uri_fallback(u)


def spotify_search(query: str, prefer: str | None = None) -> bool:
    raw = str(query or "").strip()
    if not raw:
        return False
    spotify_launch_if_needed()
    time.sleep(0.4)
    q = urllib.parse.quote(raw)
    tab = {
        "playlist": "playlists", "playlists": "playlists",
        "album": "albums", "albums": "albums",
        "artist": "artists", "artists": "artists",
        "track": "tracks", "tracks": "tracks",
    }.get(prefer or "")
    if tab:
        if spotify_open_uri(f"spotify:search:{raw}/{tab}"):
            return True
        if spotify_open_uri(f"https://open.spotify.com/search/{q}/{tab}"):
            return True
    if spotify_open_uri(f"spotify:search:{raw}"):
        return True
    return spotify_open_uri(f"spotify:search:{q}")


def _spotify_scrape_uri(name: str, kind: str) -> str | None:
    """Resolve a playlist/album/artist name to spotify:<kind>:<id> via search HTML."""
    import re as _re
    import urllib.request as _ureq

    q = str(name or "").strip()
    if not q:
        return None
    plural = {"playlist": "playlists", "album": "albums", "artist": "artists",
              "track": "tracks"}.get(kind, kind)
    url = f"https://open.spotify.com/search/{urllib.parse.quote(q)}/{plural}"
    patterns = {
        "playlist": (
            r'"uri"\s*:\s*"spotify:playlist:([A-Za-z0-9]+)"',
            r'spotify:playlist:([A-Za-z0-9]+)',
        ),
        "album": (
            r'"uri"\s*:\s*"spotify:album:([A-Za-z0-9]+)"',
            r'spotify:album:([A-Za-z0-9]+)',
        ),
        "artist": (
            r'"uri"\s*:\s*"spotify:artist:([A-Za-z0-9]+)"',
            r'spotify:artist:([A-Za-z0-9]+)',
        ),
        "track": (
            r'"uri"\s*:\s*"spotify:track:([A-Za-z0-9]+)"',
            r'spotify:track:([A-Za-z0-9]+)',
        ),
    }.get(kind, ())
    try:
        req = _ureq.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
        with _ureq.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        for pat in patterns:
            m = _re.search(pat, html)
            if m:
                return f"spotify:{kind}:{m.group(1)}"
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return None


def spotify_resolve_playlist_uri(name: str) -> str | None:
    return _spotify_scrape_uri(name, "playlist")


def spotify_resolve_album_uri(name: str, artist: str | None = None) -> str | None:
    query = f"{name} {artist}".strip() if artist else name
    return _spotify_scrape_uri(query, "album")


def spotify_resolve_artist_uri(name: str) -> str | None:
    return _spotify_scrape_uri(name, "artist")


def spotify_open_liked_songs() -> bool:
    """Open the user's Liked Songs collection — try every URI form Spotify accepts."""
    uris = (
        "spotify:collection:tracks",
        "https://open.spotify.com/collection/tracks",
    )
    for uri in uris:
        if spotify_open_uri(uri):
            return True
    if pc.LINUX and shutil.which("playerctl"):
        ok, _, _ = _run(["playerctl", "-p", "spotify", "open", "spotify:collection:tracks"])
        if ok:
            return True
    return False


def shuffle_wanted(value: Any) -> bool | None:
    """The shuffle state asked for: True for on, False for off, None to toggle (nothing named)."""
    if isinstance(value, bool):
        return value
    word = str(value or "").strip().lower()
    return {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}.get(word)


def _mpris_shuffle(player: str) -> bool | None:
    """A player's MPRIS Shuffle as it reports it (playerctl), or None when it does not say."""
    ok, out, _ = _run(["playerctl", "-p", player, "shuffle"], timeout=5)
    word = out.strip().lower() if ok else ""
    return word in ("on", "true") if word in ("on", "off", "true", "false") else None


def _shuffle_result(player: str, wanted: bool | None, now: bool | None) -> dict[str, Any]:
    """What a shuffle change came to, from the state the player reports afterwards."""
    if now is None:
        msg = f"🔀 Shuffle {'set ' + ('on' if wanted else 'off') if wanted is not None else 'toggled'} — {player}; " \
              f"the player did not say whether shuffle is on now."
        return {"ok": True, "player": player, "shuffle": None, "content": msg, "response": msg}
    if wanted is not None and now is not wanted:
        msg = f"Shuffle is still {'on' if now else 'off'} on {player}: it did not take the change."
        return {"ok": False, "player": player, "shuffle": now, "error": "shuffle unchanged",
                "content": msg, "response": msg}
    msg = f"🔀 Shuffle {'on' if now else 'off'} — {player}"
    return {"ok": True, "player": player, "shuffle": now, "content": msg, "response": msg}


def spotify_shuffle(player: str | None = None, mode: Any = "Toggle") -> dict[str, Any]:
    """Toggle shuffle, or set it with mode on/off (shuffle_wanted). The result's `shuffle` and words are
    the state the player reports afterwards, read back, not the command that was sent."""
    p = (player or "spotify").strip() or "spotify"
    wanted = shuffle_wanted(mode)
    if pc.LINUX and shutil.which("playerctl"):
        ok, out, err = _run(["playerctl", "-p", p, "shuffle",
                             "Toggle" if wanted is None else ("On" if wanted else "Off")], timeout=5)
        if ok:
            return _shuffle_result(p, wanted, _mpris_shuffle(p))
        return {"ok": False, "player": p, "error": err or "shuffle failed",
                "content": f"Couldn't change shuffle on {p}.",
                "response": f"Couldn't change shuffle on {p}."}
    if pc.MACOS or pc.WINDOWS:
        # the running player the OS knows about: Spotify or Music on macOS, any app in Windows' media session
        from eli.integrations.media import system_session
        if wanted is not None:
            before = system_session.shuffle_state()
            if before and before["shuffle"] is wanted:
                return _shuffle_result(before["player"], wanted, wanted)
        done = system_session.toggle_shuffle()
        if done:
            return _shuffle_result(done["player"], wanted, done["shuffle"])
        return {"ok": False, "error": "no player",
                "content": "Nothing is playing, so there is no shuffle to change.",
                "response": "Nothing is playing, so there is no shuffle to change."}
    return {"ok": False, "error": "shuffle unavailable",
            "content": "Shuffle control isn't available on this system.",
            "response": "Shuffle control isn't available on this system."}


def spotify_set_loop(player: str | None = None, mode: str | None = None) -> dict[str, Any]:
    """Set or cycle repeat/loop mode (None, Track, Playlist)."""
    p = (player or "spotify").strip() or "spotify"
    if (pc.MACOS or pc.WINDOWS) and mode is None:
        from eli.integrations.media import system_session
        done = system_session.cycle_repeat()
        if done:
            msg = f"🔁 Repeat {done['repeat']} — {done['player']}"
            return {"ok": True, "player": done["player"], "content": msg, "response": msg}
        return {"ok": False, "error": "no player",
                "content": "Nothing is playing, so there is no repeat to change.",
                "response": "Nothing is playing, so there is no repeat to change."}
    current = spotify_loop_status()
    if mode is None:
        mode = {"None": "Track", "Track": "Playlist", "Playlist": "None"}.get(current, "Track")
    if pc.LINUX and shutil.which("playerctl"):
        ok, out, err = _run(["playerctl", "-p", p, "loop", mode], timeout=5)
        if ok:
            state = out or mode
            msg = f"🔁 Repeat {state.lower()} — {p}"
            return {"ok": True, "player": p, "content": msg, "response": msg}
        return {"ok": False, "player": p, "error": err or "loop failed",
                "content": f"Couldn't set repeat on {p}.",
                "response": f"Couldn't set repeat on {p}."}
    if pc.MACOS:
        script = {
            "None": 'set repeating to false',
            "Track": 'set repeating to true',
            "Playlist": 'set repeating to true',
        }.get(mode, 'set repeating to false')
        ok, _ = _macos_osascript(f'tell application "Spotify" to {script}')
        if ok:
            msg = f"🔁 Repeat {mode.lower()} — Spotify"
            return {"ok": True, "player": "spotify", "content": msg, "response": msg}
    return {"ok": False, "error": "repeat unavailable",
            "content": "Repeat control isn't available on this system.",
            "response": "Repeat control isn't available on this system."}


def spotify_play() -> bool:
    """Start/resume Spotify; return True only when playback is confirmed."""
    if pc.LINUX:
        if shutil.which("playerctl"):
            _run(["playerctl", "-p", "spotify", "play"], timeout=5)
        if shutil.which("dbus-send"):
            _run([
                "dbus-send", "--print-reply",
                "--dest=org.mpris.MediaPlayer2.spotify",
                "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2.Player.Play",
            ], timeout=5)
        return spotify_wait_playing(timeout=6.0)

    if pc.MACOS:
        ok, _ = _macos_osascript('tell application "Spotify" to play')
        if ok:
            return spotify_wait_playing(timeout=6.0)
        try:
            from eli.integrations.mpris import playerctl_backend as pb
            r = pb._macos_media("play")
            return bool(r.get("ok")) and spotify_wait_playing(timeout=4.0)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return False

    if pc.WINDOWS:
        # The media key is play/pause in one: pressed on a Spotify that is already playing, it pauses.
        if spotify_is_playing():
            return True
        try:
            from eli.integrations.media import system_session
            if "spotify" in str((system_session.info() or {}).get("player") or "").lower() \
                    and system_session.command("play"):
                return spotify_wait_playing(timeout=4.0)
            from eli.integrations.mpris import playerctl_backend as pb
            r = pb._windows_media_vk("play")
            if r.get("ok"):
                return spotify_wait_playing(timeout=4.0)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return False

    if pc.ANDROID:
        return _android_open_uri("spotify:")

    return False


def spotify_is_playing() -> bool:
    if pc.LINUX and shutil.which("playerctl"):
        ok, out, _ = _run(["playerctl", "-p", "spotify", "status"], timeout=5)
        if ok and "playing" in out.lower():
            return True
        return False
    return spotify_live_track()["status"] == "playing"


def spotify_wait_playing(timeout: float = 8.0, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if spotify_is_playing():
            return True
        time.sleep(max(0.05, interval))
    return False


def spotify_wait_running(timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if spotify_running():
            return True
        time.sleep(0.5)
    return False


# Words of a request that are not part of a name: articles and joining words, and the words that
# say what kind of thing is wanted. "songs by veridian and red tamsin" started the right track
# ("Veridian — North & South (feat. Red tamsin)") and it was refused, "songs" and "and" not being in it.
_TRACK_MATCH_STOPWORDS = {
    "the", "a", "an", "by", "of", "feat", "ft", "featuring", "on", "spotify", "and", "with", "from", "some", "any",
    "song", "songs", "track", "tracks", "tune", "tunes", "music", "stuff", "something", "anything",
}


def track_query_matches_now_playing(query: str, player: str = "spotify") -> bool:
    """Compare a requested "play X by Y" query against live playerctl metadata.

    spotify_play()/spotify_is_playing() only confirm *something* is playing —
    not that it's the track just requested. A resumed stale track (whatever
    was queued before this request) reports the exact same "Playing" status
    as a genuinely newly-selected one, so anything claiming success for a
    specific song must check this, not just the transport status.
    """
    _, artist, track = spotify_live_meta(player)
    return query_matches_track(query, artist, track)


def _match_tokens(text: str) -> set[str]:
    # "The Notorious B.I.G." is "notorious big", "Don't" is "dont", "Beyoncé" is "beyonce"
    import unicodedata
    low = unicodedata.normalize("NFKD", str(text or "").lower())
    low = "".join(ch for ch in low if not unicodedata.combining(ch))
    low = re.sub(r"(?<=\w)[.'’](?=\w)", "", low)
    return {t for t in re.findall(r"\w+", low) if t not in _TRACK_MATCH_STOPWORDS}


def query_matches_track(query: str, artist: str, title: str) -> bool:
    """Most of the words asked for are in the playing track, and some are in its title, unless
    only the artist was asked for.

    One shared word was enough before, so "trouble orlo" accepted any Orlo track that
    happened to be loaded.
    """
    if not f"{artist}{title}".strip():
        return False
    wanted = _match_tokens(query)
    if not wanted:
        return True
    in_title = _match_tokens(title)
    by = _match_tokens(artist)
    if wanted <= by:
        return True  # an artist was asked for and nothing else: any track of theirs is it
    found = wanted & (by | in_title)
    return len(found) * 4 >= len(wanted) * 3 and bool(wanted & in_title)


def _same_word(a: str, b: str) -> bool:
    """One word, allowing a possessive or plural "s" said or left off ("veridians" for Veridian)."""
    return a == b or (len(a) > 2 and a.endswith("s") and a[:-1] == b) or (len(b) > 2 and b.endswith("s") and b[:-1] == a)


def _all_words_in(wanted: str, have: str) -> bool:
    want, got = _match_tokens(wanted), _match_tokens(have)
    return bool(want) and bool(got) and all(any(_same_word(w, h) for h in got) for w in want)


def album_matches(album: str, artist: str, now: dict) -> bool:
    """The playing track is from the album asked for: every word of the album's name is in the
    track's album (an edition may add "(Remastered)"), and the artist, when named, is its artist.
    Judged by the player's album (xesam:album), not by the words of the request in a track title."""
    if not _all_words_in(album, str((now or {}).get("album") or "")):
        return False
    return not artist or _all_words_in(artist, str((now or {}).get("artist") or ""))


def spotify_loop_status() -> str:
    if pc.LINUX and shutil.which("playerctl"):
        ok, out, _ = _run(["playerctl", "-p", "spotify", "loop"], timeout=5)
        if ok:
            return out
    if pc.MACOS:
        ok, out = _macos_osascript(
            'tell application "Spotify" to return repeating'
        )
        if ok:
            low = out.lower()
            if "one" in low or "track" in low:
                return "Track"
            if "all" in low or "playlist" in low:
                return "Playlist"
            if "false" in low or "off" in low:
                return "None"
    return ""


def spotify_clear_track_repeat() -> bool:
    if spotify_loop_status() != "Track":
        return False
    if pc.LINUX and shutil.which("playerctl"):
        _run(["playerctl", "-p", "spotify", "loop", "None"], timeout=5)
        return spotify_loop_status() != "Track"
    if pc.MACOS:
        ok, _ = _macos_osascript(
            'tell application "Spotify" to set repeating to false'
        )
        return ok
    return False


_STATUS_HEAD = {"playing": "▶ Playing", "paused": "⏸ Paused", "stopped": "⏹ Stopped"}


def spotify_live_track(player: str = "spotify") -> dict[str, str]:
    """What the player says it is doing: {"status" (playing/paused/stopped/...), "artist", "title",
    "album"}, empty values when nothing could be read. MPRIS on Linux, the OS media session on
    Windows (playerctl_backend chooses), AppleScript for Spotify on macOS. Windows used to be asked
    through playerctl only, which it does not have, so no Spotify play there was ever confirmed."""
    empty = {"status": "", "artist": "", "title": "", "album": ""}
    if pc.MACOS:
        ok, out = _macos_osascript(
            'tell application "Spotify"\n'
            '  if not running then return ""\n'
            '  set t to name of current track\n'
            '  set a to artist of current track\n'
            '  set b to album of current track\n'
            '  set s to player state as string\n'
            '  return s & "|" & a & "|" & t & "|" & b\n'
            'end tell'
        )
        if ok and "|" in out:
            status, artist, title, album = (list(p.strip() for p in out.split("|", 3)) + ["", "", ""])[:4]
            return {"status": status.lower(), "artist": artist, "title": title, "album": album}
        return empty
    try:
        from eli.integrations.mpris import playerctl_backend as _pcb
        st = _pcb.get_player_status(player)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
        return empty
    if not st.get("ok"):
        return empty
    # Without MPRIS the OS reports whichever app holds its media session: only Spotify's is Spotify's.
    if not pc.LINUX and player and player.lower() not in str(st.get("player") or "").lower():
        return empty
    return {"status": str(st.get("status") or "").lower(), "artist": str(st.get("artist") or "").strip(),
            "title": str(st.get("title") or "").strip(), "album": str(st.get("album") or "").strip()}


def spotify_live_meta(player: str = "spotify") -> tuple[str, str, str]:
    """Return (status_head, artist, title) from the live player when possible."""
    now = spotify_live_track(player)
    if not (now["status"] or now["artist"] or now["title"]):
        return "", "", ""
    status = now["status"]
    head = _STATUS_HEAD.get(status) or (f"⏺ {status.capitalize()}" if status else "▶ Playing")
    return head, now["artist"], now["title"]


# ── shuffle, held off while one thing is asked for ───────────────────────────────

def spotify_shuffle_state() -> bool | None:
    """Whether Spotify's shuffle is on; None when it cannot be read."""
    if pc.LINUX and shutil.which("playerctl"):
        return _mpris_shuffle("spotify")
    if pc.MACOS:
        ok, out = _macos_osascript('tell application "Spotify" to return shuffling')
        return (out.strip().lower() == "true") if ok and out.strip().lower() in ("true", "false") else None
    if pc.WINDOWS:
        from eli.integrations.media import system_session
        now = system_session.info() or {}
        if "spotify" in str(now.get("player") or "").lower() and now.get("shuffle") is not None:
            return bool(now["shuffle"])
    return None


def spotify_set_shuffle(on: bool) -> bool:
    """Set Spotify's shuffle on or off; True when it is now as asked."""
    if pc.LINUX and shutil.which("playerctl"):
        _run(["playerctl", "-p", "spotify", "shuffle", "On" if on else "Off"], timeout=5)
    elif pc.MACOS:
        _macos_osascript(f'tell application "Spotify" to set shuffling to {"true" if on else "false"}')
    elif pc.WINDOWS:
        from eli.integrations.media import system_session
        if spotify_shuffle_state() not in (None, bool(on)):
            system_session.toggle_shuffle()
    return spotify_shuffle_state() is bool(on)


class spotify_shuffle_held_off:
    """With shuffle on, Spotify starts some other track of what it opened, not the one asked for: the
    track check rightly refused it and the request failed after half a minute, then played in a
    second once shuffle was off. So shuffle is off while one particular thing is started, and put
    back as it was after (the track that started keeps playing). `was_on` says what was found."""

    def __enter__(self):
        self.was_on = spotify_shuffle_state()
        self.turned_off = bool(self.was_on) and spotify_set_shuffle(False)
        return self

    def __exit__(self, *exc):
        if self.turned_off:
            spotify_set_shuffle(True)
        return False


def mpv_socket_path() -> str:
    env = os.environ.get("ELI_YOUTUBE_MPV_IPC")
    if env:
        return env
    if pc.WINDOWS:
        return __import__("eli.core.paths", fromlist=["pipe_path"]).pipe_path("youtube_mpv")
    from eli.core.paths import temp_path
    return str(temp_path("youtube_mpv.sock"))


def _is_windows_pipe(path: str) -> bool:
    p = str(path or "")
    return p.startswith("\\\\.\\pipe\\") or p.startswith(r"\\.\pipe")


def mpv_alive(sock_path: str | None = None) -> bool:
    p = sock_path or mpv_socket_path()
    if _is_windows_pipe(p):
        try:
            with open(p, "r+b", buffering=0):
                return True
        except Exception:
            return False
    if not os.path.exists(p):
        return False
    import socket as _sock
    try:
        with _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(p)
        return True
    except Exception:
        return False


def mpv_ipc_send(
    command: list[Any],
    *,
    sock_path: str | None = None,
    want_response: bool = False,
) -> Any:
    """Send one mpv JSON IPC command. Returns True/data or False/None."""
    p = sock_path or mpv_socket_path()
    payload = json.dumps({"command": command}) + "\n"

    if _is_windows_pipe(p):
        try:
            with open(p, "r+b", buffering=0) as pipe:
                pipe.write(payload.encode())
                if want_response:
                    buf = pipe.read(8192).decode("utf-8", "ignore")
                    for line in buf.splitlines():
                        try:
                            j = json.loads(line)
                            if isinstance(j, dict) and "data" in j:
                                return j["data"]
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                    return None
                return True
        except Exception:
            return None if want_response else False

    if not os.path.exists(p):
        return None if want_response else False

    import socket as _sock
    try:
        with _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(p)
            s.sendall(payload.encode())
            if want_response:
                buf = s.recv(8192).decode("utf-8", "ignore")
                for line in buf.splitlines():
                    try:
                        j = json.loads(line)
                        if isinstance(j, dict) and "data" in j:
                            return j["data"]
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                return None
        return True
    except Exception:
        return None if want_response else False
