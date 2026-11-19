"""Apps & windows effector: opening a URL or a named site, the mail app and the browser.
Tiling is window_control's (eli.system.portable_app_control); app names are app_aliases'.
"""
from __future__ import annotations

from typing import Any, Dict
from eli.utils.log import get_logger
log = get_logger(__name__)


def open_browser(url: str = "https://duckduckgo.com", urls: list = None) -> Dict[str, Any]:
    """Open one or more URLs in the default browser.

    One implementation (system_helpers), which reports a browser that did not open instead of
    claiming success; this copy used to say "Opened browser" regardless.
    """
    from eli.execution.effectors.system_helpers import open_browser as _ob
    return _ob(url, urls=urls)


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


__all__ = ['open_browser', '_eli_open_url_action']


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
    # The mail app installed on this OS (Thunderbird/Evolution, Mail/Outlook, Outlook/Mail), else
    # whatever the OS opens mailto: links with. It was three Linux apps, then a webmail site.
    try:
        from eli.utils.platform_compat import open_app_uri, open_role
        opened = open_role("mail") or ("your mail app" if open_app_uri("mailto:") else "")
        if opened:
            msg = f"Opened Communication Hub: {opened}"
            return {"ok": True, "action": a, "content": msg, "response": msg}
        msg = "No mail app is installed, and nothing on this system opens mail links."
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
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
