import logging
import os
import threading
import types
from pathlib import Path
from typing import Optional


from eli.utils.log import get_logger
log = get_logger(__name__)

_MODEL = None
_MODEL_KEY = None
_MODEL_LOCK = threading.Lock()
_MODEL_LOADING = False   # True while a download/load is in progress
_MODEL_READY = threading.Event()  # set once the model is loaded successfully
_CUDA_FAILED = False  # set permanently once CUDA OOM occurs; prevents re-escalation
_LOAD_ERROR = ""     # why the last load failed ("" once one works), for whisper_status


def _env(name, default):
    return (os.environ.get(name, default) or default).strip()


class _PyAVLeftOut(types.ModuleType):
    """What stands in for a module no release carries while a package that imports it unguarded is
    imported: anything asked of it raises, saying why."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise ImportError(f"{self.__name__} is not installed: no release of ELI carries it (its wheels' "
                          f"FFmpeg holds x264 and x265, under the GPL), and ELI decodes audio itself "
                          f"(eli/perception/audio_samples.read_mono), so faster-whisper is handed samples")


def faster_whisper():
    """The faster_whisper package, imported without PyAV. PyAV's wheels carry x264 and x265 (GPL-2.0)
    inside their FFmpeg, so no release carries PyAV and no installer keeps it (packaging/gpl_sources.py,
    LEFT_OUT); faster-whisper imports it at the top of its audio module only to open a file, and ELI
    hands it samples. Where PyAV is not installed, a placeholder that raises on use stands in for it
    while faster-whisper imports, and nothing else sees it."""
    import importlib
    import importlib.util
    import sys
    if "faster_whisper" in sys.modules or importlib.util.find_spec("av") is not None:
        return importlib.import_module("faster_whisper")
    placeholder = _PyAVLeftOut("av")
    sys.modules["av"] = placeholder
    try:
        return importlib.import_module("faster_whisper")
    finally:
        if sys.modules.get("av") is placeholder:
            del sys.modules["av"]


# The speech model is chosen in ELI's settings (the Models page, the dashboard); the environment
# still wins, for a shell that sets it.
STT_MODEL_SETTING = "stt_model"


def _setting(key: str) -> str:
    """One saved setting, read from the settings file itself. The settings loader also tidies the
    process environment (it drops an ELI_PROJECT_ROOT that points outside this install), and the
    whisper folder is anchored to that root as the launcher set it; reading the file leaves it be."""
    try:
        import json
        from eli.core.runtime_settings import _settings_file
        path = _settings_file()
        if not path.is_file():
            return ""
        return str((json.loads(path.read_text(encoding="utf-8")) or {}).get(key) or "").strip()
    except (ImportError, OSError, ValueError, TypeError, AttributeError) as failure:   # no settings: the default model
        logging.getLogger(__name__).debug("settings unreadable for the speech model: %s", failure)
        return ""


def available_models(model_dir: Optional[str] = None) -> list:
    """The speech models ELI can use, as [{"name", "on_disk"}]: every name faster-whisper knows
    (its own list, not one kept here), and any model folder found under the whisper directory;
    `on_disk` says whether the weights are already on this computer (the others download on
    first use)."""
    root = Path(_resolve_model_dir(model_dir or _env("ELI_WHISPER_MODEL_DIR", "models/whisper")))
    names: list = []
    try:
        names += list(faster_whisper().utils._MODELS)
    except Exception as failure:   # noqa: BLE001 - without the library, only what is on disk
        logging.getLogger(__name__).debug("faster-whisper's model list unavailable: %s", failure)
    if root.is_dir():
        for hub in sorted(root.glob("models--Systran--faster-whisper-*")):
            name = hub.name[len("models--Systran--faster-whisper-"):].replace("--", "/")
            if name and name not in names:
                names.append(name)
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and (folder / "model.bin").is_file() and folder.name not in names:
                names.append(folder.name)
    return [{"name": n, "on_disk": _whisper_weights_mb(n, str(root)) > 0} for n in names]


def _whisper_hub_dir(model: str) -> str:
    """HF cache folder name faster-whisper uses under download_root."""
    return f"models--Systran--faster-whisper-{model.replace('/', '--')}"


def _whisper_weights_mb(model: str, model_dir: str) -> float:
    """This whisper model's weights on disk (its model.bin), 0 when not downloaded."""
    candidates = list(Path(model_dir).glob(f"{_whisper_hub_dir(model)}/snapshots/*/model.bin"))
    candidates.append(Path(model) / "model.bin")
    for f in candidates:
        try:
            return f.resolve().stat().st_size / 1_048_576
        except OSError:
            continue
    return 0.0


