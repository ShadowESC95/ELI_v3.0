#!/usr/bin/env python3
"""
tts_router.py — TTS router for ELI.

The active voice decides the path, and there is no silent fallback from one kind to another:
- character, cloned and natural voices (char:/clone:/natural:) are rendered by synthesize_wav;
  natural and cloned voices by Qwen3-TTS on a GPU with room for it (tts_qwen3), and when it cannot
  speak them, the Kokoro default voice speaks and neural_fallback_state() says so;
- a system voice (sys:) is spoken by pyttsx3, only when that voice is the one chosen;
- every other voice is a Kokoro-82M voice (tts_kokoro), rendered on the CPU, one sentence ahead of
  the one playing.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from eli.utils.platform_compat import play_wav_with_player as _platform_play_wav

from eli.cognition.emotion_palette import NEUTRAL_VOICE as _NEUTRAL_VOICE
from eli.perception import tts_kokoro as _kokoro
from eli.utils.log import get_logger
log = get_logger(__name__)

# ELI's default voice (Kokoro's one A-graded voice); a saved voice of the earlier engine is mapped
# onto its nearest Kokoro voice (tts_kokoro.voice_for_legacy).
_DEFAULT_VOICE = _kokoro.DEFAULT_VOICE
_SYSTEM_VOICE_PREFIX = "sys:"

# Delivery: the tone palette's values (eli/cognition/emotion_palette). Kokoro takes a speed, the
# palette's pace (length_scale, >1 slower) inverted; the pause after each sentence is added between
# the sentences; pitch is shifted afterwards (_apply_tone_pitch). noise_scale and noise_w (the
# earlier engine's variation controls) have no Kokoro counterpart; the palette still reads them for
# a cloned voice's exaggeration (tone_adaptor.voice_exaggeration).
_PROSODY_DEFAULTS = {k: v for k, v in _NEUTRAL_VOICE.items() if k != "pitch"}


def _prosody() -> Dict[str, float]:
    """The delivery for the next reply: the neutral defaults, the saved tts_<key> settings, the current
    tone (tone_adaptor), then ELI_TTS_<KEY> from the environment. The owner's saved pace ("speak
    slower") is kept under any tone, as its share of the neutral pace: the tone's values replaced it,
    neutral's own pace included, so a saved pace was never heard. {} when ELI_TTS_PROSODY=0 (the
    engine's own pace). Never raises."""
    if os.environ.get("ELI_TTS_PROSODY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {}
    vals: Dict[str, Any] = dict(_PROSODY_DEFAULTS)
    saved_pace = None
    try:
        from eli.core.runtime_settings import load_settings
        s = load_settings() or {}
        for k in vals:
            v = s.get(f"tts_{k}")
            if v is not None:
                vals[k] = v
        saved_pace = s.get("tts_length_scale")
    except Exception:
        log.debug("[TTS] prosody settings read failed", exc_info=True)
    # Emotional delivery: the current tone overrides the base pace and sentence pause so ELI's VOICE
    # carries the emotion: slow with long pauses for sad, fast with short ones for ecstatic.
    try:
        from eli.cognition import tone_adaptor
        tone = tone_adaptor.voice_prosody() or {}
        for k, v in tone.items():
            if k in vals and v is not None:
                vals[k] = v
        neutral = float(_PROSODY_DEFAULTS.get("length_scale") or 1.0)
        if saved_pace is not None and tone.get("length_scale") is not None and neutral > 0:
            vals["length_scale"] = float(tone["length_scale"]) * float(saved_pace) / neutral
    except Exception:
        log.debug("[TTS] tone prosody merge skipped", exc_info=True)
    for k in vals:  # env wins over everything (explicit user pin)
        ev = os.environ.get(f"ELI_TTS_{k.upper()}", "").strip()
        if ev:
            vals[k] = ev
    out: Dict[str, float] = {}
    for k, v in vals.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            log.debug("[TTS] bad prosody value for %s: %r", k, v)
    return out


def gain_db() -> float:
    """How much louder (or, below zero, softer) than the engine's own level ELI speaks, in dB: the
    saved tts_gain_db ("speak softer"), else ELI_TTS_GAIN_DB, else 0."""
    raw = os.environ.get("ELI_TTS_GAIN_DB", "").strip()
    try:
        from eli.core.runtime_settings import load_settings
        saved = (load_settings() or {}).get("tts_gain_db")
        if saved is not None and not raw:
            raw = str(saved)
    except Exception:
        log.debug("[TTS] gain setting read failed", exc_info=True)
    try:
        return float(raw or 0.0)
    except ValueError:
        log.debug("[TTS] bad gain value %r", raw)
        return 0.0


def _with_gain(samples):
    """`samples` at the saved level, kept within full scale."""
    import numpy as _np
    db = gain_db()
    x = _np.asarray(samples, dtype=_np.float32)
    if abs(db) < 1e-6:
        return x
    return _np.clip(x * float(10.0 ** (db / 20.0)), -1.0, 1.0)


def _speed(prosody: Dict[str, float]) -> float:
    """Kokoro's speed from the palette's pace (length_scale 1.1 = ten per cent slower)."""
    ls = prosody.get("length_scale") or 1.0
    return 1.0 / ls if ls > 0 else 1.0


class Playback:
    """What playing one WAV did: true when it played, `via` saying where (a PortAudio device that
    goes through the sound server, or the system player that played it), `detail` why not."""
    __slots__ = ("ok", "via", "detail")

    def __init__(self, ok: bool, via: str = "", detail: str = ""):
        self.ok, self.via, self.detail = bool(ok), str(via or ""), str(detail or "")

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"Playback(ok={self.ok}, via={self.via!r}, detail={self.detail!r})"


_LAST_PLAYBACK: Dict[str, Any] = {}

# PortAudio's ALSA devices that are the sound server itself (its ALSA plugin). "default" and
# "sysdefault" reach the server only through that plugin, which lists itself under these names; a
# "default" without them is the card's own mixing, on the hardware the server holds.
_SERVER_PCM_NAMES = ("pipewire", "pulse")
# Lip-sync reads the voice's loudness this often (seconds): about a video frame and a half.
_AMPLITUDE_BLOCK_S = 0.04


def last_playback() -> Dict[str, Any]:
    """The last speech playback: {"ok", "via", "detail", "ts"}; {} before any."""
    return dict(_LAST_PLAYBACK)


def _portaudio_output(sd) -> Optional[tuple]:
    """(device, name) of the PortAudio output that plays through the desktop's sound server; device
    None is PortAudio's own default. None when no listed device does: then the system's player
    (platform_compat) plays, through the server. A raw ALSA card the server holds is never chosen:
    it refused to open while music played ("Device unavailable") and took the voice silently
    otherwise, in a build whose PortAudio listed only the cards."""
    from eli.utils.platform_compat import sound_server
    server = sound_server()
    if server == "macos":
        return (None, "the system output")                 # Core Audio: every device is mixed
    if not server:
        return (None, "PortAudio's default output")      # nothing holds the cards
    devices = list(sd.query_devices())
    if server == "windows":
        default = sd.query_devices(kind="output")
        apis = list(sd.query_hostapis())
        api = apis[int(default.get("hostapi", 0))]["name"] if apis else ""
        if "WDM-KS" not in api:                           # kernel streaming is the only route past the mixer
            return (None, f"'{default.get('name', '')}' ({api})")
        for a in apis:
            if "WDM-KS" not in a["name"] and int(a.get("default_output_device", -1)) >= 0:
                idx = int(a["default_output_device"])
                return (idx, f"'{devices[idx]['name']}' ({a['name']})")
        return None
    for name in _SERVER_PCM_NAMES:
        for idx, dev in enumerate(devices):
            if dev.get("name") == name and int(dev.get("max_output_channels", 0)) > 0:
                return (idx, f"PortAudio '{name}' ({server})")
    return None


def _loudness(chunk) -> float:
    import numpy as _np
    return min(1.0, float(_np.sqrt(_np.mean(chunk ** 2))) * 4.0) if len(chunk) else 0.0


def _amplitude_from_samples(data, sr: int, es, stop) -> None:
    """Lip-sync without a callback stream: the voice's loudness block by block, on the clock of
    the player that plays it, until `stop` is set or the samples end."""
    import time as _time
    block = max(1, int(sr * _AMPLITUDE_BLOCK_S))
    start = _time.monotonic()
    for i, pos in enumerate(range(0, len(data), block)):
        if stop.is_set():
            break
        es.set_amplitude(_loudness(data[pos:pos + block]))
        wait = start + (i + 1) * _AMPLITUDE_BLOCK_S - _time.monotonic()
        if wait > 0 and stop.wait(wait):
            break
    es.set_amplitude(0.0)


def _play_wav_blocking(wav_path, *, post_play_tail: float | None = None) -> Playback:
    """Blocking WAV playback through the desktop's sound server, with expression_state amplitude
    for lip-sync: a PortAudio stream on the server's own device when one is listed, the system's
    player otherwise. Returns a Playback, true only when it played, saying where."""
    import os as _os
    import threading as _threading
    import time as _time
    from pathlib import Path as _Path

    path = _Path(wav_path)
    if not path.is_file() or path.stat().st_size < 100:
        return _record_playback(Playback(False, detail="no audio to play"))

    _es = None
    try:
        from eli.cognition import expression_state as _es_mod
        _es = _es_mod
    except Exception:
        log.debug("[TTS] expression_state unavailable for amplitude lip-sync", exc_info=True)

    data, sr, why = None, 0, ""
    try:
        from eli.perception.audio_samples import read_mono as _read_mono
        data, sr = _read_mono(path)
        if data.size == 0:
            data, why = None, "the voice's audio was empty"
    except Exception as exc:
        why = f"its samples could not be read ({exc})"
        log.debug("[TTS] samples unreadable for the PortAudio stream and lip-sync", exc_info=True)

    if data is not None:
        try:
            import sounddevice as _sd
            route = _portaudio_output(_sd)
        except Exception as exc:
            route, why = None, f"PortAudio unavailable ({exc})"
            log.debug("[TTS] PortAudio unavailable", exc_info=True)
        if route is None and not why:
            why = "no PortAudio device goes through the sound server"
        if route is not None:
            device, label = route
            try:
                pos = 0
                out = data

                def _callback(outdata, frames, _time_info, status):
                    nonlocal pos
                    if status:
                        log.debug(f"[TTS] sounddevice status: {status}")
                    end = min(pos + frames, out.size)
                    chunk = out[pos:end]
                    if chunk.size == 0:
                        outdata.fill(0)
                        raise _sd.CallbackStop()
                    outdata[:chunk.size, 0] = chunk
                    if chunk.size < frames:
                        outdata[chunk.size:, 0] = 0
                        pos = out.size
                        raise _sd.CallbackStop()
                    pos = end
                    if _es is not None:
                        _es.set_amplitude(_loudness(chunk))

                # The device's own rate from the start: a device that takes only that (a USB headset's ALSA
                # device refused 22,050 Hz) failed first on every reply, three ALSA errors each time.
                info = _sd.query_devices(device) if device is not None else _sd.query_devices(kind="output")
                _dev_sr = int(info["default_samplerate"] or 0)
                rate = int(sr)
                if _dev_sr > 0 and _dev_sr != rate:
                    from eli.perception.audio_samples import resample as _resample
                    out = _resample(data, rate, _dev_sr)
                    rate = _dev_sr
                _stream = _sd.OutputStream(samplerate=rate, channels=1, callback=_callback, device=device)
                with _stream:
                    _sd.sleep(int((out.size / float(rate)) * 1000) + 80)
                if _es is not None:
                    _es.set_amplitude(0.0)
                tail = post_play_tail
                if tail is None:
                    tail = float(_os.environ.get("ELI_TTS_POST_PLAY_TAIL_SEC", "0.35"))
                if tail > 0:
                    _time.sleep(tail)
                return _record_playback(Playback(True, label))
            except Exception as exc:
                why = f"{label} failed ({exc})"
                log.debug("[TTS] PortAudio playback failed; the system player plays instead", exc_info=True)

    stop = _threading.Event()
    feeder = None
    if _es is not None and data is not None:
        feeder = _threading.Thread(target=_amplitude_from_samples, args=(data, int(sr), _es, stop),
                                   daemon=True, name="eli-tts-lipsync")
        feeder.start()
    try:
        player = _platform_play_wav(path, post_play_tail=post_play_tail)
    finally:
        stop.set()
        if feeder is not None:
            feeder.join(timeout=1.0)
    if player:
        return _record_playback(Playback(True, f"the system player {player}", why))
    return _record_playback(Playback(False, detail=(why + "; " if why else "") + "no system player could play it"))


def _record_playback(result: Playback) -> Playback:
    global _LAST_PLAYBACK
    import time as _time
    _LAST_PLAYBACK = {"ok": result.ok, "via": result.via, "detail": result.detail, "ts": _time.time()}
    return result


def _play_wav_bytes(wav_bytes: bytes) -> Playback:
    """Play WAV bytes on any supported OS without requiring Linux audio CLIs."""
    if not wav_bytes:
        return Playback(False, detail="no audio to play")
    path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="eli_tts_", suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            path = f.name
        played = _play_wav_blocking(path)
        _log_played("[TTS] voice audio", len(wav_bytes), played)
        return played
    except Exception as exc:
        log.warning("[TTS] speech not played: %s", exc, exc_info=True)
        return Playback(False, detail=str(exc))
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                log.debug("[TTS] temporary voice file not removed: %s", path, exc_info=True)


def _log_played(what: str, size: int, played) -> None:
    """One line per played reply part: where it played, or that it did not and why."""
    if played:
        log.debug(f"{what} bytes={size} played through {getattr(played, 'via', '') or 'an unnamed route'}")
    else:
        log.warning(f"{what} bytes={size} NOT played: {getattr(played, 'detail', '') or 'playback failed'}")


# Enumerating system voices is done OUT OF PROCESS on purpose — see below.
_SYSTEM_VOICES_CACHE: "list[str] | None" = None

_ENUM_SNIPPET = (
    "import pyttsx3\n"
    "e = pyttsx3.init()\n"
    "for v in (e.getProperty('voices') or []):\n"
    "    i = getattr(v, 'id', None)\n"
    "    if i:\n"
    "        print(i)\n"
    "try:\n"
    "    e.stop()\n"
    "except Exception:\n"
    "    pass\n"
)


def _list_system_voices(refresh: bool = False) -> list[str]:
    """OS-installed voices via pyttsx3 (SAPI on Windows, NSS on macOS, espeak on
    Linux), prefixed ``sys:``.

    Runs in a SUBPROCESS, deliberately. espeak-ng has a fixed ``N_VOICES_LIST``
    of 350; on a box with more installed voice/variant combinations than that,
    ``espeak_ListVoices`` walks off the end of the array and corrupts the heap —
    "double free or corruption (!prev)" then ``abort()``. That is a native crash,
    so a Python ``except`` around it catches NOTHING: it took the whole GUI down
    mid-session (observed on Linux with a large voice set installed). Isolating
    the enumeration means the worst case is an empty list from a dead child
    instead of ELI core-dumping. Result is cached for the process.
    """
    global _SYSTEM_VOICES_CACHE
    if _SYSTEM_VOICES_CACHE is not None and not refresh:
        return list(_SYSTEM_VOICES_CACHE)

    out: list[str] = []
    try:
        # Probe the binding in-process first. Importing pyttsx3 is safe (the espeak overflow is in
        # init()/getProperty(), not import), and it keeps the child honest: if the caller has no real
        # pyttsx3 (absent, or a stub in the mocked test lane) there are no system voices, and we must
        # not shell out to a fresh interpreter that bypasses the stub and reports the host's voices.
        import pyttsx3 as _p3
        if type(_p3).__name__ == "MagicMock" or type(getattr(_p3, "init", None)).__name__ == "MagicMock":
            _SYSTEM_VOICES_CACHE = []
            return []
    except Exception:
        log.debug("[TTS] pyttsx3 unavailable — no system voices", exc_info=True)
        _SYSTEM_VOICES_CACHE = []
        return []

    try:
        import subprocess as _sp
        import sys as _sys
        proc = _sp.run([_sys.executable, "-c", _ENUM_SNIPPET],
                       stdout=_sp.PIPE, stderr=_sp.DEVNULL, timeout=20)
        if proc.returncode == 0:
            for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
                vid = line.strip()
                if vid:
                    out.append(f"{_SYSTEM_VOICE_PREFIX}{vid}")
        else:
            # Negative return code = killed by a signal (SIGABRT/SIGSEGV) — the
            # espeak overflow above. Report it; do not retry in-process.
            log.debug("[TTS] system-voice enumeration exited %s (native crash "
                      "isolated in the child — no system voices listed)",
                      proc.returncode)
    except Exception:
        log.debug("[TTS] system-voice enumeration unavailable", exc_info=True)

    _SYSTEM_VOICES_CACHE = list(out)
    return out


def _allow_robotic_voices() -> bool:
    """Opt back in to the operating system's own `sys:` voices (default: hidden)."""
    return os.environ.get("ELI_TTS_ALLOW_ROBOTIC", "0").strip().lower() in {"1", "true", "yes", "on"}


def _kokoro_voices() -> List[str]:
    """Kokoro's voices that are on this machine; every one of them before the first fetch (choosing
    one fetches it with the engine, on first use)."""
    present = [v for v in _kokoro.VOICES if _voice_file(v) is not None]
    return present or list(_kokoro.VOICES)


def _voice_file(vid: str):
    try:
        from eli.runtime.voice_assets import kokoro_voice_file
        return kokoro_voice_file(vid)
    except Exception:
        log.debug("[TTS] voice file lookup failed", exc_info=True)
        return None


def _natural_voices() -> List[str]:
    """The built-in neural voices (natural:<id>): Qwen3-TTS's speakers on a machine whose GPU runs it,
    else XTTS-v2's when that extra is installed, else none."""
    try:
        from eli.perception import tts_qwen3
        found = tts_qwen3.list_natural_voices()
        if found:
            return found
    except Exception:
        log.debug("[TTS] Qwen3-TTS voices unavailable", exc_info=True)
    try:
        from eli.perception import tts_xtts
        return list(tts_xtts.list_natural_voices())
    except Exception:
        log.debug("[TTS] XTTS voices unavailable", exc_info=True)
        return []


def list_voices() -> list[str]:
    """Runnable voices: Kokoro's (by grade, American then British), then the style presets (`char:`),
    cloned voices (`clone:`) and natural neural voices (`natural:`). The operating system's own
    voices (`sys:`) are hidden unless ``ELI_TTS_ALLOW_ROBOTIC=1``, or unless nothing else can speak
    (then they return as a last resort so ELI is never mute)."""
    voices: list[str] = []
    seen: set[str] = set()
    if _kokoro.available():
        for vid in _kokoro_voices():
            seen.add(vid)
            voices.append(vid)
    try:
        from eli.perception import voice_fx
        for c in voice_fx.list_characters():
            if c["id"] not in seen:
                seen.add(c["id"])
                voices.append(c["id"])
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        from eli.perception import tts_xtts
        for c in tts_xtts.list_clones():
            if c["id"] not in seen:
                seen.add(c["id"])
                voices.append(c["id"])
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    for nid in _natural_voices():
        if nid not in seen:
            seen.add(nid)
            voices.append(nid)
    if _allow_robotic_voices() or not voices:
        voices.extend(sv for sv in _list_system_voices() if sv not in seen)
    return voices


def voice_display_name(voice_id: str) -> str:
    """Human label for UI/docs. ``char:`` presets are style names, not characters."""
    vid = str(voice_id or "").strip()
    if not vid:
        return vid
    if vid.startswith("char:"):
        try:
            from eli.perception import voice_fx
            spec = voice_fx.get_preset(vid) or {}
            label = spec.get("label") or vid.replace("char:", "").replace("_", " ").title()
            return f"Style: {label}"
        except Exception:
            return vid
    if vid.startswith("clone:"):
        return f"Clone: {vid[6:]}"
    if vid.startswith("natural:"):
        return f"Neural: {vid[8:]}"
    if vid.startswith("sys:"):
        return f"System: {vid[4:]}"
    if _kokoro.voice_id(vid):
        return f"{_kokoro.describe(vid)} [{_kokoro.voice_id(vid)}]"
    return vid


def resolve_voice_id(selection: str) -> str:
    """Map a combo display string or raw id back to the stored voice id."""
    sel = str(selection or "").strip()
    if not sel:
        return sel
    if sel.startswith(("char:", "clone:", "natural:", "sys:")) or _kokoro.voice_id(sel):
        return _kokoro.voice_id(sel) or sel
    for vid in list_voices():
        if sel == vid or sel == voice_display_name(vid):
            return vid
    return sel


def _is_system_voice(name: str) -> bool:
    return str(name or "").startswith(_SYSTEM_VOICE_PREFIX)


def _system_voice_id(name: str) -> str:
    return str(name or "")[len(_SYSTEM_VOICE_PREFIX):]


def kokoro_voice(voice_name: str) -> str:
    """The Kokoro voice that speaks for `voice_name`: itself when it is one, the nearest one for a
    voice saved for ELI's earlier engine, else the default."""
    return _kokoro.voice_id(voice_name) or _kokoro.voice_for_legacy(voice_name) or _DEFAULT_VOICE


# The voice ELI wrote into every settings file as its default before the Kokoro voice (runtime_settings),
# so a saved one is no choice of the user's: it reads as no voice chosen, and the engine's default speaks.
_RETIRED_DEFAULT_VOICES = frozenset({"en_US-amy-medium"})


def _engine_setting() -> str:
    """The saved voice engine: 'auto' (Qwen3-TTS where the GPU has room, Kokoro elsewhere), 'kokoro'
    or 'natural'. The earlier engine's 'piper', the default every install had, reads as 'auto'."""
    try:
        from eli.core.runtime_settings import load_settings
        val = str((load_settings() or {}).get("tts_engine") or "auto").strip().lower()
    except Exception:
        val = "auto"
    return {"piper": "auto"}.get(val, val)


_RUNTIME_ASKED = False


def _natural_default() -> str:
    """Qwen3-TTS's default natural voice where this machine runs it, else ''. Where the GPU has room but
    its runtime is not made yet, it is made in the background, once a run, when the network is allowed
    (eli/perception/qwen3_runtime); Kokoro speaks meanwhile."""
    global _RUNTIME_ASKED
    try:
        from eli.perception import tts_qwen3
        if tts_qwen3.usable("custom"):
            return "natural:serena"
        if not _RUNTIME_ASKED and not tts_qwen3.installed():
            _RUNTIME_ASKED = True
            from eli.core import netguard
            if not netguard.should_block_network():
                from eli.perception import qwen3_runtime
                qwen3_runtime.make_if_room(background=True)
    except Exception:
        log.debug("[TTS] Qwen3-TTS check failed", exc_info=True)
    return ""


def get_active_voice() -> str:
    """The voice that speaks: ELI_TTS_VOICE, else the saved tts_voice (a voice saved for the earlier
    engine maps onto its nearest Kokoro voice; the default it wrote for everyone counts as none), else the
    engine's default: Qwen3-TTS's natural voice when the engine is 'auto' or 'natural' and this machine's
    GPU runs it, Kokoro's otherwise."""
    env = (os.environ.get("ELI_TTS_VOICE", "") or os.environ.get("ELI_PIPER_VOICE", "")).strip()
    chosen = env
    if not chosen:
        try:
            from eli.core.runtime_settings import load_settings
            chosen = str((load_settings() or {}).get("tts_voice", "") or "").strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        if chosen in _RETIRED_DEFAULT_VOICES:
            chosen = ""
    if chosen:
        if chosen.startswith(("char:", "clone:", "natural:", "sys:")):
            return chosen
        return kokoro_voice(chosen)
    if _engine_setting() in {"auto", "natural"}:
        natural = _natural_default()
        if natural:
            return natural
    return _DEFAULT_VOICE


def set_active_voice(voice_name: str) -> None:
    """Persist the selected voice to settings."""
    os.environ["ELI_TTS_VOICE"] = voice_name
    try:
        from eli.core.runtime_settings import save_settings
        save_settings({"tts_voice": voice_name})
    except Exception:
        log.debug("suppressed exception", exc_info=True)


# ── a voice for the reply's language (eli/perception/reply_language) ─────────
# The languages Qwen3-TTS speaks (its model cards).
_QWEN_LANGUAGES = frozenset({"zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"})
_SPD_PREFIX = "sys:spd:"
_SYSTEM_BY_LANGUAGE: Optional[Dict[str, List[str]]] = None
_NO_VOICE_SAID: set = set()

# The operating system's own voices with the languages they speak, read OUT OF PROCESS like the voice list
# above (_list_system_voices): one "id<TAB>language" line each. SAPI names a language by its Windows LCID
# (hex), NSSpeechSynthesizer by a locale.
_LANG_SNIPPET = (
    "import locale, pyttsx3\n"
    "e = pyttsx3.init()\n"
    "for v in (e.getProperty('voices') or []):\n"
    "    for l in (getattr(v, 'languages', None) or []):\n"
    "        l = l.decode('utf-8', 'replace') if isinstance(l, bytes) else str(l)\n"
    "        l = l.strip().lstrip('\\x05')\n"
    "        try:\n"
    "            l = locale.windows_locale.get(int(l, 16), l)\n"
    "        except ValueError:\n"
    "            pass\n"
    "        print(f'{v.id}\\t{l[:2].lower()}')\n"
)


def _system_voices_by_language() -> Dict[str, List[str]]:
    """{language: [sys: voice ids]} of the operating system's own voices, read once a run: on Linux the
    desktop's speech-dispatcher (spd-say, a program of its own; ELI loads no speech library of the system's
    into its process), on Windows (SAPI) and macOS their own voices through pyttsx3, in a child process."""
    global _SYSTEM_BY_LANGUAGE
    if _SYSTEM_BY_LANGUAGE is not None:
        return _SYSTEM_BY_LANGUAGE
    found: Dict[str, List[str]] = {}
    import subprocess
    try:
        if sys.platform.startswith("linux"):
            if shutil.which("spd-say"):
                listed = subprocess.run(["spd-say", "-L"], capture_output=True, text=True, timeout=20).stdout
                for line in listed.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 2 and re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9-]+)?", parts[-2]):
                        lang = parts[-2].split("-", 1)[0]
                        found.setdefault(lang, [f"{_SPD_PREFIX}{lang}"])
        elif _list_system_voices():
            listed = subprocess.run([sys.executable, "-c", _LANG_SNIPPET], capture_output=True, text=True,
                                    timeout=20).stdout
            for line in listed.splitlines():
                vid, _, lang = line.partition("\t")
                if vid and lang:
                    found.setdefault(lang, []).append(f"{_SYSTEM_VOICE_PREFIX}{vid}")
    except Exception:
        log.debug("[TTS] the system's voices by language are unknown", exc_info=True)
    _SYSTEM_BY_LANGUAGE = found
    return found


