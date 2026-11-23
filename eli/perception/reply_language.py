"""The language a reply is written in, so the voice that speaks it can be one that speaks it.

ELI replies in the user's language when the user writes in one; its default voices are English. A reply in
a script of its own (Chinese, Japanese, Korean, Cyrillic, Greek, Arabic, Hebrew, Devanagari, Thai) is that
script's language. A reply in the Latin script is English unless fewer than half its words are English
words (misaki's English dictionaries, which the Kokoro voice already carries) and langdetect (Apache-2.0,
a port of Google's language-detection) names another language.

Measured on 318 English phrases from ELI's capability table and 30 replies in six other languages:
langdetect alone took 64 of the English phrases for another language even at a 0.999 probability; with
the dictionary share it took one ("gguf errors") and found 24 of the 30.
"""
from __future__ import annotations

import importlib.resources
import json
import re
import threading
import unicodedata
from typing import Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

# Unicode script name (unicodedata.name's first word) -> language
_SCRIPTS = {"CJK": "zh", "HIRAGANA": "ja", "KATAKANA": "ja", "HANGUL": "ko", "CYRILLIC": "ru", "GREEK": "el",
            "ARABIC": "ar", "HEBREW": "he", "DEVANAGARI": "hi", "THAI": "th"}
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)

_ENGLISH: Optional[set] = None
_LOCK = threading.Lock()


def _english_words() -> set:
    global _ENGLISH
    with _LOCK:
        if _ENGLISH is None:
            words: set = set()
            try:
                import misaki.data as data
                for name in ("us_gold.json", "us_silver.json"):
                    with importlib.resources.open_text(data, name) as fh:
                        words |= {k.lower() for k in json.load(fh)}
            except Exception:
                log.debug("[TTS] misaki's English dictionaries unavailable; every Latin-script reply is English",
                          exc_info=True)
            _ENGLISH = words
    return _ENGLISH


def _script(ch: str) -> str:
    try:
        return unicodedata.name(ch).split(" ", 1)[0]
    except ValueError:
        return ""


def english_share(text: str) -> float:
    """The share of the text's words that are English words (1.0 when there are none to judge)."""
    words = [w.lower() for w in _WORD.findall(text or "") if len(w) > 1 or w.lower() in ("a", "i")]
    known = _english_words()
    if not words or not known:
        return 1.0
    return sum(w in known for w in words) / len(words)


def language_of(text: str) -> str:
    """ISO 639-1 code of the language `text` is written in; "en" when it cannot tell."""
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return "en"
    counts: dict = {}
    for ch in letters:
        lang = _SCRIPTS.get(_script(ch))
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if counts and sum(counts.values()) * 2 > len(letters):
        if counts.get("ja"):                      # kana among Han characters: Japanese
            return "ja"
        return max(counts, key=counts.get)
    if english_share(text) * 2 >= 1:
        return "en"
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0                  # the same answer for the same text, every time
        best = detect_langs(str(text))[0]
        return str(best.lang).split("-", 1)[0]
    except Exception:
        log.debug("[TTS] language detection unavailable", exc_info=True)
        return "en"