_DEVICE_DECIDED: dict = {}


def _whisper_card() -> Optional[dict]:
    """The card ctranslate2 runs whisper on: CUDA device 0, the first NVIDIA card in bus order
    (eli/__init__.py sets CUDA_DEVICE_ORDER=PCI_BUS_ID). None otherwise: ctranslate2 is CUDA only."""
    from eli.perception.gpu_room import nvidia_cards
    cards = nvidia_cards()
    return cards[0] if cards else None


_FOOTPRINTS = "whisper_gpu"      # the table of what whisper was measured holding (eli/perception/gpu_room)


def _measured_footprint_mb(card: dict, model: str) -> Optional[float]:
    """What whisper was measured holding on this card, weights and working buffers, after its warm-up."""
    from eli.perception.gpu_room import measured_footprint_mb
    return measured_footprint_mb(_FOOTPRINTS, card, model)


def _record_footprint(card: dict, model: str, mb: float) -> None:
    from eli.perception.gpu_room import record_footprint
    record_footprint(_FOOTPRINTS, card, model, mb)


def _gpu_room_for_whisper(model: str, model_dir: str) -> bool:
    """GPU only when its card still holds this whisper model after the language model takes every
    layer it can (eli/perception/gpu_room): its measured footprint on this card, or its weights until
    measured. Unknown either way: the CPU, so the language model keeps the GPU."""
    card = _whisper_card()
    if card is None or not card.get("free_mb"):         # ctranslate2 offloads to NVIDIA only
        return False
    need = _measured_footprint_mb(card, model) or _whisper_weights_mb(model, model_dir)
    if need <= 0:
        return False
    from eli.perception.gpu_room import room_mb
    room = room_mb(card, 0)
    return room is not None and room >= need


def whisper_cache_ready(
    model_dir: Optional[str] = None,
    model: Optional[str] = None,
) -> bool:
    """True when the whisper weights are already on disk (no HF download needed)."""
    model = model or _env("ELI_WHISPER_MODEL", _setting(STT_MODEL_SETTING) or "small.en")
    root = Path(_resolve_model_dir(model_dir or _env("ELI_WHISPER_MODEL_DIR", "models/whisper")))
    direct = root / model
    if direct.exists():
        return True
    hub = root / _whisper_hub_dir(model)
    snaps = hub / "snapshots"
    if snaps.is_dir():
        for snap in snaps.iterdir():
            if snap.is_dir() and any(snap.iterdir()):
                return True
    return False


def resident_footprint_gib() -> float:
    """On-disk size of the cached whisper model, as a RAM-reservation proxy.

    Whisper loads once at startup and stays resident for the whole session (CPU or GPU
    depending on device). A hardware-capacity check run before it loads — the startup
    dialog's tuning pass, before the engine and its support models exist — undercounts
    what's actually committed unless this is reserved explicitly rather than relying on
    load order. 0.0 if the cache isn't there yet (nothing to reserve for).
    """
    try:
        model = _env("ELI_WHISPER_MODEL", _setting(STT_MODEL_SETTING) or "small.en")
        root = Path(_resolve_model_dir(_env("ELI_WHISPER_MODEL_DIR", "models/whisper")))
        from eli.core.mem_units import dir_size_gib
        direct = root / model
        if direct.exists():
            return dir_size_gib(direct)
        hub = root / _whisper_hub_dir(model)
        if hub.is_dir():
            return dir_size_gib(hub)
    except Exception:
        pass
    return 0.0


