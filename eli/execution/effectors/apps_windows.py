"""Apps & windows effector — launch/open apps, browser, settings, and window tiling.

Extracted verbatim from executor_enhanced.py (v3 modularisation). Security + subprocess
primitives come from eli.execution.effectors._base.
"""
from __future__ import annotations

import math
import os
import re
import shlex
import subprocess
from typing import Any, Dict
from eli.utils.platform_compat import open_file
from eli.utils.log import get_logger
log = get_logger(__name__)

# window-tiling helpers reference stdlib modules under prefixed aliases
_eli_tile_math = math
_eli_tile_re = re
_eli_tile_subprocess = subprocess

from eli.execution.effectors._base import _allowed_apps_set, _run  # noqa: F401

# ── app state ──
_APP_ALIASES = {
    "mail": "thunderbird",
    "email": "thunderbird",
    "e-mail": "thunderbird",
    "thunderbird": "thunderbird",
    "thunderbirds": "thunderbird",
    "thunder birds": "thunderbird",
    "tundra bird": "thunderbird",
    "tundrabird": "thunderbird",
    "browser": "firefox",
    "mozilla": "firefox",
    "firefox": "firefox",
}


def open_audio_settings() -> Dict[str, Any]:
    for cmd in (["pavucontrol"], ["gnome-control-center", "sound"]):
        r = _run(cmd, timeout=5)
        if r.get("ok"):
            msg = "Audio settings opened."
            return {"ok": True, "action": "OPEN_AUDIO_SETTINGS", "cmd": cmd, "content": msg, "response": msg}
    msg = "No audio settings app found (pavucontrol / gnome-control-center)."
    return {"ok": False, "action": "OPEN_AUDIO_SETTINGS", "error": msg, "content": msg, "response": msg}


def open_file_system(path: str = "~") -> Dict[str, Any]:
    from eli.execution.effectors.system_helpers import open_file_system as _ofs
    return _ofs(path)


def open_browser(url: str = "https://duckduckgo.com", urls: list = None) -> Dict[str, Any]:
    """Open one or more URLs in the default browser.

    One implementation (system_helpers), which reports a browser that did not open instead of
    claiming success; this copy used to say "Opened browser" regardless.
    """
    from eli.execution.effectors.system_helpers import open_browser as _ob
    return _ob(url, urls=urls)


def _normalize_app(spoken: str) -> str:
    """Normalize application names using aliases."""
    s = (spoken or "").strip().lower()
    s = " ".join(s.split())
    return _APP_ALIASES.get(s, s)


def _open_app_with_timeout(argv, timeout=30):
    from eli.execution.effectors.system_helpers import open_app_with_timeout
    return open_app_with_timeout(argv, timeout=timeout)


def _open_app_builtin(app_name: str) -> dict:
    app = _normalize_app(app_name)
    if not app:
        return {"ok": False, "error": "empty app_name"}

    allowed = _allowed_apps_set()
    if allowed is not None and app not in allowed:
        return {"ok": False, "error": f"app not allowed: {app} (set ELI_ALLOWED_APPS)"}

    # Allow per-app override command
    env_key = "ELI_APP_CMD_" + re.sub(r"[^A-Z0-9]+", "_", app.upper())
    override = (os.environ.get(env_key) or "").strip()

    if override:
        argv = shlex.split(override)
    else:
        # sensible defaults
        if app == "thunderbird":
            argv = ["thunderbird"]
        elif app == "firefox":
            argv = ["firefox"]
        elif app == "spotify":
            SPOTIFY_CMD = os.environ.get("ELI_SPOTIFY_CMD", "").strip() or "spotify"
            argv = shlex.split(SPOTIFY_CMD) if SPOTIFY_CMD else ["spotify"]
        else:
            # last resort: try running by name
            argv = [app]

    try:
        return _open_app_with_timeout(argv)
    except Exception as e:
        return {"ok": False, "error": str(e), "app": app, "cmd": argv}


def _eli_open_named_site(args):
    """OPEN_URL for a site known by name ("nasa website") or pointed at ("that website"): the
    address comes from the conversation or a web search, and the reply says which."""
    from eli.runtime import site_address

    def _say(msg):
        return {"ok": False, "action": "OPEN_URL", "error": msg, "content": msg, "response": msg}

    if args.get("refers_to"):
        url = site_address.referred_to(int(args.get("which") or 1))
        if not url:
            return _say(f"No web address has come up in this conversation for “{args['refers_to']}” to point at. "
                        "Give me the address, or the name of the site.")
        return _eli_open_url_action(url)
    name = str(args.get("site") or "").strip()
    found = site_address.named(name)
    if not found.get("url"):
        return _say(f"I have no address for the {name} website: {found.get('why')}. Give me the address, "
                    f"or say “search for {name}” and I will open a search for it.")
    opened = _eli_open_url_action(found["url"])
    if opened.get("ok"):
        opened["content"] = opened["response"] = f"{opened['content']} (the {name} site, from {found['from']})"
    return opened


