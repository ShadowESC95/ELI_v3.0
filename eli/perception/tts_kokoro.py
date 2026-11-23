"""Kokoro-82M, ELI's voice on every machine: text to speech on the CPU through onnxruntime.

Kokoro (hexgrad/Kokoro-82M, Apache-2.0 weights) turns phonemes into 24 kHz audio. The phonemes
come from misaki (Apache-2.0), Kokoro's own grapheme-to-phoneme library, used without its espeak-ng
fallback: espeak-ng is GPL and ELI v3 is sold, so a word misaki's dictionaries do not hold is
guessed here instead (_OovFallback):

- an all-capitals word is read letter by letter (an acronym), from misaki's own letter names;
- a word made of two dictionary words is read as those two words ("glimmerbrook");
- any other word is guessed by a CRF model trained on the CMU pronouncing dictionary (rhasspy's
  gruut, MIT), whose IPA is mapped onto Kokoro's phoneme set.

On a held-out set (misaki's gold words the CRF never saw) that order made 19.5% phoneme errors;
the CRF alone 21.0%, OpenPhonemizer 18.0% (it needs PyTorch, about 1 GB, for a fallback), and
reading every word letter by letter 126.6%.

The files (the ONNX model, the voices, spaCy's small English tagger that misaki tags words with,
and the CRF model) are fetched once, pinned and checksum-checked, by eli/runtime/voice_assets.py;
after that ELI speaks offline. tts_router decides when this engine speaks; this module only
renders.
"""
from __future__ import annotations

import base64
import io
import json
import re
import threading
import unicodedata
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 24_000          # Kokoro's output rate (a property of the model)
PREFIX = "kokoro:"            # accepted on a voice id; the bare id is the stored form