def voice_language(voice: str) -> str:
    """The language (ISO 639-1) a voice reads: a Kokoro voice's own (its accent); English for any other
    voice built on Kokoro (a character voice, the default), which is what they read."""
    parts = _kokoro.blend_parts(kokoro_voice(voice))         # a blend reads as its first voice
    accent = _kokoro.VOICES.get(parts[0] if parts else "", {}).get("accent") or "a"
    return _kokoro.ACCENT_LANGUAGE.get(accent, "en")


def voice_for_reply(text: str, active: str) -> str:
    """The voice that speaks `text`: `active`, unless the reply is in a language `active` does not speak.
    Then, in this order: Qwen3-TTS where this machine runs it (ten languages), a Kokoro voice of that
    language (Mandarin), the operating system's own voice for it, else `active` reading it as best it can
    (said once per language in the log). A natural, cloned or system voice is kept as chosen."""
    if str(active).startswith(("natural:", "clone:", "sys:")):
        return active
    try:
        from eli.perception.reply_language import language_of
        lang = language_of(text)
    except Exception:
        log.debug("[TTS] reply language unknown", exc_info=True)
        return active
    if lang == voice_language(active):
        return active
    try:
        from eli.perception import tts_qwen3
        if lang in _QWEN_LANGUAGES and tts_qwen3.usable("custom"):
            return "natural:serena"
    except Exception:
        log.debug("[TTS] Qwen3-TTS check failed", exc_info=True)
    if lang == "en":
        return _DEFAULT_VOICE
    here = [v for v in _kokoro.speaks(lang) if _voice_file(v) is not None]
    if here:
        return here[0]
    system = _system_voices_by_language().get(lang)
    if system:
        return system[0]
    if lang not in _NO_VOICE_SAID:
        _NO_VOICE_SAID.add(lang)
        log.warning("[TTS] no voice on this machine speaks '%s'; %s reads the reply as best it can", lang, active)
    return active


