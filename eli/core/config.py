"""
Configuration manager for ELI - thin shim over runtime_settings.

All reads/writes go through eli.core.runtime_settings which reads and writes
a single canonical settings.json. Legacy key names are mapped on the fly so
old callers keep working, but the on-disk file uses canonical names only.

Canonical key mapping (legacy -> canonical):
  gguf_n_ctx       -> n_ctx
  gguf_n_gpu_layers-> n_gpu_layers
  gguf_n_batch     -> batch_size
  num_predict      -> max_tokens
  gguf_model_path  -> model_path
  n_batch          -> batch_size
"""

from typing import Optional
import os
import json

from eli.core.runtime_settings import (
    DEFAULTS as _RS_DEFAULTS,
    load_settings as _rs_load,
    save_settings as _rs_save,
    _settings_file as _rs_file,
)
from eli.core.paths import resolve_runtime_path
from eli.utils.log import get_logger

log = get_logger(__name__)


# Exposed for back-compat with any caller that reads the path directly.
CONFIG_PATH = _rs_file()


# Legacy -> canonical key translation for get/set.
_KEY_ALIASES = {
    "gguf_n_ctx": "n_ctx",
    "gguf_n_gpu_layers": "n_gpu_layers",
    "gguf_n_batch": "batch_size",
    "n_batch": "batch_size",
    "num_predict": "max_tokens",
    "gguf_model_path": "model_path",
}


def _canonical(key: str) -> str:
    return _KEY_ALIASES.get(key, key)


def get(key: str, default=None):
    """Read a config value. Legacy key names transparently mapped to canonical."""
    try:
        s = _rs_load()
        return s.get(_canonical(key), default)
    except Exception:
        return default


def set(key: str, value):
    """Write a config value. Legacy key names transparently mapped to canonical."""
    try:
        _rs_save({_canonical(key): value})
        return True
    except Exception:
        return False


def delete(key: str):
    """Delete a config key from settings.json."""
    try:
        settings_file = _rs_file()
        if not settings_file.exists():
            return False
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        ck = _canonical(key)
        if ck in data:
            del data[ck]
            # settings may hold secrets — write born-0600, never world-readable.
            from eli.core.secure_io import secure_write_text as _secure_write
            _secure_write(settings_file, json.dumps(data, indent=2, ensure_ascii=False), mode=0o600)
            return True
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return False


# ---------- GGUF parameters (canonical + legacy getter/setter names) ----------

def get_gguf_model_path() -> Optional[str]:
    env = os.getenv("ELI_GGUF_MODEL_PATH")
    if env:
        p = resolve_runtime_path(env)
        if p.exists():
            return str(p)
    models_dir = os.getenv("ELI_MODELS_DIR")
    model_name = os.getenv("ELI_GGUF_MODEL")
    if models_dir and model_name:
        return str((resolve_runtime_path(models_dir) / model_name).resolve())
    v = get("model_path")
    if v:
        return str(resolve_runtime_path(str(v)))
    try:
        from eli.core.paths import PATHS
        return str(PATHS.model)
    except Exception:
        return None


def get_gguf_n_ctx() -> int:
    """The operator's context (env, then settings); 0 / unset is auto, sized for the configured
    model on this machine. 0 only when there is no model to size for."""
    for raw in (os.getenv("ELI_GGUF_N_CTX"), os.getenv("ELI_N_CTX"), get("n_ctx", 0)):
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            log.debug("ignoring a non-numeric n_ctx: %r", raw)
            continue
        if value > 0:
            return value
    try:
        from eli.core.hardware_profile import auto_ctx_for
        return int(auto_ctx_for(get_gguf_model_path()) or 0)
    except Exception:
        log.debug("auto context unavailable", exc_info=True)
        return 0


def get_gguf_n_batch() -> int:
    env = os.getenv("ELI_GGUF_N_BATCH") or os.getenv("ELI_BATCH_SIZE")
    if env is not None:
        try:
            return int(env)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return int(get("batch_size", _RS_DEFAULTS["batch_size"]))


# ---------- Persona (delegates to canonical persona authority) ----------

DEFAULT_PERSONA = ("You are ELI, a local reasoning and automation assistant. "
                   "Be direct, accurate, grounded, privacy-preserving, and useful.")


def get_persona() -> str:
    try:
        from eli.cognition.persona import get_persona as _get_persona
        val = (_get_persona() or "").strip()
        if val:
            return val
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return get("eli_persona", DEFAULT_PERSONA)


def get_eli_persona() -> str:
    return get_persona()


# ---------- Generation parameters ----------
# A value the settings cannot give falls back to the one defaults table (runtime_settings.DEFAULTS):
# these getters carried figures of their own (0.55, 0.9, 1.15, 2048) that differed from it.

def get_temperature() -> float:
    cur = get("temperature", None)
    if cur is not None:
        try:
            return float(cur)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    env = os.getenv("ELI_TEMP") or os.getenv("ELI_TEMPERATURE")
    if env:
        try:
            return float(env)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return float(_RS_DEFAULTS["temperature"])


def get_top_p() -> float:
    return float(get("top_p", _RS_DEFAULTS["top_p"]))


def get_top_k() -> int:
    return int(get("top_k", _RS_DEFAULTS["top_k"]))


def get_repeat_penalty() -> float:
    return float(get("repeat_penalty", _RS_DEFAULTS["repeat_penalty"]))


def get_num_predict() -> int:
    cur = get("max_tokens", None)
    if cur is not None:
        try:
            return int(cur)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    env = os.getenv("ELI_NUM_PREDICT") or os.getenv("ELI_MAX_TOKENS")
    if env:
        try:
            return int(env)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return int(_RS_DEFAULTS["max_tokens"])


# ---------- Threads ----------

def get_n_threads() -> int:
    env = os.getenv("ELI_GGUF_THREADS") or os.getenv("ELI_N_THREADS") or os.getenv("ELI_CPU_THREADS")
    if env:
        try:
            return int(env)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    cur = get("n_threads", None)
    if cur is not None:
        try:
            if int(cur) > 0:
                return int(cur)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    from eli.core.hardware_profile import recommend_cpu_threads   # auto: the one thread rule
    return int(recommend_cpu_threads())


# ---------- Network / offline policy ----------

def network_allowed() -> bool:
    """Return True only if networked features are explicitly enabled.

    Defaults OFF so ELI's 'fully offline' claim is true out of the box.
    Opt-in via settings (network_enabled=true) or ELI_OFFLINE=0/false/no env var.
    """
    env = os.getenv("ELI_OFFLINE")
    if env is not None:
        return env.strip().lower() not in ("1", "true", "yes")
    return bool(get("network_enabled", False))