# The voices Kokoro ships whose text side needs no espeak-ng, with the grade its authors give each
# (VOICES.md: an estimate of the quality and amount of the voice's training audio). Accent "a" is American
# English, "b" British English, "z" Mandarin Chinese (misaki's Chinese G2P: jieba and pypinyin, MIT).
# Kokoro's Spanish, French, Hindi, Italian and Portuguese are left out: misaki reads them through espeak-ng;
# its Japanese needs pyopenjtalk, which has no ready-made build. Those languages are spoken by Qwen3-TTS
# on a GPU, or by the operating system's own voices (tts_router.voice_for_reply).
VOICES: Dict[str, Dict[str, str]] = {
    "af_heart": {"name": "Heart", "accent": "a", "gender": "female", "grade": "A"},
    "af_bella": {"name": "Bella", "accent": "a", "gender": "female", "grade": "A-"},
    "af_nicole": {"name": "Nicole", "accent": "a", "gender": "female", "grade": "B-"},
    "af_aoede": {"name": "Aoede", "accent": "a", "gender": "female", "grade": "C+"},
    "af_kore": {"name": "Kore", "accent": "a", "gender": "female", "grade": "C+"},
    "af_sarah": {"name": "Sarah", "accent": "a", "gender": "female", "grade": "C+"},
    "af_alloy": {"name": "Alloy", "accent": "a", "gender": "female", "grade": "C"},
    "af_nova": {"name": "Nova", "accent": "a", "gender": "female", "grade": "C"},
    "af_sky": {"name": "Sky", "accent": "a", "gender": "female", "grade": "C-"},
    "af_jessica": {"name": "Jessica", "accent": "a", "gender": "female", "grade": "D"},
    "af_river": {"name": "River", "accent": "a", "gender": "female", "grade": "D"},
    "am_fenrir": {"name": "Fenrir", "accent": "a", "gender": "male", "grade": "C+"},
    "am_michael": {"name": "Michael", "accent": "a", "gender": "male", "grade": "C+"},
    "am_puck": {"name": "Puck", "accent": "a", "gender": "male", "grade": "C+"},
    "am_echo": {"name": "Echo", "accent": "a", "gender": "male", "grade": "D"},
    "am_eric": {"name": "Eric", "accent": "a", "gender": "male", "grade": "D"},
    "am_liam": {"name": "Liam", "accent": "a", "gender": "male", "grade": "D"},
    "am_onyx": {"name": "Onyx", "accent": "a", "gender": "male", "grade": "D"},
    "am_santa": {"name": "Santa", "accent": "a", "gender": "male", "grade": "D-"},
    "am_adam": {"name": "Adam", "accent": "a", "gender": "male", "grade": "F+"},
    "bf_emma": {"name": "Emma", "accent": "b", "gender": "female", "grade": "B-"},
    "bf_isabella": {"name": "Isabella", "accent": "b", "gender": "female", "grade": "C"},
    "bf_alice": {"name": "Alice", "accent": "b", "gender": "female", "grade": "D"},
    "bf_lily": {"name": "Lily", "accent": "b", "gender": "female", "grade": "D"},
    "bm_fable": {"name": "Fable", "accent": "b", "gender": "male", "grade": "C"},
    "bm_george": {"name": "George", "accent": "b", "gender": "male", "grade": "C"},
    "bm_lewis": {"name": "Lewis", "accent": "b", "gender": "male", "grade": "D+"},
    "bm_daniel": {"name": "Daniel", "accent": "b", "gender": "male", "grade": "D"},
    "zf_xiaobei": {"name": "Xiaobei", "accent": "z", "gender": "female", "grade": "D"},
    "zf_xiaoni": {"name": "Xiaoni", "accent": "z", "gender": "female", "grade": "D"},
    "zf_xiaoxiao": {"name": "Xiaoxiao", "accent": "z", "gender": "female", "grade": "D"},
    "zf_xiaoyi": {"name": "Xiaoyi", "accent": "z", "gender": "female", "grade": "D"},
    "zm_yunjian": {"name": "Yunjian", "accent": "z", "gender": "male", "grade": "D"},
    "zm_yunxi": {"name": "Yunxi", "accent": "z", "gender": "male", "grade": "D"},
    "zm_yunxia": {"name": "Yunxia", "accent": "z", "gender": "male", "grade": "D"},
    "zm_yunyang": {"name": "Yunyang", "accent": "z", "gender": "male", "grade": "D"},
}
ENGLISH = ("a", "b")
# The language each accent speaks (ISO 639-1), for choosing a voice for a reply's language.
ACCENT_LANGUAGE = {"a": "en", "b": "en", "z": "zh"}
DEFAULT_VOICE = "af_heart"    # the one voice its authors grade A

# The voices that ELI's earlier engine (Piper) offered, mapped to the nearest Kokoro voice by accent
# and gender, so a saved choice keeps its kind of voice. Anything else falls to voice_for_legacy's
# accent rule.
_LEGACY_VOICES = {
    "en_US-amy-medium": "af_heart", "en_US-amy-low": "af_heart",
    "en_US-hfc_female-medium": "af_bella", "en_US-kathleen-low": "af_nicole",
    "en_US-lessac-high": "af_sarah", "en_US-lessac-medium": "af_sarah", "en_US-lessac-low": "af_sarah",
    "en_US-ljspeech-high": "af_sarah", "en_US-ljspeech-medium": "af_sarah",
    "en_US-hfc_male-medium": "am_michael", "en_US-joe-medium": "am_fenrir",
    "en_US-kusal-medium": "am_puck", "en_US-ryan-high": "am_michael", "en_US-ryan-medium": "am_michael",
    "en_US-ryan-low": "am_michael", "en_US-john-medium": "am_fenrir", "en_US-norman-medium": "am_onyx",
    "en_GB-alan-medium": "bm_george", "en_GB-alan-low": "bm_george",
    "en_GB-northern_english_male-medium": "bm_lewis", "en_GB-jenny_dioco-medium": "bf_emma",
    "en_GB-alba-medium": "bf_isabella", "en_GB-cori-high": "bf_alice", "en_GB-cori-medium": "bf_alice",
    "en_GB-southern_english_female-low": "bf_lily", "en_GB-semaine-medium": "bf_emma",
    "en_GB-aru-medium": "bf_emma", "en_GB-vctk-medium": "bf_emma",
}