def _neural_engine_available() -> bool:
    """True when a natural voice can synthesise: Qwen3-TTS on this GPU, or XTTS-v2 installed."""
    try:
        from eli.perception import tts_qwen3
        if tts_qwen3.usable("custom"):
            return True
    except Exception:
        log.debug("[TTS] Qwen3-TTS check failed", exc_info=True)
    try:
        from eli.perception import tts_xtts
        return bool(tts_xtts.xtts_available())
    except Exception:
        return False


def _clone_backend_available(which: str) -> bool:
    """Report whether a clone backend can run — for diagnostics/UI. Never raises."""
    try:
        if which == "qwen3":
            from eli.perception import tts_qwen3
            return bool(tts_qwen3.usable("base"))
        if which == "chatterbox":
            from eli.perception import tts_chatterbox
            return bool(tts_chatterbox.chatterbox_available())
        from eli.perception import tts_xtts
        return bool(tts_xtts.xtts_available())
    except Exception:
        return False


def available_backends() -> dict:
    """What can speak on this machine, for the diagnostics, the voice panel and the voice status
    answer: the engine that speaks the active voice, whether Kokoro's files are here (and why it did
    not load when it did not), the neural engines, and the last playback."""
    installed = list_voices()
    active = get_active_voice()
    files = None
    try:
        from eli.runtime.voice_assets import kokoro_files
        files = kokoro_files()
    except Exception:
        log.debug("[TTS] Kokoro files lookup failed", exc_info=True)
    if active.startswith(("natural:", "clone:")):
        from eli.perception import tts_qwen3
        engine = "qwen3" if tts_qwen3.usable("base" if active.startswith("clone:") else "custom") else "neural"
    elif _is_system_voice(active):
        engine = "system"
    else:
        engine = "kokoro"
    return {
        "engine": engine,
        "kokoro": _kokoro.available(),
        "kokoro_ready": files is not None,
        "kokoro_error": _kokoro.load_error(),
        "voices": installed,
        "system_voices": [v for v in installed if _is_system_voice(v)],
        "active_voice": active,
        "speaking_voice": kokoro_voice(active) if engine == "kokoro" else active,
        "default_voice": _DEFAULT_VOICE,
        "neural_available": _neural_engine_available(),
        # the model file the default engine reads, when it is here
        "active_model": str(files["model"]) if files else None,
        "pyttsx3": True,
        # neural engines: Qwen3-TTS (GPU extra) for natural and cloned voices, then Chatterbox (MIT),
        # then XTTS-v2 (legacy)
        "qwen3": _clone_backend_available("qwen3"),
        "chatterbox": _clone_backend_available("chatterbox"),
        "xtts": _clone_backend_available("xtts"),
        "last_playback": last_playback(),
    }


