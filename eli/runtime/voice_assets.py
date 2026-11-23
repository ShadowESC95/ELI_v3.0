"""Ensure the local voice models (speech in and speech out) are present.

Speech out is Kokoro-82M (eli/perception/tts_kokoro.py); speech in is faster-whisper. The packages
ship the runtimes; this module fetches the weights: Kokoro's files on first use (or in the
installer's online step), whisper's model the same way. Every Kokoro file is pinned to one revision
and checked against its SHA-256 before it is kept, so a truncated or altered download is refused
here rather than crashing the engine later. Already-present files are reported without touching the
network, and every download goes through ``netguard.allow_network`` (ELI is offline by default).

Release builds carry the Kokoro files (the release workflow fetches them with this module into
``tts_kokoro/`` and the bundle reads them from there), so a packaged app speaks without ever going
online; a source install fetches them into ``models/tts/kokoro`` the first time ELI speaks.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ── Kokoro's files ─────────────────────────────────────────────────────────────
# The ONNX export of Kokoro-82M v1.0 (Apache-2.0), at one revision. fp32: on a CPU it rendered as
# fast as the fp16 and uint8 exports and four times faster than the int8 one, and the reduced
# exports each changed the sound (log-spectral distance from fp32: fp16 0.44, int8 1.42, uint8 1.89,
# the last two also changing the timing).
_KOKORO_REPO = "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/1939ad2a8e416c0acfeecc08a694d14ef25f2231/"
# spaCy's small English tagger (MIT), which misaki tags words with to tell "read" from "read".
_SPACY_MODEL = "en_core_web_sm-3.8.0"
_SPACY_WHEEL = ("https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/"
                "en_core_web_sm-3.8.0-py3-none-any.whl")
# rhasspy/gruut's English grapheme-to-phoneme CRF (MIT), for words misaki's dictionaries lack.
_G2P_CRF = ("https://raw.githubusercontent.com/rhasspy/gruut/a2187f9dfe472e4aafffcf043bd1a220c057ee9d/"
            "gruut-lang-en/gruut_lang_en/g2p/model.crf")
_MARKER = ".eli-sha256"           # in an unpacked folder: the checksum of the archive it came from


@dataclass(frozen=True)
class KokoroFile:
    """One pinned file: where it goes under the Kokoro folder, where it comes from, its SHA-256 and
    size. `unpack` names the folder inside a wheel that is kept (the file is then a folder)."""
    rel: str
    url: str
    sha256: str
    size: int
    unpack: str = ""


_VOICE_SHA256 = {
    "af_alloy": "c4a6b876047fd7fb472edf4ebd63cfac7c3b958a7cae7c106e8f038ca6308c45",
    "af_aoede": "4a004c33430762e2461eedb2013fad808ef4ab3121f5300f554476caf58d8361",
    "af_bella": "f69d836209b78eb8c66e75e3cda491e26ea838a3674257e9d4e5703cbaf55c8b",
    "af_heart": "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
    "af_jessica": "a240a5e3c15b43563d6e923bdca8ef5613a23471d9b77653694012435df23bd8",
    "af_kore": "9be5221b6a941c04b561959b8ff0b06e809444dcc4ab7e75a7b23606f691819e",
    "af_nicole": "cd2191ab31b914ed7b318416b0e4440fdf392ddad9106a060819aa600a64f59a",
    "af_nova": "18778272caa0d0eebaea251c35fd635f038434f9eee5e691d02a174bd328414f",
    "af_river": "00a2bcf82b1d86e8f19902ede58c65ccf6c0e43b44b7d74fad54e5d8933c9c30",
    "af_sarah": "4409fbc125afabacc615d94db5398d847006a737b0247d6892b7a9a0007a2f0a",
    "af_sky": "4435255c9744f3f31659e0d714ab7689bf65d9e77ec1cce060f083912614f0b9",
    "am_adam": "162b035ed91cfc48b6046982184c645f72edcdd1b82843347f605d7bf7b15716",
    "am_echo": "3968b92c3c4cd1c4416dbded36c13eaa388a90d5788d02a13e4d781f5f8cf3c3",
    "am_eric": "e8b5be17edd1e3636901ce7598baafe2dc8dd8ff707a0c23bf9e461add7e2832",
    "am_fenrir": "c27989f741f7ee34d273a39d8a595cc0837d35f5ced9a29b7cc162614616df43",
    "am_liam": "52403be32fd047c6a44517cb0bcd6b134f2a18baa73e70ef41651e0eab921ade",
    "am_michael": "1d1f21dd8da39c30705cd4c75d039d265e9bc4a2a93ed09bc9e1b1225eb95ba1",
    "am_onyx": "da5d135b424164916d75a68ffb4c2abce3d7d5ccc82dd1ee6cf447ce286145e6",
    "am_puck": "fcf73c989033e9233e0b98713eca600c8c74dcc1614b37009d5450ff4a2274a0",
    "am_santa": "61150cf726ab6c5ed7a99f90a304f91f5a72c00c592e89ec94e5df11c319227a",
    "bf_alice": "08afa6ba24da61ea5e8efa139e5aadc938d83f0a6da5a900adaf763ac1da5573",
    "bf_emma": "669fe0647f9dd04fcab92f1439a40eeb4c8b4ab1f82e4996fe3d918ce4a63b73",
    "bf_isabella": "3754352c4aaa46d17f27654ab7518d65b62ad6163a0f55a5f4330c2da2c4e94f",
    "bf_lily": "5e0ee32ebe64a467124976b14e69590746f1c4ce41a12b587a50c862edfea335",
    "bm_daniel": "6b3194bbceffb746733cbc22c8f593dd44e401a71d53895a2dca891bc595a1e8",
    "bm_fable": "f889083196807b4adb15e9204252165f503b8d33d3982e681c52443c49d798f1",
    "bm_george": "c4b235a4c1f2cd3b939fed08b899ce9385638b763f7b73a59616c4fc9bd6c9bc",
    "bm_lewis": "b8f671cef828c30e66fdf0b0756a76bba58f6bb3398cbbf27058642acbcedb97",
}
_VOICE_BYTES = 522_240           # each voice: 510 style vectors of 256 float32

KOKORO_ENGINE_FILES: Dict[str, KokoroFile] = {
    "model": KokoroFile("model.onnx", _KOKORO_REPO + "onnx/model.onnx",
                        "8fbea51ea711f2af382e88c833d9e288c6dc82ce5e98421ea61c058ce21a34cb", 325_532_232),
    "tokenizer": KokoroFile("tokenizer.json", _KOKORO_REPO + "tokenizer.json",
                            "77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34", 3_497),
    "spacy": KokoroFile(_SPACY_MODEL, _SPACY_WHEEL,
                        "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85", 12_806_118,
                        unpack=f"en_core_web_sm/{_SPACY_MODEL}/"),
    "crf": KokoroFile("g2p_en.crf", _G2P_CRF,
                      "7942e8f8968b1bd5b322fd58d78b947f5fd305c9d4aeb6a8be1d0ea96227bbad", 1_653_560),
}


def kokoro_voice_entry(vid: str) -> Optional[KokoroFile]:
    sha = _VOICE_SHA256.get(str(vid or ""))
    if not sha:
        return None
    return KokoroFile(f"voices/{vid}.bin", f"{_KOKORO_REPO}voices/{vid}.bin", sha, _VOICE_BYTES)


def kokoro_manifest() -> List[KokoroFile]:
    """Every file the Kokoro voice uses: the engine's, then each English voice."""
    return [*KOKORO_ENGINE_FILES.values(), *(kokoro_voice_entry(v) for v in sorted(_VOICE_SHA256))]