def whisper_status() -> dict:
    """Lightweight STT runtime snapshot — does not load the model."""
    try:
        model, model_dir, device, compute_type, local_only = _model_settings()
        return {
            "model": model,
            "model_dir": model_dir,
            "device_configured": device,
            "compute_type": compute_type,
            "local_only": local_only,
            "cache_ready": whisper_cache_ready(model_dir, model),
            "loaded": _MODEL is not None,
            "loading": _MODEL_LOADING,
            "load_error": _LOAD_ERROR,
            "cuda_failed": _CUDA_FAILED,
            "gpu_total_mb": int((_whisper_card() or {}).get("total_mb") or 0),
        }
    except Exception as exc:
        return {"error": repr(exc)}


def _offline_whisper_missing_message() -> str:
    return (
        "Whisper STT model not installed locally. ELI is offline-by-default, so "
        "voice transcription cannot download from huggingface.co. "
        "On the server host run: .venv/bin/python -m eli.runtime.voice_assets "
        "(or re-run ./INSTALL_ELI.sh with network enabled)."
    )


def _resolve_model_dir(raw: str) -> str:
    """Absolute whisper cache dir — relative paths break when cwd != install root."""
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    try:
        from eli.core.paths import project_root
        return str((Path(project_root()) / p).resolve())
    except Exception:
        return str(Path(raw).expanduser().resolve())


def _model_settings():
    # small.en, not base.en: base.en mis-transcribes real speech ("hair with stings") and accuracy
    # follows model size, so small.en is the floor. To still recover VRAM for the 7B's GPU layers,
    # keep STT on GPU in int8_float16 rather than a smaller model. ELI_WHISPER_MODEL overrides
    # (base.en/tiny.en faster, medium.en more accurate).
    model = _env("ELI_WHISPER_MODEL", _setting(STT_MODEL_SETTING) or "small.en")
    model_dir = _resolve_model_dir(_env("ELI_WHISPER_MODEL_DIR", "models/whisper"))
    # Prefer GPU for speed, get_model() falls back to CPU if CUDA fails. ELI_WHISPER_DEVICE=cpu
    # forces CPU. int8_float16 on GPU: about half the VRAM of float16 at equal WER.
    # VRAM-aware default: on the GPU only when the card holds the language model and this whisper
    # model both (measured). An explicit ELI_WHISPER_DEVICE wins.
    _explicit_device = (os.environ.get("ELI_WHISPER_DEVICE", "") or "").strip().lower()
    if _explicit_device:
        device = _explicit_device
    else:
        # decided once per model: whisper's own memory, once loaded, would otherwise turn the answer
        # round on the next call and reload it on the other device
        if model not in _DEVICE_DECIDED:
            _DEVICE_DECIDED[model] = "cuda" if _gpu_room_for_whisper(model, model_dir) else "cpu"
        device = _DEVICE_DECIDED[model]
    compute_type = _env(
        "ELI_WHISPER_COMPUTE_TYPE", "int8_float16" if device == "cuda" else "int8")
    local_only = _env("ELI_WHISPER_LOCAL_ONLY", "0").lower() in {"1", "true", "yes", "on"}

    # Offline by default: when Net is off, force local-only so faster-whisper loads the cached model
    # instead of validating against huggingface.co, which the network failsafe blocks (OfflineError
    # on every transcription). The model lives in download_root; no network is needed.
    if not local_only:
        try:
            from eli.core.netguard import should_block_network
            if should_block_network():
                local_only = True
        except Exception:
            local_only = True

    # If CUDA has previously OOM'd this session, stay on CPU permanently
    if _CUDA_FAILED:
        device = "cpu"
        compute_type = "int8"

    candidate = Path(model_dir) / model
    if candidate.exists():
        model = str(candidate)

    return model, model_dir, device, compute_type, local_only