def _tts_chunks(text, max_chars=None):
    import os as _os
    import re as _re

    if max_chars is None:
        if _os.environ.get("ELI_TTS_CHUNK_CHARS"):
            max_chars = _os.environ.get("ELI_TTS_CHUNK_CHARS", "360")
        else:
            try:
                from eli.runtime.runtime_policy import tts_chunk_chars as _eli_tts_chunk_chars
                max_chars = _eli_tts_chunk_chars(360)
            except Exception:
                max_chars = "360"
    max_chars = int(max_chars)
    text = str(text or "").strip()
    if not text:
        return []

    text = _re.sub(r'^\s*(?:As\s+ELI|ELI)\s*:\s*', '', text, flags=_re.I).strip()

    sentences = _re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    cur = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue

        if len(sent) > max_chars:
            words = sent.split()
            tmp = ""
            for w in words:
                if len(tmp) + len(w) + 1 > max_chars and tmp:
                    if cur:
                        chunks.append(cur)
                        cur = ""
                    chunks.append(tmp)
                    tmp = w
                else:
                    tmp = (tmp + " " + w).strip()
            sent = tmp

        if len(cur) + len(sent) + 1 <= max_chars:
            cur = (cur + " " + sent).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = sent

    if cur:
        chunks.append(cur)

    return chunks