def kokoro_dir() -> Path:
    """Where ELI keeps the Kokoro files it fetched (writable)."""
    try:
        from eli.core.paths import models_dir
        return Path(models_dir()) / "tts" / "kokoro"
    except Exception:
        return Path("models") / "tts" / "kokoro"


def _kokoro_roots() -> List[Path]:
    """The folders the files are read from, first match wins: ELI's own models folder, then the
    copy a release build carries (read in place, never copied out), then a portable tree's."""
    roots = [kokoro_dir()]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle) / "tts_kokoro")
    try:
        from eli.core.paths import project_root
        roots.append(Path(project_root()) / "tts_kokoro")
    except Exception:
        log.debug("voice_assets: project root unknown", exc_info=True)
    seen, out = set(), []
    for r in roots:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def _present_in(root: Path, entry: KokoroFile) -> Optional[Path]:
    path = root / entry.rel
    try:
        if entry.unpack:
            marker = path / _MARKER
            if marker.is_file() and marker.read_text(encoding="utf-8").strip() == entry.sha256:
                return path
            return None
        if path.is_file() and path.stat().st_size == entry.size:
            return path
    except OSError:
        log.debug("voice_assets: %s unreadable", path, exc_info=True)
    return None


def _locate(entry: KokoroFile) -> Optional[Path]:
    for root in _kokoro_roots():
        found = _present_in(root, entry)
        if found is not None:
            return found
    return None