def get_model():
    global _MODEL, _MODEL_KEY, _MODEL_LOADING, _CUDA_FAILED, _LOAD_ERROR

    model, model_dir, device, compute_type, local_only = _model_settings()
    key = (model, model_dir, device, compute_type, local_only)

    # Fast path — model already loaded and key matches
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL

    with _MODEL_LOCK:
        # Re-check after acquiring lock (another thread may have loaded it). A load runs whole under
        # this lock, so a second caller waits here and then finds the model, or loads it itself.
        if _MODEL is not None and _MODEL_KEY == key:
            return _MODEL

        _MODEL_LOADING = True
        _MODEL_READY.clear()
        # Everything that can fail is inside the try, so the flags are reset whatever happens: the
        # import and the offline-cache check used to sit before it, and after one such failure every
        # later transcription waited five minutes on a load that was never running, with whisper
        # reported as "loading" for good.
        try:
            WhisperModel = faster_whisper().WhisperModel

            if local_only and not whisper_cache_ready(model_dir, model):
                raise RuntimeError(_offline_whisper_missing_message())

            log.debug(
                f"[LOCAL_STT] loading faster-whisper model={model!r} "
                f"device={device!r} compute={compute_type!r} "
                f"download_root={model_dir!r} local_only={local_only}",
            )
            try:
                _MODEL = WhisperModel(
                    model,
                    device=device,
                    compute_type=compute_type,
                    download_root=model_dir,
                    local_files_only=local_only,
                )
                log.debug(f"[LOCAL_STT] faster-whisper ready on {device}")
            except Exception as _stt_dev_err:
                # GPU load failed/OOM'd — fall back to CPU and remember, so
                # the rest of the session stays on CPU (no repeated OOMs).
                if str(device).lower() == "cuda":
                    _CUDA_FAILED = True
                    log.debug(
                        f"[LOCAL_STT] CUDA load failed "
                        f"({type(_stt_dev_err).__name__}: {_stt_dev_err}); "
                        f"falling back to CPU")
                    device, compute_type = "cpu", "int8"
                    key = (model, model_dir, device, compute_type, local_only)
                    _MODEL = WhisperModel(
                        model, device="cpu", compute_type="int8",
                        download_root=model_dir, local_files_only=local_only,
                    )
                    log.debug("[LOCAL_STT] faster-whisper ready on cpu (fallback)")
                else:
                    raise
            _MODEL_KEY = key
            _LOAD_ERROR = ""
        except Exception as exc:
            _note_load_error(exc)
            raise
        finally:
            _MODEL_LOADING = False
            _MODEL_READY.set()  # unblock any waiters even on failure

        return _MODEL


def _note_load_error(exc: BaseException) -> None:
    """Keep why whisper could not load, for whisper_status (Settings shows it), and say it once in
    the log at warning: each later utterance tries again and fails the same way, and those stay at
    debug. A different reason is said again."""
    global _LOAD_ERROR
    why = str(exc) or type(exc).__name__
    if why != _LOAD_ERROR:
        log.warning("[LOCAL_STT] speech recognition cannot load: %s", why)
    _LOAD_ERROR = why


def preload_model() -> bool:
    """Load whisper at startup, before the language model is planned, so the memory it holds is
    already taken when the load plan measures what each card has free. On a GPU it transcribes one
    second of silence: its working buffers are allocated on the first transcription, and the plan
    keeps only what llama.cpp itself was measured to need free. True when it is on CUDA."""
    try:
        model = _env("ELI_WHISPER_MODEL", _setting(STT_MODEL_SETTING) or "small.en")
        _card = _whisper_card() if _MODEL is None else None
        _free_before_load = (_card or {}).get("free_mb")
        get_model()
        if _MODEL is None:
            return False
        _, _, device, _, _ = _model_settings()
        if device != "cuda":
            return False
        try:
            import numpy as np
            from eli.perception.audio_samples import SPEECH_RATE
            segments, _info = _MODEL.transcribe(np.zeros(int(SPEECH_RATE), dtype=np.float32), beam_size=5,
                                                vad_filter=False, without_timestamps=True)
            for _seg in segments:
                pass
        except Exception:
            log.debug("[LOCAL_STT] warm-up transcription skipped", exc_info=True)
        if _free_before_load is not None:
            card = _whisper_card()
            if card and card.get("free_mb") is not None:
                _record_footprint(card, model, max(0.0, float(_free_before_load) - float(card["free_mb"])))
        return True
    except Exception:
        return False