def _eli_open_url_action(raw_url=None, *, query=None):
    """
    Safe URL opener for OPEN_URL.

    Rules:
    - Accepts http/https URLs.
    - Accepts bare domains like github.com and normalizes to https://github.com.
    - Blocks unsupported schemes such as file:, javascript:, data:, ftp:, etc.
    - Uses xdg-open without shell=True.
    """
    from urllib.parse import urlparse, quote_plus
    import shutil
    import subprocess
    import webbrowser

    value = str(raw_url or "").strip()

    if not value and query:
        value = "https://duckduckgo.com/?q=" + quote_plus(str(query).strip())

    if not value:
        msg = "No URL was provided."
        return {
            "ok": False,
            "action": "OPEN_URL",
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }

    # Allow common bare domains: github.com, openai.com/docs, etc.
    if "://" not in value:
        if " " in value or "." not in value:
            msg = f"Not a valid URL or bare domain: {value!r}"
            return {
                "ok": False,
                "action": "OPEN_URL",
                "url": value,
                "error": msg,
                "content": f"⚡ Could not open URL. {msg}",
                "response": f"⚡ Could not open URL. {msg}",
            }
        value = "https://" + value

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        msg = f"Blocked unsupported URL scheme: {parsed.scheme!r}"
        return {
            "ok": False,
            "action": "OPEN_URL",
            "url": value,
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }

    if not parsed.netloc:
        msg = f"URL is missing a domain: {value!r}"
        return {
            "ok": False,
            "action": "OPEN_URL",
            "url": value,
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }

    try:
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen(
                [opener, value],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            webbrowser.open(value, new=2)

        msg = f"Opened URL: {value}"
        return {
            "ok": True,
            "action": "OPEN_URL",
            "url": value,
            "content": msg,
            "response": msg,
        }

    except Exception as exc:
        msg = f"Failed to open URL: {exc!r}"
        return {
            "ok": False,
            "action": "OPEN_URL",
            "url": value,
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }


def _eli_tile_run(cmd, timeout=2):
    try:
        return _eli_tile_subprocess.run(
            cmd,
            text=True,
            stdout=_eli_tile_subprocess.PIPE,
            stderr=_eli_tile_subprocess.PIPE,
            timeout=timeout,
        )
    except Exception as e:
        class _R:
            returncode = 999
            stdout = ""
            stderr = str(e)
        return _R()


def _eli_tile_current_desktop():
    p = _eli_tile_run(["wmctrl", "-d"])
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        if "*" in line:
            try:
                return int(line.split()[0])
            except Exception:
                return None
    return None


def _eli_tile_screen_size():
    p = _eli_tile_run(["xdotool", "getdisplaygeometry"])
    if p.returncode == 0:
        parts = p.stdout.strip().split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])

    p = _eli_tile_run(["xrandr", "--current"])
    if p.returncode == 0:
        m = _eli_tile_re.search(r"current\s+(\d+)\s+x\s+(\d+)", p.stdout)
        if m:
            return int(m.group(1)), int(m.group(2))

    return 1366, 768


def _eli_tile_xprop(wid):
    p = _eli_tile_run(["xprop", "-id", wid], timeout=1)
    return p.stdout if p.returncode == 0 else ""


def _eli_tile_visible_windows():
    p = _eli_tile_run(["wmctrl", "-lG", "-p"])
    if p.returncode != 0:
        return [], p.stderr.strip() or "wmctrl failed"

    curdesk = _eli_tile_current_desktop()
    wins = []

    for line in p.stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue

        wid, desk_s, pid_s, x_s, y_s, w_s, h_s, host, title = parts
        try:
            desk = int(desk_s)
            x, y, w, h = int(x_s), int(y_s), int(w_s), int(h_s)
        except Exception:
            continue

        if curdesk is not None and desk not in {curdesk, -1}:
            continue
        if w < 80 or h < 80:
            continue
        if not str(title or "").strip():
            continue

        xp = _eli_tile_xprop(wid)
        xp_low = xp.lower()

        # Skip hidden/minimized/taskbar-skipped/system surfaces.
        if "_net_wm_state_hidden" in xp_low:
            continue
        if "_net_wm_state_skip_taskbar" in xp_low:
            continue
        if "_net_wm_state_skip_pager" in xp_low:
            continue

        # Keep normal/dialog windows; skip docks, desktops, menus, tooltips, splash, etc.
        if "_net_wm_window_type" in xp_low:
            bad_types = (
                "_net_wm_window_type_desktop",
                "_net_wm_window_type_dock",
                "_net_wm_window_type_toolbar",
                "_net_wm_window_type_menu",
                "_net_wm_window_type_utility",
                "_net_wm_window_type_splash",
                "_net_wm_window_type_dropdown_menu",
                "_net_wm_window_type_popup_menu",
                "_net_wm_window_type_tooltip",
                "_net_wm_window_type_notification",
            )
            if any(t in xp_low for t in bad_types):
                continue

        wins.append({"id": wid, "desk": desk, "x": x, "y": y, "w": w, "h": h, "title": title})

    return wins, ""


