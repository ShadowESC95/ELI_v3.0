"""Screen effector — AMBIENT_VISION, SCREEN_READ_ANALYZE, SCREEN_LOCATE."""
from __future__ import annotations

from pathlib import Path

from eli.utils.log import get_logger

log = get_logger(__name__)

def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)


def _h_ambient_vision(a, args):
    _av_enabled = args.get("enabled")
    if _av_enabled is None:
        # Infer from the phrasing if not explicit.
        _av_txt = str(args.get("text") or args.get("message") or "").lower()
        _av_enabled = not any(w in _av_txt for w in
                              ("stop", "off", "disable", "don't", "dont", "quit", "cease"))
    _av_enabled = bool(_av_enabled)
    try:
        from eli.perception.ambient_vision import set_ambient_vision
        st = set_ambient_vision(_av_enabled)
    except Exception as e:
        msg = f"Couldn't change ambient vision: {e}"
        return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg}
    if _av_enabled:
        from eli.perception import vision as _avv
        _ok, _reason = _avv.vision_available()
        if _ok:
            msg = (f"Ambient vision is ON — I'll glance at your screen about every "
                   f"{st.get('interval', 300)//60} min when I'm not mid-reply, and keep a "
                   f"rolling sense of what you're working on.")
        else:
            msg = (f"Ambient vision toggle is ON, but I can't actually see yet: {_reason} "
                   f"It'll start working once the vision model is installed.")
    else:
        msg = "Ambient vision is OFF — I'll only look at the screen when you ask."
    return {"ok": True, "action": a, "enabled": _av_enabled, "content": msg, "response": msg}


def _h_screen_read_analyze(a, args):
    try:
        import tempfile
        from eli.perception.screen_analysis import (
            analysis_depth_from_text,
            build_screen_analysis_prompt,
            prefer_fast_for_depth,
        )
        user_q = str(args.get("query") or args.get("text") or args.get("instruction") or "").strip()
        depth = str(args.get("depth") or analysis_depth_from_text(user_q) or "").strip()
        if not depth:
            try:
                from eli.core.runtime_settings import load_settings
                depth = str(load_settings().get("screen_analysis_depth", "standard") or "standard")
            except Exception:
                depth = "standard"
        # 1. Take screenshot
        from eli.perception.os_controller import take_screenshot
        ss_result = take_screenshot(region=str(args.get("area") or args.get("region") or "full"))
        ss_path = ss_result.get("path") or ss_result.get("file") or ""
        if not ss_path or not Path(ss_path).exists():
            try:
                import subprocess as _sp
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.close()
                _sp.run(["scrot", tmp.name], check=True)
                ss_path = tmp.name
            except Exception:
                return {"ok": False, "action": a,
                        "error": "Screenshot failed — no screenshot path returned.",
                        "content": "Screenshot failed.", "response": "Screenshot failed."}
        _sra_prompt = build_screen_analysis_prompt(
            user_q,
            explicit_prompt=str(args.get("prompt") or args.get("instruction") or "").strip(),
            depth=depth,
        )
        _prefer_fast = bool(args.get("prefer_fast")) if "prefer_fast" in (args or {}) else prefer_fast_for_depth(depth)
        _sra = _execute_impl(
            "ANALYZE_IMAGE",
            {
                "path": ss_path,
                "prompt": _sra_prompt,
                "prefer_fast": _prefer_fast and depth == "quick",
                "fuse_with_text": depth in ("standard", "deep"),
            },
        )
        _sra_body = str(_sra.get("content") or _sra.get("response") or "").strip()
        if not _sra_body:
            _sra_body = "I captured the screen but couldn't produce a description."
        return {"ok": bool(_sra.get("ok", True)), "action": a,
                "content": _sra_body, "response": _sra_body,
                "vision_text": _sra.get("vision_text", ""),
                "ocr_text": _sra.get("ocr_text", ""),
                "screenshot_path": ss_path, "depth": depth}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_screen_locate(a, args):
    query = str(args.get("query") or "").strip()
    click = bool(args.get("click"))
    if not query:
        return {"ok": False, "action": a, "error": "missing query",
                "content": "Specify what to find on screen.",
                "response": "Specify what to find on screen."}
    try:
        from eli.perception.screen_locator import locate_on_screen
        result = locate_on_screen(query=query, click=click)
        ok = bool(result.get("ok") or result.get("matches"))
        matches = result.get("matches") or []
        if matches:
            first = matches[0]
            if click and result.get("clicked"):
                msg = f"Clicked '{query}' at ({first.get('cx')}, {first.get('cy')})."
            elif click:
                msg = f"Found '{query}' at ({first.get('cx')}, {first.get('cy')}), but the click didn't go through."
            else:
                msg = f"Found '{query}' at ({first.get('cx')}, {first.get('cy')})."
        else:
            msg = f"Could not find '{query}' on the screen."
            ok = False
        payload = dict(result)
        payload.update({"ok": ok, "action": a, "content": msg, "response": msg})
        return payload
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e),
                "content": f"Screen locator failed: {e}",
                "response": f"Screen locator failed: {e}"}


def _h_screenshot(a, args):
    try:
        from eli.perception.os_controller import take_screenshot
        region = (args.get("region") or "full").strip()
        return take_screenshot(region=region)
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "AMBIENT_VISION": _h_ambient_vision,
    "SCREEN_READ_ANALYZE": _h_screen_read_analyze,
    "SCREEN_LOCATE": _h_screen_locate,
}.items():
    _register(_action, _fn)

_register("SCREENSHOT", _h_screenshot)