def _audio_for_whisper(path):
    """The recording as 16 kHz mono samples, which faster-whisper takes without opening the file
    itself. It opens files with PyAV, which no release carries (its FFmpeg holds x264, under the GPL;
    and PyAV 18 refused the argument faster-whisper 1.2.1 passes, so every transcription in the 2.5.11
    packaged app failed): ELI decodes the file (audio_samples.read_mono), and one it cannot read raises,
    saying what would read it."""
    from eli.perception.audio_samples import SPEECH_RATE, read_mono
    return read_mono(path, SPEECH_RATE)[0]


_HINTS: list = []          # [the hint words], filled once in the background
_HINTS_STARTED = False
_LAST_HEARD: dict = {"text": "", "doubtful": False}


def _hint_words() -> str:
    """Words a spoken command to ELI is likely to hold, for the decoder: the wake words and the apps on
    this machine the person has named in conversation. "open spotify" in a noisy room came out as "old
    thing spot employee". Only names they use: every name offered is one Whisper can hear in noise."""
    global _HINTS_STARTED
    if not _HINTS_STARTED:
        _HINTS_STARTED = True
        threading.Thread(target=_gather_hints, name="stt-hints", daemon=True).start()
    return " ".join(_HINTS)


def _gather_hints() -> None:
    import sqlite3
    from eli.memory.memory import get_memory
    from eli.perception.audio_stt import WAKE_WORDS
    from eli.system.portable_app_control import iter_installed_apps
    words = [str(w) for w in WAKE_WORDS]
    try:
        names = sorted({a.name for a in iter_installed_apps() if a.name})
    except OSError:
        log.warning("[LOCAL_STT] the installed apps could not be listed: no app names as hints", exc_info=True)
        names = []
    import re
    mem = get_memory()
    for name in names:
        if name.lower() in (w.lower() for w in words):
            continue
        # The whole name, said: the search matches any one of its words anywhere ("world" took in a game
        # called "... World of ...", "files" took in "profiles"), and nearly every app became a hint.
        said = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.I)
        try:
            hits = mem.search_conversations(name, limit=None, match_all=True)
        except sqlite3.Error:
            log.warning("[LOCAL_STT] could not look for %r in conversation", name, exc_info=True)
            continue
        if any(said.search(str((hit or {}).get("content") or "")) for hit in hits or []):
            words.append(name)
    _HINTS[:] = words
    log.debug("[LOCAL_STT] decoder hints: %s", ", ".join(words))


def _doubtful(model, segments) -> bool:
    """Every segment below the decoder's own bar for a failed decode, or more likely silence than speech:
    faster-whisper's log_prob_threshold and no_speech_threshold, read from its own defaults."""
    import inspect
    params = inspect.signature(model.transcribe).parameters
    low = getattr(params.get("log_prob_threshold"), "default", None)
    silent = getattr(params.get("no_speech_threshold"), "default", None)

    def unsure(seg) -> bool:
        logprob, no_speech = getattr(seg, "avg_logprob", None), getattr(seg, "no_speech_prob", None)
        return ((low is not None and logprob is not None and logprob < low)
                or (silent is not None and no_speech is not None and no_speech > silent))
    return bool(segments) and all(unsure(s) for s in segments)


def heard_clearly(text: str) -> bool:
    """False when `text` is what was last transcribed and the decoder was not sure of it."""
    return not (_LAST_HEARD["doubtful"] and " ".join(str(text or "").lower().split()) == _LAST_HEARD["text"])


