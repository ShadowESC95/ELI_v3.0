"""Router weather intent — weather-request prepass (bare/topic/signal detection). Moved from router_enhanced.py."""
from __future__ import annotations

import re


def _eli_weather_prepass(user_text: str):
    low = user_text.lower().strip()
    # Guard: only fire when the sentence is *requesting* weather info.
    # Skip if the user is talking *about* a weather-related topic (e.g.
    # "the script is for a weather forecast", "stop telling me the weather").
    _REQUEST_SIGNALS = re.compile(
        r"\b(?:what(?:'s|s|\s+is|\s+will)?\s+(?:the\s+)?(?:weather|forecast|temperature|temp)|"
        r"how(?:'s|\s+is)\s+(?:the\s+)?weather|"
        r"(?:check|get|tell\s+me|show(?:\s+me)?|give\s+me)\s+(?:the\s+)?(?:weather|forecast|temperature)|"
        r"(?:will\s+it|is\s+it|going\s+to)\s+(?:rain|snow|be\s+(?:hot|cold|warm|sunny|cloudy)))\b",
        re.I)
    # Also allow bare "weather in X" / "weather for X" at the start of input
    _BARE_WEATHER_REQUEST = re.compile(
        r"^(?:weather|forecast|temperature)\b[^.!?]{0,60}\b(?:in|at|for)\s+[A-Za-z]",
        re.I)
    _TOPIC_PHRASES = re.compile(
        r"\b(?:script|code|program|file|function|task|fix|issue|error|this|that|the\s+\w+)\s+"
        r"(?:is|was|are|were|for|about|regarding|related\s+to)\s+(?:a\s+)?(?:weather|forecast|temperature)\b|"
        r"\b(?:stop|don'?t|cease|quit)\b.{0,30}\bweather\b",
        re.I)
    if _TOPIC_PHRASES.search(low):
        return None
    if not _REQUEST_SIGNALS.search(low) and not _BARE_WEATHER_REQUEST.search(low):
        return None
    m = re.search(
        r"(?:weather|forecast|temperature).{0,40}?(?:in|at|for) ([A-Za-z][A-Za-z ,]+?)(?:\?|$|[,;]|\band\b)",
        user_text,
        re.I)
    if m:
        loc = m.group(1).strip().rstrip("?.!, ")
        return {
            "action": "GET_WEATHER",
            "args": {"location": loc, "_raw_user_text": user_text},
            "confidence": 0.97,
            "meta": {"matched_by": "weather.prepass", "entities": {"location": loc}},
        }
    return None
