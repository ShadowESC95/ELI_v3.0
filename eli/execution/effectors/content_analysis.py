"""Content-analysis effector — OCR, image analysis, CSV analysis."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from eli.core.paths import get_paths
from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_ocr_image(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        path = str(args.get("path") or "").strip()
        if not path:
            return {"ok": False, "action": a, "error": "No image path provided",
                    "content": "No image path provided", "response": "No image path provided"}
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return {"ok": False, "action": a, "error": f"File not found: {p}",
                    "content": f"File not found: {p}", "response": f"File not found: {p}"}
        text = None
        try:
            import subprocess as _sp
            _tess = shutil.which("tesseract")
            if _tess:
                _r = _sp.run([_tess, str(p), "stdout", "--psm", "11", "-l", "eng"],
                             capture_output=True, text=True, timeout=30)
                if _r.returncode == 0:
                    text = _r.stdout.strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        if not text:
            try:
                import pytesseract
                from PIL import Image
                text = pytesseract.image_to_string(Image.open(str(p)), config="--psm 11").strip()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        if text is None:
            return {"ok": False, "action": a,
                    "error": "tesseract not found. Install: sudo apt install tesseract-ocr",
                    "content": "OCR engine not found.", "response": "OCR engine not found."}
        msg = f"OCR result from {p.name}:\n\n{text}" if text else f"No text detected in {p.name}."
        return {"ok": True, "action": a, "content": msg, "response": msg, "text": text, "path": str(p)}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_analyze_image(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    import os as _ai_os
    from pathlib import Path as _AIPP
    path = str(args.get("path") or args.get("file") or "").strip()
    if not path:
        msg = "Specify an image file to analyze."
        return {"ok": False, "action": a, "error": "missing path", "content": msg, "response": msg}
    expanded = _ai_os.path.expanduser(path)
    if not _ai_os.path.exists(expanded):
        msg = f"Image not found: {expanded}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    try:
        from eli.perception.analyze_image import analyze_image_file
    except Exception as _ai_err:
        msg = f"Image analysis is unavailable: {_ai_err}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    _ai_stem = _AIPP(expanded).stem
    _ai_out = str(get_paths().artifacts_dir / f"image_report_{_ai_stem}.md")
    try:
        rep = analyze_image_file(expanded, _ai_out, ocr=True)
    except Exception as _ai_err:
        msg = f"Image analysis failed: {_ai_err}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    if not rep.get("ok"):
        msg = rep.get("error") or "Image analysis failed."
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    _ai_w, _ai_h = rep.get("width"), rep.get("height")
    _ai_dims = f"{_ai_w}×{_ai_h} px" if _ai_w and _ai_h else "unknown dimensions"
    _ai_fmt = rep.get("format", "image")
    _ai_size = int(rep.get("size_bytes") or 0)
    _ai_ocr = (rep.get("ocr_text") or "").strip()
    _ai_name = _AIPP(expanded).name
    _ai_prompt = str(args.get("prompt") or args.get("instruction") or "").strip() or None
    _ai_prefer_fast = bool(args.get("prefer_fast"))

    _ai_vision_text = ""
    _ai_vision_err = ""
    try:
        from eli.perception import vision as _eli_vision
        _va_ok, _va_reason = _eli_vision.vision_available()
        if not _va_ok and _ai_prefer_fast:
            _va_ok, _va_reason = _eli_vision.fast_vision_available()
        if _va_ok:
            _vres = _eli_vision.describe_image(expanded, prompt=_ai_prompt, prefer_fast=_ai_prefer_fast)
            if _vres.get("ok"):
                _ai_vision_text = str(_vres.get("text") or "").strip()
            else:
                _ai_vision_err = str(_vres.get("error") or "")
        else:
            _ai_vision_err = _va_reason
    except Exception as _ai_vexc:
        _ai_vision_err = str(_ai_vexc)

    _ai_fused = ""
    try:
        from eli.core import config as _ai_cfg
        _fuse_on = bool(_ai_cfg.get("vision_fuse_with_text_model", True))
    except Exception:
        _fuse_on = True
    if _fuse_on and (_ai_vision_text or _ai_ocr):
        try:
            from eli.cognition import gguf_inference as _fgi
            _ocr_clip = (_ai_ocr or "")[:2500]
            _vis_clip = (_ai_vision_text or "(no visual description available)")[:1200]
            _fuse_user = (
                f"OCR text read from the screen (authoritative for names/titles):\n{_ocr_clip}\n\n"
                f"Vision model's visual description:\n{_vis_clip}\n\n"
                f"Using ONLY the evidence above, write 2-4 sentences on what application/"
                f"content is shown and what the user appears to be doing. Prefer the OCR text "
                f"for exact names. Do not invent anything not present in the evidence."
            )
            _ai_fused = (_fgi.chat_completion(
                _fuse_user,
                system=("You describe what is on the user's screen strictly from the provided "
                        "OCR + vision evidence. Never invent apps, text, or activities. Be concise."),
                max_tokens=300, temperature=0.3,
            ) or "").strip()
        except Exception as _fuse_err:
            log.debug(f"[ANALYZE_IMAGE] fusion skipped: {_fuse_err}")
            _ai_fused = ""

    _ai_header = f"**{_ai_name}** ({_ai_fmt}, {_ai_dims}, {_ai_size:,} bytes)"
    if _ai_fused:
        body = _ai_fused
    elif _ai_vision_text:
        body = f"Looking at {_ai_header}:\n\n{_ai_vision_text}"
        if _ai_ocr:
            body += f"\n\n---\nText I can read in it (OCR):\n\n{_ai_ocr}"
    elif _ai_ocr:
        body = (
            f"I ran OCR on {_ai_header}. Here is the text I extracted from it:\n\n{_ai_ocr}"
        )
        if _ai_vision_err:
            body += (
                f"\n\n(I couldn't run full visual analysis — {_ai_vision_err} — "
                f"so this is text only.)"
            )
    else:
        body = (
            f"I examined {_ai_header}. No readable text was detected via OCR"
        )
        if _ai_vision_err:
            body += (
                f", and I couldn't run visual analysis ({_ai_vision_err}). "
                f"I won't guess at what it shows."
            )
        else:
            body += ". I won't guess at what it shows."
    return {"ok": True, "action": a, "path": expanded, "ocr_text": _ai_ocr,
            "vision_text": _ai_vision_text, "fused_text": _ai_fused, "report": rep,
            "content": body, "response": body}


def _h_analyze_csv(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.analyze_csv import analyze_csv_file
        import tempfile as _csv_tf, os as _csv_os
        path = str(args.get("path") or "").strip()
        if not path:
            return {"ok": False, "action": a, "error": "Missing path", "content": "Missing path", "response": "Missing path"}
        out_md = _csv_os.path.join(_csv_tf.gettempdir(), "eli_csv_report.md")
        result = analyze_csv_file(path, out_md)
        if isinstance(result, dict):
            result.setdefault("action", a)
            if result.get("ok"):
                shape = result.get("shape", [0, 0])
                cols = result.get("columns", [])
                _content = (
                    f"CSV: {path}\n"
                    f"Shape: {shape[0]} rows × {shape[1]} columns\n"
                    f"Columns: {', '.join(cols[:20])}{'...' if len(cols) > 20 else ''}"
                )
            else:
                _content = result.get("error", "Analysis failed")
            result.setdefault("content", _content)
            result.setdefault("response", result.get("content"))
        return result
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "OCR_IMAGE": _h_ocr_image,
    "ANALYZE_IMAGE": _h_analyze_image,
    "ANALYZE_CSV": _h_analyze_csv,
}.items():
    _register(_action, _fn)