def kokoro_files() -> Optional[Dict[str, Path]]:
    """{model, tokenizer, spacy, crf} paths when every engine file is on this machine, else None."""
    out = {}
    for key, entry in KOKORO_ENGINE_FILES.items():
        found = _locate(entry)
        if found is None:
            return None
        out[key] = found
    return out


def kokoro_voice_file(vid: str) -> Optional[Path]:
    """The voice's file when it is on this machine, else None."""
    entry = kokoro_voice_entry(vid)
    return _locate(entry) if entry else None


def kokoro_ready(voice: str = "") -> bool:
    """True when the engine's files and the voice (the default one when none is named) are here."""
    from eli.perception.tts_kokoro import DEFAULT_VOICE
    return kokoro_files() is not None and kokoro_voice_file(voice or DEFAULT_VOICE) is not None


def _fetch(entry: KokoroFile, dest_root: Path) -> None:
    """Download one pinned file into dest_root, checked against its size and SHA-256 before it
    replaces anything; a wheel is unpacked to the one folder it names. Raises on any failure."""
    import urllib.request
    from eli.core import netguard
    target = dest_root / entry.rel
    part = target.parent / (target.name + ".part")
    digest, size = hashlib.sha256(), 0
    req = urllib.request.Request(entry.url, headers={"User-Agent": "ELI-voice-assets"})
    with netguard.allow_network(f"voice asset: {entry.rel}"):
        with netguard.guarded_urlopen(req, timeout=300) as r:
            # the folder is made once the download has started: a refused one leaves nothing behind
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)
    try:
        if size != entry.size or digest.hexdigest() != entry.sha256:
            raise ValueError(f"{entry.rel}: the download does not match its pinned checksum "
                             f"({size} bytes, sha256 {digest.hexdigest()[:12]}…)")
        if not entry.unpack:
            part.replace(target)
            return
        staging = target.parent / (target.name + ".unpacking")
        shutil.rmtree(staging, ignore_errors=True)
        try:
            with zipfile.ZipFile(part) as zf:
                for info in zf.infolist():
                    name = info.filename
                    if not name.startswith(entry.unpack) or name.endswith("/"):
                        continue
                    inner = Path(name[len(entry.unpack):])
                    if inner.is_absolute() or ".." in inner.parts:
                        raise ValueError(f"{entry.rel}: unsafe path in the archive: {name}")
                    out = staging / inner
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(zf.read(info))
            if not (staging / "config.cfg").is_file():
                raise ValueError(f"{entry.rel}: the archive does not hold {entry.unpack}")
            (staging / _MARKER).write_text(entry.sha256, encoding="utf-8")
            shutil.rmtree(target, ignore_errors=True)
            staging.replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)    # gone once moved; a half-unpacked one too
    finally:
        part.unlink(missing_ok=True)