def _eli_tile_parse_grid(args, count):
    args = args or {}
    grid = args.get("grid")
    cols = args.get("cols") or args.get("columns")
    rows = args.get("rows")

    if isinstance(grid, (list, tuple)) and len(grid) >= 2:
        cols = cols or grid[0]
        rows = rows or grid[1]

    try:
        cols = int(cols) if cols else 0
    except Exception:
        cols = 0
    try:
        rows = int(rows) if rows else 0
    except Exception:
        rows = 0

    if cols > 0 and rows > 0:
        return max(1, min(cols, 8)), max(1, min(rows, 8))

    if count <= 1:
        return 1, 1
    cols = int(_eli_tile_math.ceil(_eli_tile_math.sqrt(count)))
    rows = int(_eli_tile_math.ceil(count / cols))
    return max(1, cols), max(1, rows)


def _eli_tile_windows(args=None):
    wins, err = _eli_tile_visible_windows()
    if err:
        return {"ok": False, "action": "TILE_WINDOWS", "content": err, "response": err, "error": err}

    count = len(wins)
    if count == 0:
        msg = "No visible normal windows found to tile."
        return {"ok": False, "action": "TILE_WINDOWS", "content": msg, "response": msg, "count": 0}

    cols, rows = _eli_tile_parse_grid(args or {}, count)
    screen_w, screen_h = _eli_tile_screen_size()

    margin = int((args or {}).get("margin", 10) or 10)
    top_reserved = int((args or {}).get("top_reserved", 34) or 34)

    usable_x = margin
    usable_y = top_reserved + margin
    usable_w = max(300, screen_w - margin * 2)
    usable_h = max(240, screen_h - top_reserved - margin * 2)

    cell_w = max(180, usable_w // cols)
    cell_h = max(140, usable_h // rows)

    moved = 0
    for i, win in enumerate(wins[: cols * rows]):
        c = i % cols
        r = i // cols
        x = usable_x + c * cell_w
        y = usable_y + r * cell_h
        w = max(120, cell_w - margin)
        h = max(100, cell_h - margin)

        wid = win["id"]
        _eli_tile_run(["wmctrl", "-ir", wid, "-b", "remove,maximized_vert,maximized_horz"], timeout=1)
        p = _eli_tile_run(["wmctrl", "-ir", wid, "-e", f"0,{x},{y},{w},{h}"], timeout=2)
        if p.returncode == 0:
            moved += 1

    msg = f"Tiled {moved} visible window{'s' if moved != 1 else ''} into a {cols}×{rows} grid."
    if count > cols * rows:
        msg += f" {count - cols * rows} visible window(s) did not fit in the requested grid."

    return {
        "ok": moved > 0,
        "action": "TILE_WINDOWS",
        "content": msg,
        "response": msg,
        "count": moved,
        "visible_count": count,
        "grid": [cols, rows],
    }

__all__ = ['_APP_ALIASES', 'open_audio_settings', 'open_file_system', 'open_browser', '_normalize_app', '_open_app_with_timeout', '_open_app_builtin', '_eli_open_url_action', '_eli_tile_run', '_eli_tile_current_desktop', '_eli_tile_screen_size', '_eli_tile_xprop', '_eli_tile_visible_windows', '_eli_tile_parse_grid', '_eli_tile_windows']


# ── dispatch handlers (v3 registry) ──
from eli.execution.effectors.registry import register as _register


def _h_open_url(a, args):
    url = None
    query = None
    try:
        if isinstance(args, dict):
            url = (args.get("url") or args.get("target") or args.get("value")
                   or args.get("path") or args.get("text"))
            query = args.get("query")
    except Exception:
        url = None
        query = None
    if not url and isinstance(args, dict) and (args.get("site") or args.get("refers_to")):
        return _eli_open_named_site(args)
    return _eli_open_url_action(url, query=query)


def _h_open_communication_hub(a, args):
    try:
        for cmd in (["thunderbird"], ["evolution"], ["geary"]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Opened Communication Hub: {' '.join(cmd)}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue
        url = "https://mail.google.com"
        open_browser(url)
        msg = f"Opened Communication Hub: {url}"
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_open_network_browser(a, args):
    try:
        url = str((args or {}).get("url") or "https://duckduckgo.com").strip()
        open_browser(url)
        msg = f"Opened Network Browser: {url}"
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


for _action, _fn in {
    "OPEN_URL": _h_open_url,
    "OPEN_COMMUNICATION_HUB": _h_open_communication_hub,
    "OPEN_NETWORK_BROWSER": _h_open_network_browser,
}.items():
    _register(_action, _fn)
