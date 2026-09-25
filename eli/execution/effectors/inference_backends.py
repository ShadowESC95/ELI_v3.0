"""Inference-backends effector — Ollama HTTP transport + default-model resolution.

Lifted from executor_enhanced.py (v3 modularisation). OLLAMA_HOST is reproduced; DEFAULT_CHAT_MODEL is computed here (its resolver moved with it) and imported back.
"""
from __future__ import annotations

import os
import re
import requests
from typing import Any, Dict, Optional
from eli.utils.log import get_logger
log = get_logger(__name__)


def _resolve_default_chat_model() -> str:
    envv = os.environ.get("ELI_CHAT_MODEL") or os.environ.get("OLLAMA_MODEL")
    if envv:
        return envv.strip()
    try:
        from eli.core import runtime_settings as _rs
        s = _rs.load_settings() or {}
        v = str(s.get("ollama_model") or "").strip()
        if v:
            return v
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return ""


OLLAMA_HOST = (os.environ.get("ELI_OLLAMA_HOST") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
DEFAULT_CHAT_MODEL = _resolve_default_chat_model()


def _ollama_http_post_json(url: str, payload: Dict[str, Any], timeout_s: int = 30):
    """POST JSON to an Ollama endpoint.

    Returns (ok, status_code, parsed_json_or_None, error_repr_or_None) so
    callers can distinguish transport failures from HTTP-level failures.
    """
    try:
        resp = requests.post(url, json=payload, timeout=timeout_s)
        try:
            data = resp.json()
        except Exception:
            data = None
        return (200 <= resp.status_code < 300, resp.status_code, data, None)
    except Exception as e:
        return (False, 0, None, repr(e))


def _ollama_warm_load(model: str, ollama_host: str) -> bool:
    """
    Best-effort warm-load so /api/ps can report digest for lockdown verification.
    Uses /api/chat (non-stream) with a tiny prompt.
    Returns True if the request likely caused the model to load (or is already loadable),
    False otherwise.
    """
    try:
        model = (model or "").strip()
        if not model:
            return False
        host = (ollama_host or "http://localhost:11434").rstrip("/")
        url = host + "/api/chat"

        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": "ping"}],
            "options": {"num_predict": 1},
        }

        ok, status, data, err = _ollama_http_post_json(url, payload, timeout_s=30)
        if not ok:
            return False

        # If we got a response object at all, the model was at least resolvable.
        # Ollama typically loads the model to answer, so this is a decent warm-load signal.
        if isinstance(data, dict):
            if "message" in data or "done" in data or "created_at" in data:
                return True
        return True
    except Exception:
        return False


def _strip_ollama_artifacts(s: str) -> str:
    s = (s or "")
    s = s.replace("<|\nend\n", "").replace("<|end|>", "").replace("<|end|", "")
    s = re.sub(r"\s+$", "", s)
    return s.strip()


def _ollama_ps() -> Dict[str, Any]:
    try:
        return requests.get(f"{OLLAMA_HOST}/api/ps", timeout=10).json()
    except Exception:
        return {"models": []}


def _ollama_model_digest_from_ps(ps: Dict[str, Any], model_name: str) -> Optional[str]:
    for m in ps.get("models", []) or []:
        if (m.get("name") == model_name) or (m.get("model") == model_name):
            return m.get("digest") or None
    return None


def _ollama_chat_raw(user_text: str, temperature: float = 0.7, num_predict: int = 80) -> Dict[str, Any]:
    model = DEFAULT_CHAT_MODEL
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": user_text}],
        "options": {"temperature": float(temperature), "num_predict": int(num_predict)},
    }
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=(10, 3600))
        r.raise_for_status()
        j = r.json()
        content = _strip_ollama_artifacts(((j.get("message") or {}).get("content") or "").strip())
        return {"ok": True, "model": model, "content": content, "response": content}
    except Exception as e:
        msg = "raw chat failed"
        return {"ok": False, "model": model, "error": repr(e), "content": msg, "response": msg}

__all__ = ['_resolve_default_chat_model', '_ollama_http_post_json', '_ollama_warm_load', '_strip_ollama_artifacts', '_ollama_ps', '_ollama_model_digest_from_ps', '_ollama_chat_raw', 'OLLAMA_HOST', 'DEFAULT_CHAT_MODEL']