# Neural-voice fallback state (observable, not silent). A `natural:` / `clone:` voice that
# couldn't synthesise fell through to the default voice with no log line, so Settings said "Active
# voice: natural:sophia" while every reply was the default voice, and a user who dropped in a clip
# couldn't tell the clone was registered but unused. Record why so the panel and console match
# the speakers.
_NEURAL_FALLBACK: Dict[str, Any] = {"active": False, "requested": "", "reason": ""}


def _neural_unavailable_reason() -> str:
    """Why a neural voice cannot synthesise right now, in one line."""
    try:
        from eli.perception import qwen3_runtime, tts_qwen3
        if qwen3_runtime.state().get("state") == "making":
            return "its runtime is being made (a download of a few GB, once); the default voice speaks meanwhile"
        if tts_qwen3.installed() and not tts_qwen3.usable("custom") and not tts_qwen3.usable("base"):
            return "no GPU on this machine has room for Qwen3-TTS beside the language model"
        if tts_qwen3.last_error():
            return f"Qwen3-TTS failed: {tts_qwen3.last_error()}"
    except Exception:
        log.debug("[TTS] Qwen3-TTS state unknown", exc_info=True)
    if _clone_backend_available("qwen3") or _clone_backend_available("chatterbox") or _clone_backend_available("xtts"):
        return "the neural engine is present but produced no audio for this voice"
    return ("the neural voice engine is not on this machine (ELI makes its runtime only where an NVIDIA "
            "GPU has room for it beside the language model)")


def _note_neural_fallback(requested) -> None:
    """Record (and log once per change) that a neural voice fell back to the Kokoro default voice."""
    global _NEURAL_FALLBACK
    if not requested:
        if _NEURAL_FALLBACK.get("active"):
            log.debug("[TTS_NEURAL] neural voice is working again")
        _NEURAL_FALLBACK = {"active": False, "requested": "", "reason": ""}
        return
    reason = _neural_unavailable_reason()
    if (not _NEURAL_FALLBACK.get("active")) or _NEURAL_FALLBACK.get("requested") != requested:
        log.warning(
            "[TTS_NEURAL] '%s' could not synthesise — %s. Falling back to the Kokoro "
            "voice '%s'. The selected voice is NOT what you are hearing.",
            requested, reason, _DEFAULT_VOICE,
        )
    _NEURAL_FALLBACK = {"active": True, "requested": str(requested), "reason": reason}


def neural_fallback_state() -> Dict[str, Any]:
    """Public read for the diagnostics panel. Empty dict-ish when all is well."""
    return dict(_NEURAL_FALLBACK)


def _eli_tts_visible_text(text) -> str:
    """
    Final-only TTS surface.

    TTS must never read private reasoning scaffolds. Use the central visible
    output contract and return its result directly.
    """
    from eli.runtime.visible_output import visible_text as _eli_visible_text
    try:
        clean = _eli_visible_text(text)
    except Exception:
        log.warning("speech skipped: the text could not be checked", exc_info=True)
        return ""
    return clean if clean else "..."


