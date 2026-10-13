"""System-open effector — open settings panels, browser, file manager, media hub."""
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess

from eli.utils.log import get_logger
from eli.execution.effectors.system_helpers import (
    open_app_with_timeout,
    open_browser,
    open_file_system,
)

log = get_logger(__name__)


def _h_open_audio_settings(a, args):
    try:
        candidates = [
            ["pavucontrol"],
            ["gnome-control-center", "sound"],
            ["gnome-control-center"],
            ["systemsettings5", "kcm_pulseaudio"],
            ["systemsettings5"],
            ["kcmshell5", "kcm_pulseaudio"],
            ["mate-volume-control"],
            ["xfce4-mixer"],
        ]
        for cmd in candidates:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Opened audio settings: {' '.join(cmd)}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue
        raise FileNotFoundError("No audio settings app found (pavucontrol / gnome-control-center / systemsettings5 / etc).")
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_open_browser(a, args):
    import re as _re2
    raw_q = (args.get("query") or args.get("url") or args.get("link") or "").lower()

    # YouTube with a search query → open YouTube search/watch URL in browser
    _yt_query = (args.get("query") or "").strip()
    if "youtube" in raw_q and _yt_query and "youtube.com" not in raw_q:
        try:
            from eli.integrations.media.youtube_playback import yt_browser_play_url
            yt_url = yt_browser_play_url(_yt_query)
            if _open_in_browser(yt_url):
                msg = f"Opened YouTube: {_yt_query}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    # Several sites asked for by name ("youtube and github"): each is opened.
    from eli.execution.app_aliases import WEBSITE_ALIASES as _sites
    _asked = [w.strip() for w in _re2.split(r"\s*(?:,|&|\band\b)\s*", raw_q) if w.strip()]
    found_urls = [_sites[w] for w in _asked if w in _sites] if len(_asked) > 1 else []
    if len(found_urls) > 1 and len(found_urls) == len(_asked):
        return open_browser(found_urls[0], urls=found_urls)
    url = str(args.get("url") or args.get("link") or "")
    if not url or not url.startswith("http"):
        q = (args.get("query") or "").strip()
        if q:
            import urllib.parse as _up
            # Safe search applies here too. The API path is filtered in
            # eli/plugins/web/plugin.py, but this opens a real search page the filter never
            # sees; kp=1 is DuckDuckGo's strict setting.
            try:
                from eli.plugins.web.plugin import _safe_search_enabled as _sse
                _kp = "&kp=1" if _sse() else ""
            except Exception:
                _kp = "&kp=1"
            url = "https://duckduckgo.com/?q=" + _up.quote_plus(q) + _kp
        else:
            url = "https://duckduckgo.com"
    return open_browser(url)