def ensure_kokoro(voices: Optional[List[str]] = None, dest: Optional[Path] = None) -> Dict[str, Any]:
    """Fetch whichever Kokoro files are missing: the engine's and the named voices (every English
    voice when none are named), into ELI's models folder or `dest`. Offline-safe: files already
    present are not fetched again, and nothing touches the network when all are here."""
    wanted = list(KOKORO_ENGINE_FILES.values())
    names = list(voices) if voices is not None else sorted(_VOICE_SHA256)
    for vid in names:
        entry = kokoro_voice_entry(vid)
        if entry is None:
            return {"ok": False, "asset": "kokoro", "error": f"no Kokoro voice named {vid}"}
        wanted.append(entry)
    root = Path(dest) if dest else kokoro_dir()
    have = (lambda e: _present_in(root, e)) if dest else _locate
    missing = [e for e in wanted if have(e) is None]
    if not missing:
        return {"ok": True, "asset": "kokoro", "already_present": True, "fetched": []}
    fetched = []
    try:
        for entry in missing:
            _fetch(entry, root)
            fetched.append(entry.rel)
    except Exception as exc:
        log.warning("voice_assets: Kokoro file not fetched: %s", exc, exc_info=True)
        return {"ok": False, "asset": "kokoro", "fetched": fetched, "error": str(exc)}
    return {"ok": True, "asset": "kokoro", "already_present": False, "fetched": fetched}


# ── the voices a user can pick ─────────────────────────────────────────────────

def _voice_row(vid: str) -> Dict[str, Any]:
    from eli.perception.tts_kokoro import VOICES, describe
    meta = VOICES[vid]
    return {"id": vid, "name": meta["name"].lower(), "language": "en_US" if meta["accent"] == "a" else "en_GB",
            "language_name": "English", "country": "United States" if meta["accent"] == "a" else "United Kingdom",
            "gender": meta["gender"], "quality": meta["grade"], "size_mb": round(_VOICE_BYTES / (1 << 20), 1),
            "present": kokoro_voice_file(vid) is not None, "restricted": False,
            "recommended": meta["grade"][0] in "AB", "desc": describe(vid), "license": "Apache-2.0"}


def list_available_voices(language: str = "", installed_only: bool = False,
                          refresh: bool = False) -> "list[Dict[str, Any]]":
    """Every Kokoro voice, annotated for a picker: ``present`` (its file is here), ``quality`` (the
    grade Kokoro's authors give it), ``recommended`` (graded A or B). ``language`` filters on 'en',
    'en_US' or 'en_GB'. Local only: the list is the pinned one (`refresh` is accepted and changes
    nothing; there is no remote index to read)."""
    from eli.perception.tts_kokoro import VOICES
    rows = []
    for vid in VOICES:
        row = _voice_row(vid)
        if language and not (row["language"] == language or row["language"].startswith(f"{language}_")):
            continue
        if installed_only and not row["present"]:
            continue
        rows.append(row)
    rank = {vid: i for i, vid in enumerate(VOICES)}        # VOICES lists each kind best grade first
    rows.sort(key=lambda r: (not r["recommended"], r["language"], rank[r["id"]]))
    return rows


def download_voice(voice_id: str, mirror: bool = False) -> Dict[str, Any]:
    """Fetch one Kokoro voice (and the engine's files, when missing). `mirror` is accepted from
    callers of the earlier engine and changes nothing. Offline-safe and idempotent."""
    from eli.perception.tts_kokoro import voice_id as _kid
    vid = _kid(voice_id)
    if not vid:
        return {"ok": False, "asset": "kokoro", "voice": str(voice_id), "error": f"no Kokoro voice named {voice_id}"}
    res = ensure_kokoro([vid])
    return {**res, "voice": vid}


def voice_name_of(voice_id: str) -> str:
    """'bf_emma' -> 'emma'."""
    from eli.perception.tts_kokoro import VOICES, voice_id as _kid
    meta = VOICES.get(_kid(voice_id))
    return meta["name"].lower() if meta else ""