def _eli_tts_is_unspeakable(text) -> bool:
    """True for degenerate output that must never be spoken — empty, too short,
    punctuation-only, or a leading-symbol stub ('-', '-Auto', '-Auto/G 5/').
    Speaking these is meaningless (the earlier engine crashed on a lone '-',
    '# channels not specified'). Mirrors the engine fragment guard so '34G',
    'No.', 'Volume down' remain speakable."""
    s = str(text or "").strip()
    if len(s) < 2:
        return True
    if not re.search(r"[A-Za-z0-9]", s):          # only symbols/whitespace
        return True
    words = re.findall(r"[A-Za-z]{2,}", s)
    if not words and not re.search(r"\d", s):      # e.g. '-G' (no word, no digit)
        return True
    if len(s) < 12 and not s[0].isalnum():         # short leading-symbol stub
        return True
    return False


def _speak_pyttsx3(text: str, voice_name: str) -> bool:
    """Speak via OS TTS (Windows SAPI, macOS NSS, Linux espeak backend in pyttsx3). A `sys:spd:<lang>`
    voice is the desktop's speech-dispatcher, a program of its own (spd-say), in that language."""
    if str(voice_name or "").startswith(_SPD_PREFIX):
        import subprocess
        try:
            # -w: back when the speech ends, as pyttsx3's runAndWait is; no time limit, which would cut a long reply
            done = subprocess.run(["spd-say", "-w", "-l", voice_name[len(_SPD_PREFIX):], "--", str(text or "")],
                                  capture_output=True)
            return done.returncode == 0
        except Exception:
            log.debug("[TTS] spd-say failed", exc_info=True)
            return False
    try:
        import pyttsx3

        eng = pyttsx3.init()
        vid = _system_voice_id(voice_name) if _is_system_voice(voice_name) else voice_name
        if vid:
            eng.setProperty("voice", vid)
        eng.say(str(text or ""))
        eng.runAndWait()
        try:
            eng.stop()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return True
    except Exception:
        log.debug("[TTS] pyttsx3 speak failed", exc_info=True)
        return False


def _speaking_lock():
    """The file that tells the microphone ELI is speaking (audio_stt reads it), holding this process's id."""
    import os as _os
    from pathlib import Path as _Path
    from eli.core.paths import temp_path
    return _Path(_os.environ.get("ELI_TTS_SPEAKING_LOCK") or temp_path("tts_speaking.lock"))


def _run_tts(text: str, voice_name: str | None = None) -> bool:
    # Mark the avatar "speaking" for the whole synth+play so the face lip-syncs;
    # cleared in every exit path. Best-effort — never let it block TTS.
    try:
        from eli.cognition import expression_state as _es
        _es.set_speaking(True)
    except Exception:
        _es = None
        log.debug("[TTS] speaking-state set failed", exc_info=True)
    # Held for the whole reply, every sentence and every voice (it was once set per chunk by one engine alone).
    _lock = _speaking_lock()
    try:
        _lock.write_text(str(__import__("os").getpid()), encoding="utf-8")
    except OSError:
        log.debug("[TTS] speaking lock not written", exc_info=True)
    try:
        return _run_tts_impl(text, voice_name=voice_name)
    finally:
        try:
            _lock.unlink(missing_ok=True)
        except OSError:
            log.debug("[TTS] speaking lock not removed", exc_info=True)
        if _es is not None:
            try:
                _es.set_speaking(False)
            except Exception:
                log.debug("[TTS] speaking-state clear failed", exc_info=True)


def _speech_pieces(text: str) -> List[str]:
    """The reply as the pieces Kokoro renders one at a time: its sentences, each no longer than the
    chunk budget (_tts_chunks splits one that is). The first starts playing as soon as it is
    rendered, while the next renders."""
    pieces: List[str] = []
    for chunk in _tts_chunks(text):
        for sentence in re.split(r'(?<=[.!?])\s+', chunk):
            sentence = sentence.strip()
            if sentence:
                pieces.append(sentence)
    return pieces


def _render_piece(piece: str, voice: str, prosody: Dict[str, float]) -> Optional[bytes]:
    """One sentence in a Kokoro voice, as WAV bytes, with the tone's pitch and the saved level; None
    when it could not be rendered (tts_kokoro logs why)."""
    wav = _kokoro.synthesize_wav(piece, voice, speed=_speed(prosody))
    return _apply_tone_pitch(_gain_wav(wav)) if wav else None


def _wav_samples(wav: bytes):
    """(float32 mono samples, rate) of WAV bytes."""
    import numpy as _np
    import wave
    with wave.open(io.BytesIO(wav), "rb") as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = _np.frombuffer(raw, dtype="<i2").astype(_np.float32) / 32768.0
    elif width == 4:
        x = _np.frombuffer(raw, dtype="<i4").astype(_np.float32) / 2147483648.0
    else:
        x = (_np.frombuffer(raw, dtype=_np.uint8).astype(_np.float32) - 128.0) / 128.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, int(rate)


def _gain_wav(wav: Optional[bytes]) -> Optional[bytes]:
    """A voice's WAV at the saved level."""
    if not wav or abs(gain_db()) < 1e-6:
        return wav
    try:
        x, rate = _wav_samples(wav)
        return _kokoro.to_wav(_with_gain(x), rate)
    except Exception:
        log.debug("[TTS] level not applied to the neural voice", exc_info=True)
        return wav


def render_samples(text: str, voice_name: Optional[str] = None):
    """`text` in the active voice (or `voice_name`) as (float32 samples, rate), with the tone and
    the saved pace, pitch and level, for a caller that plays it itself (speech_stream, a reply
    spoken clause by clause). None when this voice cannot be rendered to samples (a system voice:
    pyttsx3 speaks it) or nothing could be said."""
    active = voice_name or get_active_voice()
    if _is_system_voice(active) or _eli_tts_is_unspeakable(text):
        return None
    if str(active).startswith(("char:", "clone:", "natural:")):
        wav = synthesize_wav(text, voice_name=active)
        if not wav:
            return None
        x, rate = _wav_samples(wav)
        return (x, rate) if x.size else None
    wav = _render_piece(str(text), kokoro_voice(active), _prosody())
    if not wav:
        return None
    x, rate = _wav_samples(wav)
    return (x, rate) if x.size else None


def _silence_wav(seconds: float, like: bytes) -> Optional[bytes]:
    """`seconds` of silence in the format of the WAV `like`."""
    import wave
    if seconds <= 0 or not like:
        return None
    with wave.open(io.BytesIO(like), "rb") as w:
        params = w.getparams()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(b"\x00" * int(params.framerate * seconds) * params.sampwidth * params.nchannels)
    return out.getvalue()


def _speak_kokoro(text: str, voice: str) -> Playback:
    """Render the reply sentence by sentence on a thread of its own and play each one as soon as it
    is ready, the next rendering while this one plays; the tone's pause is added after each sentence
    and the player's tail only after the last. A Playback: where it played, or why not."""
    import queue
    import threading

    pieces = _speech_pieces(text)
    if not pieces:
        return Playback(False, detail="nothing to say")
    prosody = _prosody()
    pause = float(prosody.get("sentence_silence") or 0.0)
    ready: "queue.Queue[Optional[bytes]]" = queue.Queue()
    stop = threading.Event()

    def _render_all() -> None:
        try:
            for piece in pieces:
                if stop.is_set():
                    break
                try:
                    ready.put(_render_piece(piece, voice, prosody))
                except Exception:
                    log.warning("[TTS] a sentence could not be rendered", exc_info=True)
                    ready.put(None)
                    break
        finally:
            ready.put(b"")                               # the end, whatever happened: playback never waits forever

    threading.Thread(target=_render_all, daemon=True, name="eli-tts-render").start()
    played = Playback(False, detail="nothing was spoken")
    spoken = 0
    try:
        while True:
            wav = ready.get()
            if wav == b"":
                break
            if not wav:
                why = _kokoro.load_error() or "the voice could not be synthesised"
                return Playback(False, detail=why)
            spoken += 1
            last = spoken == len(pieces)
            if not last and pause > 0:
                wav = _concat_wavs([wav, _silence_wav(pause, wav)])
            path = ""
            try:
                with tempfile.NamedTemporaryFile(prefix="eli_tts_", suffix=".wav", delete=False) as f:
                    f.write(wav)
                    path = f.name
                played = _play_wav_blocking(path, post_play_tail=None if last else 0.0)
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        log.debug("[TTS] temporary voice file not removed: %s", path, exc_info=True)
            _log_played(f"[TTS] voice={voice} sentence {spoken}/{len(pieces)}", len(wav), played)
            if not played:
                return played
    finally:
        stop.set()
    return played