def _h_open_system_settings(a, args):
    try:
        candidates = [
            ["gnome-control-center"],
            ["systemsettings5"],
            ["xfce4-settings-manager"],
            ["mate-control-center"],
            ["cinnamon-settings"],
        ]
        for cmd in candidates:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Opened system settings: {' '.join(cmd)}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue
        raise FileNotFoundError("No settings app found")
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_open_power_settings(a, args):
    try:
        candidates = [
            ["gnome-control-center", "power"],
            ["systemsettings5", "kcm_energyinfo"],
            ["xfce4-power-manager-settings"],
        ]
        for cmd in candidates:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Opened power settings: {' '.join(cmd)}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue
        raise FileNotFoundError("No power settings app found")
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_open_file_system(a, args):
    try:
        raw_target = str(
            (args or {}).get("path")
            or (args or {}).get("target")
            or (args or {}).get("name")
            or ""
        ).strip()

        low_target = raw_target.lower()

        if low_target in {"trash", "/trash"}:
            probe = open_app_with_timeout(["xdg-open", "trash:///"])
            if probe.get("ok") or probe.get("spawned"):
                msg = "Opened folder: trash:///"
                return {"ok": True, "action": a, "content": msg, "response": msg, "probe": probe}
            return {"ok": False, "action": a, "error": probe.get("stderr") or probe.get("error") or "trash_open_failed",
                    "content": "Could not open trash.", "response": "Could not open trash."}

        if low_target in {"home", "/home", "home directory", "home folder",
                          "home dir", "my home", "my files", "files", "~", "~/",
                          "file manager", "file explorer", ""}:
            resolved = str(Path.home())
        else:
            cleaned = re.sub(r"\b(folder|directory|path)\b", "", raw_target, flags=re.I).strip()
            cleaned = cleaned.rstrip("/")

            candidates = []

            if raw_target.startswith("~/"):
                candidates.append(Path(raw_target).expanduser())

            if raw_target.startswith("/Desktop/") or raw_target.startswith("/desktop/"):
                suffix = raw_target.split("/", 2)[-1] if raw_target.count("/") >= 2 else ""
                candidates.append(Path.home() / "Desktop" / suffix)

            if raw_target.startswith("/Documents/") or raw_target.startswith("/documents/"):
                suffix = raw_target.split("/", 2)[-1] if raw_target.count("/") >= 2 else ""
                candidates.append(Path.home() / "Documents" / suffix)

            if raw_target.startswith("/"):
                candidates.append(Path(raw_target))

            if cleaned:
                candidates.append(Path.home() / cleaned)
                candidates.append(Path.home() / "Desktop" / cleaned)
                candidates.append(Path.home() / "Documents" / cleaned)

            resolved = ""
            seen = set()
            for cand in candidates:
                try:
                    p = cand.expanduser().resolve()
                except Exception:
                    p = cand.expanduser()
                sp = str(p)
                if sp in seen:
                    continue
                seen.add(sp)
                if p.exists():
                    resolved = sp
                    break

            if not resolved:
                msg = f"Could not open folder: {raw_target}"
                return {"ok": False, "action": a, "error": "path_not_found", "content": msg, "response": msg}

        # Cross-platform folder open: xdg-open (Linux) / open (macOS) /
        # os.startfile (Windows) via platform_compat — raw xdg-open was
        # Linux-only and silently failed elsewhere.
        from eli.utils.platform_compat import open_file as _open_file
        if _open_file(resolved):
            msg = f"Opened folder: {resolved}"
            return {"ok": True, "action": a, "content": msg, "response": msg}

        return {
            "ok": False,
            "action": a,
            "error": probe.get("stderr") or probe.get("error") or "xdg_open_failed",
            "content": f"Could not open folder: {resolved}",
            "response": f"Could not open folder: {resolved}",
        }

    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_open_media_hub(a, args):
    try:
        if (args or {}).get("open_settings"):
            try:
                from eli.execution.effectors.registry import get as _reg_get
                fn = _reg_get("OPEN_AUDIO_SETTINGS")
                if fn:
                    fn("OPEN_AUDIO_SETTINGS", {})
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        candidates = [
            ["spotify"],
            ["flatpak", "run", "com.spotify.Client"],
        ]
        for cmd in candidates:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Opened media: {' '.join(cmd)}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue

        url = "https://music.youtube.com"
        result = open_browser(url)
        if result.get("ok"):
            return result

        music_dir = os.path.expanduser("~/Music")
        return open_file_system(music_dir)
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "OPEN_AUDIO_SETTINGS": _h_open_audio_settings,
    "OPEN_BROWSER": _h_open_browser,
    "OPEN_SYSTEM_SETTINGS": _h_open_system_settings,
    "OPEN_POWER_SETTINGS": _h_open_power_settings,
    "OPEN_FILE_SYSTEM": _h_open_file_system,
    "OPEN_MEDIA_HUB": _h_open_media_hub,
}.items():
    _register(_action, _fn)

def _open_in_browser(*a, **k):
    from eli.execution.executor_enhanced import _open_in_browser as _f
    return _f(*a, **k)
