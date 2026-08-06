"""Power controls, system settings, and direct URL opens.

Everything that reaches for the machine's own surfaces rather than an app.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import re

from eli.execution.routing._helpers import (
    _mk,
)


def route_system_settings(text: str, raw: str, low: str, text_l: str):
    if re.search(
            r"\b(?:initiate|open|launch|access|start)\s+audio\s+(?:interface|settings)\b", raw, re.I):
        return _mk("OPEN_AUDIO_SETTINGS", {}, 1.0,
                   matched_by="alias.audio_interface")

    if re.search(
            r"\b(?:access|open|launch|start)\s+communication\s+hub\b", raw, re.I):
        return _mk("OPEN_COMMUNICATION_HUB", {}, 1.0,
                   matched_by="alias.communication_hub")

    if re.search(r"\bopen\s+power\b", raw, re.I):
        return _mk("OPEN_POWER_SETTINGS", {}, 0.95, matched_by="system.power")

    if re.match(r"^(?:open|launch|start|show)\s+(?:system\s+)?settings?\b", low):
        return _mk("OPEN_SYSTEM_SETTINGS", {}, 0.98,
                   matched_by="system.settings")

    # ------------------------------------------------------------
    # 12b) PROACTIVE DAEMON — must be before section 13 open/launch/start catch-all
    # ------------------------------------------------------------
    if (
        re.search(r"\bproactive\s+(?:daemon\s+)?(?:status|state|running|doing|up\s+to|activity|active|alive)\b", low)
        or re.search(r"\b(?:status|state)\s+of\s+(?:the\s+)?proactive\s+daemon\b", low)
        or re.search(r"\bwhat(?:'s| is)\s+(?:the\s+)?status\s+of\s+(?:the\s+)?proactive\s+daemon\b", low)
        # "what is the proactive daemon doing (right now)", "what's the proactive
        # daemon up to" — fell to CHAT and confabulated "performing its normal
        # background tasks" instead of reporting real pid/running/log state.
        or re.search(r"\bwhat(?:'s| is)\s+(?:the\s+)?proactive\s+daemon\b", low)
        or re.search(r"\b(?:is|are)\s+(?:the\s+)?proactive\s+(?:daemon|loop)\b.*\b(running|active|on|working|alive)\b", low)
    ):
        return _mk("PROACTIVE_STATUS", {}, 0.95,
                   matched_by="system.proactive_status")
    if re.search(
            r"\b(?:start|launch|run)\s+(?:the\s+)?(?:proactive\s+(?:daemon|mode)|proactive)\b", low):
        return _mk("PROACTIVE_START", {}, 0.95,
                   matched_by="system.proactive_start")
    if re.search(
            r"\b(?:stop|kill|shut\s+down)\s+(?:the\s+)?(?:proactive\s+(?:daemon|mode)|proactive)\b", low):
        return _mk("PROACTIVE_STOP", {}, 0.95,
                   matched_by="system.proactive_stop")

    # ------------------------------------------------------------
    # 13) OPEN / LAUNCH (apps, aliases, websites, paths)
    # ------------------------------------------------------------
    # A bare domain ("open github.com") is a URL, not an app. Checked first and
    # kept in its OWN variable: this match used to overwrite the shared `m`
    # before the open/launch block below could read it, which silently made the
    # whole path/folder branch unreachable — every "open <path|folder>" fell
    # through to OPEN_APP and was answered "not installed, shall I install it?".
    _url_m = re.match(r"^\s*open\s+((?:https?://)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s]*)?)\s*$", text_l)
    if _url_m:
        _url = _url_m.group(1).strip()
        if not _url.startswith(("http://", "https://")):
            _url = "https://" + _url
        return _mk(
            "OPEN_URL",
            {"url": _url},
            0.99,
            matched_by="open.domain.preempt",
            allow_chat_without_evidence=False,
        )

    return None