def _run_tts_impl(text: str, voice_name: str | None = None) -> bool:
    import os as _os

    active = voice_for_reply(text, voice_name or get_active_voice())
    # Character (char:) / cloned (clone:) / natural neural (natural:) voice → render via
    # synthesize_wav (which owns the effect chain / Qwen3-TTS + fallback), then play the WAV.
    if str(active).startswith(("char:", "clone:", "natural:")):
        wav = synthesize_wav(text, voice_name=active)
        return _play_wav_bytes(wav) if wav else False
    # Never voice a degenerate fragment.
    if _eli_tts_is_unspeakable(text):
        log.debug(f"[TTS] skipped unspeakable text: {str(text)[:24]!r}")
        return False
    chunks = _tts_chunks(text)

    if not chunks:
        return False

    if _is_system_voice(active):
        ok_all = True
        for i, chunk in enumerate(chunks, 1):
            log.debug(f"[TTS_SYSTEM] {i}/{len(chunks)} voice={active[:48]} chars={len(chunk)}")
            if not _speak_pyttsx3(chunk, active):
                ok_all = False
                break
        return ok_all

    try:
        max_chunks = int(_os.environ.get("ELI_TTS_MAX_CHUNKS", "0") or "0")
    except Exception:
        max_chunks = 0
    if max_chunks > 0 and len(chunks) > max_chunks:
        try:
            from eli.runtime.evidence_ledger import record_event as _eli_record_event
            _eli_record_event(
                "tts_truncated",
                source="tts_router",
                action="SPEAK",
                subject=active,
                content=str(text or "")[:1000],
                payload={"chunks": len(chunks), "max_chunks": max_chunks},
                severity="error",
                outcome="truncated",
                reusable=True,
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # The default is unlimited. If the operator explicitly sets a cap, log it
        # but do not silently pretend the spoken response was complete.
        chunks = chunks[:max_chunks]

    voice = kokoro_voice(active)
    played = _speak_kokoro(" ".join(chunks), voice)
    if not played and _os.environ.get("ELI_TTS_FALLBACK", "").strip().lower() in {
        "1", "true", "yes", "on", "system", "pyttsx3",
    }:
        sys_voices = _list_system_voices()
        if sys_voices:
            log.debug("[TTS] Kokoro failed; falling back to system voice.")
            if all(_speak_pyttsx3(c, sys_voices[0]) for c in chunks):
                played = Playback(True, "the system's own voice")
    ok_all = bool(played)
    if not ok_all:
        log.debug("[TTS] not spoken; no further fallback.")

    try:
        from eli.runtime.evidence_ledger import record_event as _eli_record_event
        _eli_record_event(
            "tts_playback",
            source="tts_router",
            action="SPEAK",
            subject=voice,
            content=str(text or "")[:1000],
            payload={"chunks": len(chunks), "ok": ok_all,
                     "played_through": played.via if ok_all else "", "detail": played.detail},
            severity="info" if ok_all else "error",
            outcome="ok" if ok_all else "failed",
            reusable=True,
        )
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    return played if ok_all else Playback(False, detail=played.detail or "playback failed")


def speak(text: str, voice_name: str | None = None) -> bool:
    """Speak `text` on a thread of its own; True when speech was started. Text with nothing to say
    (blank, punctuation, a fragment) starts nothing and returns False: it returned True, as if
    speaking, because blank text is shown as "..." and that was handed on for _run_tts to refuse."""
    import threading as _threading

    text = _eli_tts_visible_text(text)
    clean = str(text or "").strip()
    if not clean or _eli_tts_is_unspeakable(clean):
        log.debug(f"[TTS] nothing to say in {str(text)[:24]!r}: not speaking")
        return False

    def _runner():
        _run_tts(clean, voice_name=voice_name)

    _threading.Thread(target=_runner, daemon=True).start()
    return True


def speak_if_enabled(text: str, enabled: bool = True, voice_name: str | None = None) -> bool:
    if not enabled:
        return False
    return speak(text, voice_name=voice_name)


def maybe_speak(response, tts_engine=None, enabled: bool = False, voice_name: str | None = None):
    if not enabled:
        return None
    text = response.get("full_text") or response.get("response") or response.get("content") if isinstance(response, dict) else str(response or "")
    if text:
        return speak(text, voice_name=voice_name)
    return None


def speak_text(text: str, *, voice: str | None = None, **_unused) -> dict:
    """Speak `text` and wait: {"ok", "backend", "played_through", "error"}. `voice` picks the voice
    (the active one when None); keyword arguments the earlier engine took are accepted and ignored."""
    text = _eli_tts_visible_text(text)
    ok = _run_tts(str(text or ""), voice_name=voice)
    # where it was heard, or why it was not: what a status line about the voice may say
    return {"ok": bool(ok), "backend": available_backends().get("engine") if ok else None,
            "played_through": getattr(ok, "via", "") if ok else "",
            "error": "" if ok else (getattr(ok, "detail", "") or "the reply was not played")}


# ── Synthesise to WAV bytes (no host playback) — powers browser voice ───────
# The browser plays the audio itself, so the host must NOT play it here.

def _concat_wavs(wavs: list[bytes]) -> Optional[bytes]:
    """Concatenate same-format WAV chunks into a single WAV (PCM frame splice)."""
    wavs = [w for w in wavs if w]
    if not wavs:
        return None
    if len(wavs) == 1:
        return wavs[0]
    import wave
    params = None
    frames = bytearray()
    for wb in wavs:
        with wave.open(io.BytesIO(wb), "rb") as wf:
            if params is None:
                params = wf.getparams()
            frames += wf.readframes(wf.getnframes())
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setparams(params)
        wf.writeframes(bytes(frames))
    return out.getvalue()


def _apply_tone_pitch(wav_bytes: Optional[bytes]) -> Optional[bytes]:
    """Shift the synthesized audio by the current emotion's pitch (semitones) so a
    `sad` reply sounds lower and an `ecstatic` one higher — the pitch dimension the
    voice engines do not take themselves. No-op when pitch≈0 or ffmpeg is missing (fail open)."""
    if not wav_bytes:
        return wav_bytes
    try:
        from eli.cognition import tone_adaptor
        semis = float((tone_adaptor.voice_prosody() or {}).get("pitch") or 0.0)
    except Exception:
        return wav_bytes
    if abs(semis) < 0.1 or not shutil.which("ffmpeg"):
        return wav_bytes
    import subprocess as _sp
    import tempfile as _tf
    sr = _wav_sample_rate_bytes(wav_bytes)
    factor = max(0.5, min(2.0, 2.0 ** (semis / 12.0)))
    # asetrate shifts pitch+speed; atempo restores duration → pitch-only shift.
    chain = f"asetrate={int(sr*factor)},aresample={sr},atempo={1.0/factor:.4f}"
    out = ""
    try:
        fd, out = _tf.mkstemp(suffix=".wav"); __import__("os").close(fd)
        proc = _sp.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "wav", "-i", "pipe:0", "-af", chain, out],
                       input=wav_bytes, stdout=_sp.PIPE, stderr=_sp.PIPE, timeout=30)
        if proc.returncode == 0 and __import__("os").path.getsize(out) > 0:
            return Path(out).read_bytes()
    except Exception:
        log.debug("[TTS] tone pitch shift failed", exc_info=True)
    finally:
        if out:
            try:
                __import__("os").unlink(out)
            except OSError:
                log.debug("[TTS] pitch temp cleanup failed", exc_info=True)
    return wav_bytes


