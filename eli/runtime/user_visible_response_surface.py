from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from eli.utils.log import get_logger

log = get_logger(__name__)


def format_name_source_audit(text: Any = "") -> str:
    return json.dumps(
        {
            "surface": "name_source_evidence",
            "allowed_sources": [
                "SQLite memory rows",
                "conversation rows",
                "settings fields",
                "profile artifacts",
                "imported or quarantined memory",
            ],
            "question": str(text or ""),
        },
        ensure_ascii=False,
        indent=2,
    )




def _consume_generator(gen: Any) -> str:
    # The engine's end-of-main-reply marker drives the stream consumer; it is never text. It
    # reached replies glued to a sentence ("...21 October.__MAIN_REPLY_DONE__").
    try:
        from eli.kernel.engine import MAIN_REPLY_DONE_SENTINEL as _done
    except Exception:
        _done = "__MAIN_REPLY_DONE__"
    parts: list[str] = []
    try:
        for chunk in gen:
            if chunk is None or chunk == _done:
                continue
            if isinstance(chunk, str):
                parts.append(chunk.replace(_done, ""))
            elif isinstance(chunk, dict):
                val = (
                    chunk.get("response")
                    or chunk.get("content")
                    or chunk.get("text")
                    or chunk.get("delta")
                    or chunk.get("message")
                )
                if val:
                    parts.append(str(val))
            else:
                parts.append(str(chunk))
    except Exception as e:
        parts.append(f"[stream failed: {type(e).__name__}: {e}]")
    return "".join(parts).strip()


def coerce_user_visible(result: Any, user_input: Any = "", mode: Any = "") -> str:
    # Prevent <generator object ...> from ever reaching the GUI.
    if inspect.isgenerator(result) or inspect.isasyncgen(result):
        return _consume_generator(result)

    if isinstance(result, str):
        _s = result.strip()
        if (
            (_s.startswith("{") or _s.startswith("["))
            and (
                '"surface":' in _s[:800]
                or "memory_truth_evidence" in _s[:800]
                or "runtime_truth_evidence" in _s[:800]
                or "missing_user_visible_text" in _s[:800]
            )
        ):
            # Never dump raw JSON to the user — format surface packets as readable text.
            try:
                _blob = json.loads(_s)
                _surface = _blob.get("surface", "")
                if _surface == "runtime_truth_evidence":
                    _eff = _blob.get("effective", {}) or {}
                    _cfg = _blob.get("configured", {}) or {}
                    _gpu = _blob.get("gpu", {}) or {}
                    _live = (_blob.get("gguf", {}) or {}).get("live_override", {}) or {}
                    # provider/model/gpu aren't top-level here, they live under configured / gguf.live_override /
                    # gpu. Reading _blob['provider'] returned 'unknown' with a model loaded, so this compact surface
                    # contradicted the full RUNTIME_STATUS dump seconds apart. Resolve from the right places,
                    # preferring the live model, falling back to the resolver the full dump uses so the two can't
                    # disagree.
                    _provider = _live.get("provider") or _cfg.get("provider")
                    _model = (_live.get("model_path") or _live.get("model_name")
                              or _cfg.get("model_path"))
                    _gpu_name = _gpu.get("name") or _eff.get("gpu")
                    try:
                        from eli.runtime.live_introspection import _runtime_core as _rc
                        _core = _rc()
                        if (not _provider) or _provider == "unknown":
                            _provider = _core.get("provider")
                        if (not _model) or _model == "unknown":
                            _model = _core.get("model_path") or _core.get("model_name")
                    except Exception:
                        pass
                    _mb = str(_model or "").strip()
                    _model_disp = (Path(_mb).name if _mb and _mb != "unknown" else "unknown")
                    _lines = [
                        f"Provider: {_provider or 'unknown'}",
                        f"Model: {_model_disp}",
                        (
                            f"Effective — ctx: {_eff.get('n_ctx', '?')}, "
                            f"gpu_layers: {_eff.get('n_gpu_layers', '?')}, "
                            f"threads: {_eff.get('n_threads', '?')}, "
                            f"batch: {_eff.get('n_batch', '?')}"
                        ),
                        f"GPU: {_gpu_name or 'unavailable'}",
                    ]
                    # Requested vs loaded, from the shared rule (truth_report.runtime_load_facts).
                    _facts = _blob.get("load_facts") or {}
                    if _facts.get("differences"):
                        _lines.append("Loaded differently from what was requested:")
                        _lines += [f"  - {d}" for d in _facts["differences"]]
                    elif _facts and not _facts.get("above_request"):
                        _lines.append("Requested and loaded values match.")
                    if _facts.get("above_request"):
                        _lines.append("Loaded more than the saved setting:")
                        _lines += [f"  - {d}" for d in _facts["above_request"]]
                    _tun = _facts.get("tuner_recommendation") or {}
                    if _tun:
                        _lines.append(
                            "Tuner suggestion (a stored fallback, not what loaded): "
                            + ", ".join(f"{k}={v}" for k, v in _tun.items()))
                    return "\n".join(_lines)
                if _surface == "identity_evidence":
                    return "Identity: ELI — runtime snapshot present, persona files loaded."
                if _surface in ("runtime_evidence", "identity_runtime_evidence"):
                    _resp = _blob.get("response") or _blob.get("content") or ""
                    if _resp:
                        return str(_resp).strip()
                # Generic surface: try response/content fields before giving up.
                for _k in ("response", "content", "text", "message"):
                    _v = _blob.get(_k)
                    if isinstance(_v, str) and _v.strip():
                        return _v.strip()
            except Exception:
                pass
            return "A diagnostic evidence packet was produced, but it is not a user-facing answer."
        return result

    if isinstance(result, dict):
        action = str(result.get("action") or "").upper()

        # Prefer a clean response/content field (including streaming iterators).
        for key in ("response", "content", "message", "text"):
            val = result.get(key)
            if val is not None and hasattr(val, "__next__"):
                return _consume_generator(val)
            if isinstance(val, str) and val.strip():
                return val.strip()

        # Last-resort: never surface a raw envelope to the user.
        return f"No user-visible response was produced for action {action or 'UNKNOWN'}."

    if result is None:
        return ""

    return str(result)


def _coerce_streaming_result(result: Any, user_input: Any = "", mode: Any = "") -> Any:
    """
    Stream-aware coercer.
    - Generators / async generators: pass through, but ensure each yielded chunk
      is converted to a clean string (no dict reprs, no envelope leakage).
    - Dicts / strings / None: collapse to the user-visible string via
      coerce_user_visible() and return that string directly.
    """
    if inspect.isgenerator(result) or inspect.isasyncgen(result):
        def _safe_chunks():
            try:
                for chunk in result:
                    if chunk is None:
                        continue
                    if isinstance(chunk, str):
                        if chunk:
                            yield chunk
                        continue
                    if isinstance(chunk, dict):
                        text = ""
                        for key in ("response", "content", "message", "text", "delta"):
                            val = chunk.get(key)
                            if isinstance(val, str) and val.strip():
                                text = val.strip()
                                break
                        if text:
                            yield text
                        # Silently drop any envelope chunk with no usable text.
                        continue
                    yield str(chunk)
            except Exception as e:
                yield f"[stream failed: {type(e).__name__}: {e}]"
        return _safe_chunks()
    return coerce_user_visible(result, user_input=user_input, mode=mode)