def _do_transcribe(model, audio, language: str | None) -> str:
    """`audio`: 16 kHz mono float32 samples, or the path of a recording (read into samples here)."""
    hints = _hint_words()
    segments, _info = model.transcribe(
        audio if _is_samples(audio) else _audio_for_whisper(audio),
        language=language,
        hotwords=hints or None,
        # beam_size=5 (faster-whisper's default) restores sentence punctuation
        # (. , ? !) that greedy beam_size=1 suppresses, and improves accuracy.
        # condition_on_previous_text stays False to avoid Whisper repetition loops.
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    segments = list(segments)
    text = " ".join((seg.text or "").strip() for seg in segments).strip()
    # Preserve punctuation; collapse whitespace only. Case is normalised by the
    # consuming wake/command layer (audio_stt), not here.
    text = " ".join(text.lower().split())
    _LAST_HEARD.update(text=text, doubtful=_doubtful(model, segments))
    return text


def _reload_model_on_cpu() -> None:
    """Replace the global model with a CPU instance so STT survives VRAM exhaustion.
    Sets _CUDA_FAILED so get_model() never attempts CUDA again this session."""
    global _MODEL, _MODEL_KEY, _CUDA_FAILED
    _CUDA_FAILED = True  # set before loading so _model_settings() returns cpu/int8
    WhisperModel = faster_whisper().WhisperModel
    model_name, model_dir, _device, _compute, local_only = _model_settings()
    log.warning("[LOCAL_STT] CUDA OOM — reloading whisper on CPU (int8); CUDA disabled for this session")
    _MODEL = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=model_dir,
        local_files_only=local_only,
    )
    _MODEL_KEY = (model_name, model_dir, "cpu", "int8", local_only)


def _is_samples(audio) -> bool:
    return hasattr(audio, "dtype") and hasattr(audio, "shape")


def _transcribe_with_fallback(audio, language: str | None) -> str:
    """Transcribe samples or a file path, retrying once on CPU if CUDA runs out of VRAM."""
    model = get_model()
    try:
        return _do_transcribe(model, audio, language)
    except RuntimeError as exc:
        _exc_str = str(exc).lower()
        _is_vram_failure = (
            "out of memory" in _exc_str
            or "cublas_status_alloc_failed" in _exc_str
            or "cuda error" in _exc_str
            or "cufft" in _exc_str
        )
        if not _is_vram_failure:
            raise
        # CUDA VRAM failure: flush cache and retry once on CPU
        log.warning("[LOCAL_STT] CUDA failure during transcription — flushing cache and retrying on CPU")
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _reload_model_on_cpu()
        return _do_transcribe(_MODEL, audio, language)


def speech_recognition_samples(audio):
    """A speech_recognition AudioData as 16 kHz mono float32 samples, straight from memory. The
    utterance was written to a temporary WAV that was then reopened by name while still open, which
    Windows does not allow: every utterance came back empty there, and each one went to disk."""
    import numpy as np
    from eli.perception.audio_samples import SPEECH_RATE
    raw = audio.get_raw_data(convert_rate=SPEECH_RATE, convert_width=2)
    if not raw:
        return None
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def transcribe_speech_recognition_audio(audio):
    samples = speech_recognition_samples(audio)
    if samples is None or not len(samples):
        return ""
    language = _env("ELI_WHISPER_LANGUAGE", "en") or None
    return _transcribe_with_fallback(samples, language)


def transcribe_file(path: str, language: str | None = None) -> str:
    """Transcribe a local audio file (WAV, FLAC, Ogg, MP3; others through the system's ffmpeg, see
    audio_samples.read_mono). Powers the browser-voice endpoint; nothing leaves the box. Mirrors the
    CUDA→CPU VRAM fallback of the live-mic path."""
    lang = language or (_env("ELI_WHISPER_LANGUAGE", "en") or None)
    return _transcribe_with_fallback(str(path), lang)