# Accent / language words a user might say, mapped to a voice language, so the conversational voice
# actions (DOWNLOAD_VOICE / SET_VOICE) never hard-code voice ids: "get me a British voice" resolves here.
_ACCENT_HINTS = {
    "british": "en_GB", "uk": "en_GB", "england": "en_GB", "english accent": "en_GB",
    "scottish": "en_GB", "scotland": "en_GB", "northern": "en_GB",
    "american": "en_US", "us": "en_US", "usa": "en_US", "united states": "en_US",
}


# Legacy spoken names → generic built-in voice styles (no copyrighted character presets).
_STYLE_SPOKEN_ALIASES = {
    "hal": "robotic",
    "jarvis": "refined",
    "glados": "synthetic",
    "tars": "robotic",
    "rick": "energetic",
}


def resolve_voice_query(text: str) -> Dict[str, Any]:
    """Turn a spoken voice request into a concrete choice, without hard-coding ids.

    Returns ``{"voice": <id or "">, "matches": [ids…], "kind": ...}``. Resolution: a natural
    (neural) voice request, then a style preset, then a voice id or name, then an accent or gender
    word narrowing the voices; else empty (the caller lists options).
    """
    raw = str(text or "").strip().lower()
    if not raw:
        return {"voice": "", "matches": [], "kind": "none"}
    fem = re.search(r"\b(female|woman|women|she|her|girl|lady)\b", raw)
    male = re.search(r"\b(male|man|men|he|him|guy|boy)\b", raw)

    # Natural neural voice ("use the natural/human/realistic voice"): the GPU engine's, gender-aware.
    if re.search(r"\b(natural|human|human[- ]like|realistic|lifelike|neural)\b", raw):
        try:
            from eli.perception import tts_xtts
            pick = "james" if (male and not fem) else "sophia"
            vid = f"{tts_xtts.NATURAL_PREFIX}{pick}"
            return {"voice": vid, "matches": [vid], "kind": "natural"}
        except Exception:
            log.debug("natural voice match failed", exc_info=True)

    for alias, style in _STYLE_SPOKEN_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            vid = f"char:{style}"
            return {"voice": vid, "matches": [vid], "kind": "character"}
    try:
        from eli.perception import voice_fx
        for c in voice_fx.list_characters():
            nm = str(c.get("name") or "").lower()
            if nm and re.search(rf"\b{re.escape(nm)}\b", raw):
                return {"voice": c["id"], "matches": [c["id"]], "kind": "character"}
    except Exception:
        log.debug("voice_fx match failed", exc_info=True)

    rows = list_available_voices()
    by_id = {r["id"].lower(): r["id"] for r in rows}
    for token in re.findall(r"[a-z0-9_]+", raw):
        if token in by_id:
            return {"voice": by_id[token], "matches": [by_id[token]], "kind": "id"}
    try:
        from eli.perception.tts_kokoro import voice_for_legacy
        for token in re.findall(r"[A-Za-z]{2}_[A-Za-z]{2}-[A-Za-z0-9_]+-(?:x_low|low|medium|high)", str(text)):
            vid = voice_for_legacy(token)
            if vid:
                return {"voice": vid, "matches": [vid], "kind": "id"}
    except Exception:
        log.debug("legacy voice id match failed", exc_info=True)
    for r in rows:
        if re.search(rf"\b{re.escape(r['name'])}\b", raw):
            return {"voice": r["id"], "matches": [r["id"]], "kind": "name"}
    try:
        from eli.perception.tts_kokoro import legacy_names
        for name, vid in legacy_names().items():
            if re.search(rf"\b{re.escape(name.replace('_', ' '))}\b|\b{re.escape(name)}\b", raw):
                return {"voice": vid, "matches": [vid], "kind": "name"}
    except Exception:
        log.debug("legacy voice name match failed", exc_info=True)

    cand = rows
    kind = "none"
    for word, prefix in sorted(_ACCENT_HINTS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", raw):
            cand, kind = [r for r in cand if r["language"].startswith(prefix)], "accent"
            break
    if fem or male:
        want = "female" if fem else "male"
        cand, kind = [r for r in cand if r["gender"] == want], ("accent" if kind == "accent" else "gender")
    if kind == "none":
        return {"voice": "", "matches": [], "kind": "none"}
    from eli.perception.tts_kokoro import VOICES
    rank = {vid: i for i, vid in enumerate(VOICES)}
    cand = sorted(cand, key=lambda r: (not r["present"], not r["recommended"], rank[r["id"]]))
    ids = [r["id"] for r in cand]
    return {"voice": ids[0] if ids else "", "matches": ids, "kind": kind}


def ensure_whisper() -> Dict[str, Any]:
    """Ensure the faster-whisper STT model is cached. faster-whisper downloads it on
    first construction; force CPU so the installer claims no VRAM, and gate the fetch."""
    os.environ.setdefault("ELI_WHISPER_DEVICE", "cpu")
    try:
        from eli.perception.local_whisper_stt import whisper_cache_ready
        from eli.perception import local_whisper_stt as W
        already = whisper_cache_ready()
        if already:
            os.environ.setdefault("ELI_WHISPER_LOCAL_ONLY", "1")
            W.preload_model()
            present = getattr(W, "_MODEL", None) is not None
            return {"ok": present, "asset": "whisper", "already_present": True,
                    **({} if present else {"error": "model cache present but load failed"})}
        from eli.core import netguard
        with netguard.allow_network("voice asset: whisper model"):
            prev_local = os.environ.get("ELI_WHISPER_LOCAL_ONLY")
            os.environ["ELI_WHISPER_LOCAL_ONLY"] = "0"
            try:
                W.preload_model()
            finally:
                if prev_local is None:
                    os.environ.pop("ELI_WHISPER_LOCAL_ONLY", None)
                else:
                    os.environ["ELI_WHISPER_LOCAL_ONLY"] = prev_local
        present = getattr(W, "_MODEL", None) is not None
        err = None if present else "model not loaded (is faster-whisper installed?)"
        return {"ok": present, "asset": "whisper",
                "already_present": False, **({} if present else {"error": err})}
    except ImportError:
        return {"ok": False, "asset": "whisper",
                "error": "faster-whisper not installed — re-run INSTALL_ELI.sh"}
    except Exception as e:
        log.debug("voice_assets: whisper preload failed", exc_info=True)
        return {"ok": False, "asset": "whisper", "error": str(e)}


def ensure_voice_assets() -> Dict[str, Any]:
    """Ensure speech out (Kokoro's files) and speech in (the whisper model) are present: the
    installer's and first run's online step. Best-effort; a failure is reported, never raised."""
    return {"kokoro": ensure_kokoro(), "whisper": ensure_whisper()}


def _main(argv: Optional[List[str]] = None) -> int:
    """`python -m eli.runtime.voice_assets` fetches both; `--kokoro-to DIR` fetches only Kokoro's
    files into DIR, checked as always (the release workflow does that for the bundle)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--kokoro-to"] and len(args) == 2:
        res = ensure_kokoro(dest=Path(args[1]))
        print(f"[{'OK' if res.get('ok') else 'FAIL'}] kokoro -> {args[1]}: "
              + (", ".join(res.get("fetched") or []) or "already present")
              + ("" if res.get("ok") else f" ({res.get('error')})"))
        return 0 if res.get("ok") else 1
    res = ensure_voice_assets()
    ok_all = True
    for asset in ("kokoro", "whisper"):
        r = res.get(asset) or {}
        if r.get("ok"):
            print(f"[OK] voice/{asset}: {'already present' if r.get('already_present') else 'downloaded'}")
        else:
            ok_all = False
            print(f"[WARN] voice/{asset}: {r.get('error', 'unavailable')}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(_main())
