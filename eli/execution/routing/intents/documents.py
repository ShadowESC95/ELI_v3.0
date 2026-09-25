"""Document creation, clipboard, weather and calendar lookups.

CREATE_DOCUMENT / DATA_FABRICATOR plus the ambient clipboard, weather and
calendar guards that sit with them in the legacy order.

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


def route_documents(text: str, raw: str, low: str, text_l: str):
    m = re.search(
        r"\b(create|write|generate|draft|make|place|raise)\s+(?:a\s+)?(?:doc|document|report|notes?)\s+(?:about|on|for)\s+(.+)$",
        raw,
        re.I)
    if m:
        topic = m.group(2).strip()
        return _mk("CREATE_DOCUMENT", {"topic": topic, "format": "md", "use_advanced_generator": True, "use_gguf_only": True,
                   "forbid_ollama": True}, 0.9, matched_by="doc.generate_structured", entities={"topic": topic})

    # Data fabricator alias
    if re.search(
            r"\b(?:fabricate|create|make)\s+data\s+(?:construct|fabricator)\b", raw, re.I):
        topic_match = re.search(
            r"fabricate data construct(?:\s+about\s+|\s+on\s+)?(.+)$", raw, re.I)
        if topic_match:
            topic = topic_match.group(1).strip()
            return _mk("DATA_FABRICATOR", {
                       "topic": topic}, 0.98, matched_by="alias.data_fabricator", entities={"topic": topic})
        return _mk("DATA_FABRICATOR", {}, 0.98,
                   matched_by="alias.data_fabricator")

    # ------------------------------------------------------------
    # 11) CLIPBOARD / WEATHER / CALENDAR
    # ------------------------------------------------------------
    if any(w in low for w in ["clipboard", "copy", "paste"]):
        if "copy" in low or "set clipboard" in low:
            content = re.sub(
                r'.*(copy|set clipboard to|clipboard)\s+',
                '',
                raw,
                flags=re.I).strip()
            return _mk("SET_CLIPBOARD", {
                       "text": content}, 0.9, matched_by="clipboard.set", entities={"text": content})
        if "get" in low or "what" in low or "show" in low:
            return _mk("GET_CLIPBOARD", {}, 0.9, matched_by="clipboard.get")

    if any(w in low for w in ["weather", "forecast", "temperature"]):
        # Only route when the sentence is an actual weather *request*, not a
        # sentence that merely mentions weather as a subject/object/topic.
        _wx_request = re.search(
            r"\b(?:what(?:'s|s|\s+is|\s+will)?\s+(?:the\s+)?(?:weather|forecast|temperature)|"
            r"how(?:'s|\s+is)\s+(?:the\s+)?weather|"
            r"(?:check|get|tell\s+me|show(?:\s+me)?|give\s+me)\s+(?:the\s+)?(?:weather|forecast|temperature)|"
            r"(?:will\s+it|is\s+it|going\s+to)\s+(?:rain|snow|be\s+(?:hot|cold|warm|sunny|cloudy)))\b",
            low, re.I)
        _wx_topic = re.search(
            r"\b(?:script|code|program|file|function|task|fix|issue|error|this|that)\s+"
            r"(?:is|was|are|were|for|about)\s+(?:a\s+)?(?:weather|forecast)|"
            r"\b(?:stop|don'?t|cease|quit)\b.{0,30}\bweather\b",
            low, re.I)
        if _wx_request and not _wx_topic:
            location_match = re.search(
                r'\b(?:in|at|for)\b\s+([\w\s\-]+?)(?:\s*[,?!.]|\s+and\s+|\s+(?:provide|tell|show|give|also|then|next|tomorrow|today|forecast)|$)',
                raw,
                re.I)
            location = location_match.group(1).strip().rstrip("?.!, ") if location_match else None
            return _mk("GET_WEATHER", {"location": location, "_raw_user_text": raw}, 0.95, matched_by="info.weather", entities={
                       "location": location} if location else None)

    if any(w in low for w in ["calendar", "calender",
           "calander", "event", "appointment", "meeting"]):
        if any(w in low for w in ["show", "list",
               "my", "what", "open", "display"]):
            return _mk("LIST_EVENTS", {}, 0.95, matched_by="calendar.list")
        if any(w in low for w in ["add", "create", "schedule"]):
            return _mk("ADD_EVENT", {"text": raw}, 0.95,
                       matched_by="calendar.add", entities={"text": raw})
    return None