BLEND = "+"                   # 'af_heart+bf_emma': the voices' styles averaged (Kokoro's own blending)


def voice_id(name: str) -> str:
    """The stored form of a Kokoro voice name ('kokoro:af_heart' -> 'af_heart'; 'af_heart,bf_emma' or
    'af_heart+bf_emma' -> 'af_heart+bf_emma', a blend); '' when it is not one."""
    vid = str(name or "").strip()
    if vid.lower().startswith(PREFIX):
        vid = vid[len(PREFIX):]
    parts = [p.strip() for p in re.split(r"[+,]", vid) if p.strip()]
    if len(parts) > 1:
        parts = list(dict.fromkeys(parts))
        # one language: the phonemes are read for the first voice's
        if all(p in VOICES for p in parts) and len({ACCENT_LANGUAGE.get(VOICES[p]["accent"]) for p in parts}) == 1:
            return BLEND.join(parts) if len(parts) > 1 else parts[0]
        return ""
    return vid if vid in VOICES else ""


def blend_parts(vid: str) -> List[str]:
    """The Kokoro voices a voice id is made of: itself, or each voice of a blend."""
    v = voice_id(vid)
    return v.split(BLEND) if v else []


def voice_for_legacy(name: str) -> str:
    """The Kokoro voice that takes the place of a voice id saved for an earlier engine (a Piper name
    such as 'en_GB-alan-medium'): its own match, else British English for an en_GB voice, else the
    default. '' for anything that is not a voice id of that form."""
    vid = str(name or "").strip()
    if vid in _LEGACY_VOICES:
        return _LEGACY_VOICES[vid]
    if re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(?:x_low|low|medium|high)", vid):
        return "bf_emma" if vid.startswith("en_GB-") else DEFAULT_VOICE
    return ""


def legacy_names() -> Dict[str, str]:
    """{name: Kokoro voice} for the names of the earlier engine's voices ("alan" -> "bm_george"), so
    "use the alan voice" still finds a voice of that kind."""
    out: Dict[str, str] = {}
    for vid, kokoro in _LEGACY_VOICES.items():
        out.setdefault(vid.split("-", 1)[1].rsplit("-", 1)[0].lower(), kokoro)
    return out


def describe(vid: str) -> str:
    """'Heart (US female)' for a voice id; 'Heart + Emma (blend)' for a blend."""
    parts = blend_parts(vid)
    if len(parts) > 1:
        return " + ".join(VOICES[p]["name"] for p in parts) + " (blend)"
    meta = VOICES.get(voice_id(vid))
    if not meta:
        return str(vid or "")
    where = {"a": "US", "b": "UK", "z": "Mandarin"}.get(meta["accent"], meta["accent"])
    return f"{meta['name']} ({where} {meta['gender']})"


# ── out-of-dictionary words ───────────────────────────────────────────────────

_STRESS = "ˈˌ"
# gruut's IPA (from the CMU dictionary) in Kokoro's symbols: Kokoro writes each diphthong and
# affricate as one letter (misaki's EN_PHONES.md).
_IPA_TO_KOKORO = (("d͡ʒ", "ʤ"), ("t͡ʃ", "ʧ"), ("aɪ", "I"), ("aʊ", "W"), ("eɪ", "A"), ("ɔɪ", "Y"))
# A word is split into two dictionary words only when each part is a word of its own; parts this
# short ("an", "is") made more wrong splits than right ones on the held-out set.
_MIN_PART = 3