def _wav_sample_rate_bytes(wav_bytes: bytes) -> int:
    import io as _io
    import wave as _wave
    try:
        with _wave.open(_io.BytesIO(wav_bytes)) as w:
            return w.getframerate() or 22050
    except Exception:
        return 22050


def _tone_instruct() -> str:
    """The delivery as Qwen3-TTS's plain-language instruction: the current tone, and the pace and
    level the owner set ("speak slower", "speak softer"); "" when it is the voice's own."""
    parts: List[str] = []
    try:
        from eli.cognition import tone_adaptor
        tone = str((tone_adaptor.current_tone() or {}).get("tone") or "")
    except Exception:
        log.debug("[TTS_WAV] tone read skipped", exc_info=True)
        tone = ""
    if tone not in {"", "neutral"}:
        parts.append(f"in a {tone.replace('_', ' ')} tone")
    try:
        from eli.core.runtime_settings import load_settings
        pace = float((load_settings() or {}).get("tts_length_scale") or _PROSODY_DEFAULTS.get("length_scale", 1.0))
    except Exception:
        log.debug("[TTS_WAV] pace read skipped", exc_info=True)
        pace = float(_PROSODY_DEFAULTS.get("length_scale", 1.0))
    base = float(_PROSODY_DEFAULTS.get("length_scale", 1.0))
    if pace > base:
        parts.append("more slowly than usual")
    elif pace < base:
        parts.append("more quickly than usual")
    db = gain_db()
    if db < 0:
        parts.append("softly")
    elif db > 0:
        parts.append("with more volume")
    return ("Speak " + ", ".join(parts) + ".") if parts else ""


def synthesize_wav(text: str, voice_name: str | None = None) -> Optional[bytes]:
    """Render `text` to WAV bytes with the active voice — for clients that play audio themselves
    (browser voice) and for the neural and style voices. None when nothing is speakable or no engine
    can render it. Never plays on the host."""
    text = _eli_tts_visible_text(text)
    if _eli_tts_is_unspeakable(text):
        return None
    active = voice_for_reply(text, voice_name or get_active_voice())
    # Natural neural voice (natural:<id>): Qwen3-TTS's built-in speaker on a GPU with room for it,
    # steered by the tone; then XTTS-v2's when that extra is installed; else the default voice.
    if str(active).startswith("natural:"):
        try:
            from eli.perception import tts_qwen3
            wav = tts_qwen3.synthesize_natural_wav(text, active, instruct=_tone_instruct())
            if wav:
                _note_neural_fallback(None)
                return _gain_wav(wav)    # the tone is in the instruction, no pitch shift after
        except Exception:
            log.debug("[TTS_WAV] Qwen3-TTS natural voice failed", exc_info=True)
        try:
            from eli.perception import tts_xtts
            wav = tts_xtts.synthesize_natural_wav(text, active)
            if wav:
                _note_neural_fallback(None)
                return _gain_wav(_apply_tone_pitch(wav))
        except Exception:
            log.debug("[TTS_WAV] natural voice resolve failed", exc_info=True)
        _note_neural_fallback(active)
        active = _DEFAULT_VOICE
    # Cloned voice (clone:<name>): Qwen3-TTS from the reference recording on a GPU with room for it,
    # then Chatterbox (MIT), then Coqui XTTS-v2. Each returns None when it cannot, and the default
    # voice speaks.
    if str(active).startswith("clone:"):
        spec = None
        try:
            from eli.perception import tts_xtts
            spec = tts_xtts.get_clone(active)
        except Exception:
            log.debug("[TTS_WAV] clone registry read failed", exc_info=True)
        try:
            from eli.perception import tts_qwen3
            if spec:
                wav = tts_qwen3.synthesize_clone_wav(text, str(spec.get("ref_wav") or ""),
                                                     ref_text=str(spec.get("ref_text") or ""))
                if wav:
                    _note_neural_fallback(None)
                    return _gain_wav(_apply_tone_pitch(wav))
        except Exception:
            log.debug("[TTS_WAV] Qwen3-TTS clone failed", exc_info=True)
        try:
            from eli.cognition import tone_adaptor as _ta
            _exag = _ta.voice_exaggeration()
        except Exception:
            _exag = None
            log.debug("[TTS_WAV] tone exaggeration read skipped", exc_info=True)
        try:
            from eli.perception import tts_chatterbox
            wav = tts_chatterbox.synthesize_wav(text, active, exaggeration=_exag)
            if wav:
                _note_neural_fallback(None)
                return _gain_wav(wav)  # Chatterbox handles emotion natively — no post-hoc pitch shift
        except Exception:
            log.debug("[TTS_WAV] chatterbox clone resolve failed", exc_info=True)
        try:
            from eli.perception import tts_xtts
            wav = tts_xtts.synthesize_wav(text, active)
            if wav:
                _note_neural_fallback(None)
                return _gain_wav(_apply_tone_pitch(wav))
        except Exception:
            log.debug("[TTS_WAV] clone voice resolve failed", exc_info=True)
        _note_neural_fallback(active)
        active = _DEFAULT_VOICE
    # Style voice (char:calm, char:robotic, …): render its base Kokoro voice, then run its ffmpeg
    # effect chain over the result.
    if str(active).startswith("char:"):
        try:
            from eli.perception import voice_fx
            spec = voice_fx.get_preset(active)
            if spec:
                base = voice_fx.resolve_base_voice(spec, list_voices(), default=_DEFAULT_VOICE)
                base_wav = synthesize_wav(text, voice_name=base)
                return voice_fx.apply_fx(base_wav, spec) if base_wav else None
        except Exception:
            log.debug("[TTS_WAV] character voice resolve failed", exc_info=True)
        active = _DEFAULT_VOICE  # unknown character → safe default
    voice = kokoro_voice(active)
    prosody = _prosody()
    pause = float(prosody.get("sentence_silence") or 0.0)
    rendered: List[bytes] = []
    for piece in _speech_pieces(text) or [text]:
        wav = _render_piece(piece, voice, prosody)
        if wav is None:
            log.debug(f"[TTS_WAV] voice={voice} not rendered: {_kokoro.load_error() or 'see the log above'}")
            return None
        if rendered and pause > 0:
            rendered.append(_silence_wav(pause, wav))
        rendered.append(wav)
    return _concat_wavs(rendered)