class _CrfG2P:
    """Grapheme-to-phoneme CRF from rhasspy's gruut (MIT, Michael Hansen): the features below are
    gruut's (gruut/g2p.py, grapheme2features), so its trained English model reads them."""

    def __init__(self, model_path: Path):
        import pycrfsuite
        self._tagger = pycrfsuite.Tagger()
        self._tagger.open(str(model_path))

    @staticmethod
    def _enc(s: str) -> str:
        return base64.b64encode(s.encode()).decode("ascii")

    def __call__(self, word: str) -> List[str]:
        word = unicodedata.normalize("NFC", word)
        feats = []
        for i, g in enumerate(word):
            f: Dict[str, Any] = {"bias": 1.0, "grapheme": self._enc(g)}
            if i == 0:
                f["begin"] = True
            for j in (1, 2, 3):
                if i >= j:
                    f[f"grapheme-{j}"] = self._enc(word[i - j])
                if i < len(word) - j:
                    f[f"grapheme+{j}"] = self._enc(word[i + j])
            if i == len(word) - 1:
                f["end"] = True
            feats.append(f)
        out: List[str] = []
        for label in self._tagger.tag(feats):
            for p in base64.b64decode(label).decode().split("|"):
                if p != "_":
                    out.append(p)
        return out


def ipa_to_kokoro(phones: List[str], british: bool) -> str:
    """gruut's American IPA phonemes (stress marks on the vowel) in Kokoro's phoneme letters. British
    voices get the forms misaki's British dictionary uses: no r after a vowel (the vowel lengthened
    instead), 'Q' for the GOAT vowel and 'a' for the TRAP vowel."""
    out: List[str] = []
    for p in phones:
        st = ""
        while p and p[0] in _STRESS:
            st, p = st + p[0], p[1:]
        for a, b in _IPA_TO_KOKORO:
            p = p.replace(a, b)
        if p == "oʊ":
            p = "Q" if british else "O"
        elif p == "æ" and british:
            p = "a"                       # misaki's British TRAP vowel
        elif p == "ɚ":
            p = ("ɜː" if st else "ə") if british else ("ɜɹ" if st else "əɹ")
        out.append(st + p)
    text = "".join(out)
    if british:
        text = re.sub(r"([ɑɔɛɪʊ])ɹ(?![AIOQWYaeiouæɑɒɔəɛɜɪʊʌ])", lambda m: {
            "ɑ": "ɑː", "ɔ": "ɔː", "ɛ": "ɛː", "ɪ": "ɪə", "ʊ": "ʊə"}[m.group(1)], text)
    return text


class _OovFallback:
    """misaki's fallback for a word its dictionaries do not hold (called with misaki's token; returns
    (phonemes, rating) as misaki's own fallbacks do, or (None, None) when it cannot say the word)."""

    def __init__(self, lexicon, crf: Optional[_CrfG2P], british: bool):
        self._lex, self._crf, self._british = lexicon, crf, british
        from misaki import en
        self._vocab = en.GB_VOCAB if british else en.US_VOCAB

    def _known(self, part: str) -> Optional[str]:
        for d in (self._lex.golds, self._lex.silvers):
            v = d.get(part)
            if isinstance(v, dict):
                v = v.get("DEFAULT")
            if isinstance(v, str) and v:
                return v
        return None

    def _split(self, word: str) -> Optional[str]:
        w = word.lower()
        best: Optional[Tuple[int, str]] = None
        for i in range(_MIN_PART, len(w) - _MIN_PART + 1):
            a, b = self._known(w[:i]), self._known(w[i:])
            if a and b and (best is None or min(i, len(w) - i) > best[0]):
                best = (min(i, len(w) - i), a + b)
        return best[1] if best else None

    def __call__(self, token) -> Tuple[Optional[str], Optional[int]]:
        text = str(getattr(token, "text", "") or "")
        letters = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        word = "".join(c for c in letters if c.isalpha() or c in "'-")
        if not word or not all(c.isascii() for c in word):
            return None, None
        ps: Optional[str] = None
        if word.isupper() and len(word) > 1:
            ps, _ = self._lex.get_NNP(word)
        if ps is None:
            ps = self._split(word)
        if ps is None and self._crf is not None:
            ps = ipa_to_kokoro(self._crf(word.lower().replace("'", "").replace("-", "")), self._british)
        if ps is None:
            ps, _ = self._lex.get_NNP(word)          # no model: spell it rather than drop it
        ps = "".join(c for c in (ps or "") if c in self._vocab)
        return (ps, 1) if ps else (None, None)


def _misaki_g2p(nlp, british: bool, fallback):
    """misaki's English G2P with the spaCy pipeline ELI loaded itself. misaki's own constructor
    installs a missing spaCy model with pip at run time, which a packaged app cannot and an offline
    one must not do; this sets the same five attributes (tests/test_perception_tts.py holds them to
    misaki's constructor)."""
    from misaki import en

    class _G2P(en.G2P):
        def __init__(self):
            self.version = None
            self.british = british
            self.nlp = nlp
            self.lexicon = en.Lexicon(british)
            self.fallback = fallback(self.lexicon)
            self.unk = ""

    return _G2P()


# ── the engine ───────────────────────────────────────────────────────────────

class _Engine:
    """One ONNX session, the tokenizer's vocabulary and the voices loaded so far."""

    def __init__(self, files: Dict[str, Path]):
        import numpy as np
        import onnxruntime as ort
        self._np = np
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(str(files["model"]), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        tok = json.loads(Path(files["tokenizer"]).read_text(encoding="utf-8"))
        self.vocab: Dict[str, int] = dict(tok["model"]["vocab"])
        self._files = files
        self._packs: Dict[str, Any] = {}
        self._g2p: Dict[str, Any] = {}
        self._nlp = None
        self._crf: Optional[_CrfG2P] = None
        self._lock = threading.Lock()

    def pack(self, vid: str):
        parts = blend_parts(vid)
        if len(parts) > 1 and vid not in self._packs:
            # a blend is its voices' style vectors averaged, row by row, over the rows all of them have
            packs = [self.pack(p) for p in parts]
            rows = min(len(p) for p in packs)
            self._packs[vid] = sum(p[:rows] for p in packs) / float(len(packs))
        if vid not in self._packs:
            from eli.runtime import voice_assets
            path = voice_assets.kokoro_voice_file(vid)
            if path is None:
                raise FileNotFoundError(f"Kokoro voice {vid} is not on this machine")
            # (rows, 1, 256): one style vector per phoneme count; rows is the longest input it takes
            self._packs[vid] = self._np.fromfile(str(path), dtype=self._np.float32).reshape(-1, 1, 256)
        return self._packs[vid]

    def g2p(self, accent: str):
        if accent == "z":
            if "z" not in self._g2p:
                from misaki import zh
                self._g2p["z"] = zh.ZHG2P(version=None)          # the v1.0 model's Chinese frontend
            return self._g2p["z"]
        british = accent == "b"
        if accent not in self._g2p:
            if self._nlp is None:
                import spacy
                self._nlp = spacy.load(str(self._files["spacy"]), enable=["tok2vec", "tagger"])
            if self._crf is None and self._files.get("crf"):
                try:
                    self._crf = _CrfG2P(self._files["crf"])
                except Exception:
                    log.warning("[TTS] the guessing model for unknown words did not load; they are spelt",
                                exc_info=True)
            crf = self._crf
            self._g2p[accent] = _misaki_g2p(self._nlp, british,
                                            lambda lex: _OovFallback(lex, crf, british))
        return self._g2p[accent]

    def phonemes(self, text: str, accent: str) -> str:
        ps, _tokens = self.g2p(accent)(str(text or ""))
        return ps

    def ids(self, phonemes: str) -> List[int]:
        return [self.vocab[c] for c in phonemes if c in self.vocab]

    def run(self, ids: List[int], pack, speed: float):
        np = self._np
        style = pack[min(len(ids), len(pack)) - 1]   # the reference package's index: pack[len(ps) - 1]
        out = self.session.run(None, {
            "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
            "style": np.asarray(style, dtype=np.float32),
            "speed": np.array([speed], dtype=np.float32),
        })[0]
        return np.asarray(out, dtype=np.float32).reshape(-1)


_ENGINE: Optional[_Engine] = None
_ENGINE_LOCK = threading.Lock()
_LOAD_ERROR = ""


def _engine(fetch: bool = True) -> Optional[_Engine]:
    """The loaded engine, its files fetched first when `fetch` and they are missing; None, with
    load_error() saying why, when it cannot load."""
    global _ENGINE, _LOAD_ERROR
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            from eli.runtime import voice_assets
            files = voice_assets.kokoro_files()
            if files is None and fetch:
                res = voice_assets.ensure_kokoro()
                if not res.get("ok"):
                    raise RuntimeError(res.get("error") or "its files could not be fetched")
                files = voice_assets.kokoro_files()
            if files is None:
                raise RuntimeError("its files are not on this machine")
            _ENGINE = _Engine(files)
            _LOAD_ERROR = ""
        except Exception as exc:
            _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
            log.warning("[TTS] the Kokoro voice did not load: %s", _LOAD_ERROR, exc_info=True)
            return None
    return _ENGINE


def load_error() -> str:
    """Why the engine did not load, or '' when it did or has not been asked to."""
    return _LOAD_ERROR


def available() -> bool:
    """True when the engine's packages are installed (its files may still have to be fetched).
    python-crfsuite is not among them: where it has no build (Windows on ARM) the words misaki's
    dictionaries lack are spelt instead of guessed, and the voice still speaks."""
    import importlib.util
    try:
        return all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "misaki", "spacy"))
    except (ImportError, ValueError):
        return False


def speaks(language: str) -> List[str]:
    """The Kokoro voices that speak `language` (ISO 639-1) on this machine's packages, best graded
    first; Chinese needs misaki's Chinese G2P (jieba, pypinyin, cn2an, ordered-set)."""
    import importlib.util
    voices = [v for v, m in VOICES.items() if ACCENT_LANGUAGE.get(m["accent"]) == language]
    if language == "zh":
        try:
            if not all(importlib.util.find_spec(m) for m in ("jieba", "pypinyin", "cn2an", "ordered_set")):
                return []
        except (ImportError, ValueError):
            return []
    return voices if available() else []


def _split_phonemes(ps: str, limit: int) -> List[str]:
    """Phonemes in pieces the model takes (at most `limit` each), cut after a sentence or clause mark,
    else at a space, as Kokoro's own pipeline cuts them."""
    pieces: List[str] = []
    ps = ps.strip()
    while len(ps) > limit:
        cut = max(ps.rfind(m, 0, limit) for m in ".!?;:,—…")
        if cut <= 0:
            cut = ps.rfind(" ", 0, limit)
        cut = cut + 1 if cut > 0 else limit
        pieces.append(ps[:cut].strip())
        ps = ps[cut:].strip()
    if ps:
        pieces.append(ps)
    return [p for p in pieces if p]


def synthesize(text: str, voice: str = DEFAULT_VOICE, *, speed: float = 1.0):
    """`text` as float32 samples at SAMPLE_RATE, or None when nothing could be said (the engine did
    not load, the voice is missing, or the text has no sayable word)."""
    vid = voice_id(voice) or DEFAULT_VOICE
    eng = _engine()
    if eng is None:
        return None
    try:
        accent = VOICES[blend_parts(vid)[0]]["accent"]     # a blend reads as its first voice
        with eng._lock:
            pack = eng.pack(vid)
            ps = eng.phonemes(text, accent)
            pieces = [eng.ids(p) for p in _split_phonemes(ps, len(pack))]
            parts = [eng.run(ids, pack, float(speed)) for ids in pieces if ids]
    except Exception:
        log.warning("[TTS] Kokoro could not render %r", str(text)[:40], exc_info=True)
        return None
    if not parts:
        return None
    import numpy as np
    return np.concatenate(parts)


def to_wav(samples, rate: int = SAMPLE_RATE) -> bytes:
    """float32 samples as 16-bit mono WAV bytes."""
    import numpy as np
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def synthesize_wav(text: str, voice: str = DEFAULT_VOICE, *, speed: float = 1.0) -> Optional[bytes]:
    """`text` as WAV bytes, or None (see synthesize)."""
    samples = synthesize(text, voice, speed=speed)
    return to_wav(samples) if samples is not None and len(samples) else None


def _reset_for_tests() -> None:
    global _ENGINE, _LOAD_ERROR
    _ENGINE, _LOAD_ERROR = None, ""
