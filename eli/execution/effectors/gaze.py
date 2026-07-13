"""Gaze effector — webcam eye-tracking: status, enable/disable, calibrate, click."""
from __future__ import annotations

from typing import Any, Dict

from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_gaze_status(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.gaze_engine import get_gaze_status
        st = get_gaze_status()
        running = st.get("running", False)
        calibrated = st.get("calibrated", False)
        last = st.get("last_gaze") or {}
        lines = [
            f"Gaze engine: {'✅ running' if running else '⛔ stopped'}",
            f"Calibration: {'✅ present' if calibrated else '⚠ missing — run: gaze calibrate'}",
            f"Camera: {st.get('camera', 'auto')}",
        ]
        if last and last.get("face_detected"):
            lines.append(
                f"Last gaze: ({last.get('screen_x', 0):.0f}, {last.get('screen_y', 0):.0f})"
                f"  conf={last.get('confidence', 0):.2f}  tracker={last.get('tracker', '?')}"
            )
        elif last and last.get("ts"):
            lines.append("Last frame: no face detected")
        if st.get("error"):
            lines.append(f"Error: {st['error']}")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg, "status": st}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_gaze_enable(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.gaze_engine import start_gaze_engine
        camera = str(args.get("camera", "auto") or "auto").strip()
        result = start_gaze_engine(camera=camera)
        _dwell_on = bool(args.get("dwell"))
        if _dwell_on:
            try:
                from eli.core.runtime_settings import update_settings as _us_dwell
                _us_dwell(gaze_dwell_click=True)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        if result.get("already_running"):
            msg = "Gaze engine is already running."
        elif result.get("ok"):
            cal_note = " (calibration present)" if result.get("calibrated") else " — no calibration file yet, run: gaze calibrate"
            msg = f"Gaze engine started{cal_note}."
        else:
            msg = f"Gaze engine failed to start: {result.get('error', 'unknown error')}"
        if _dwell_on and result.get("ok"):
            msg += " Dwell-click is on — hold your gaze on a target for ~1s to click it."
        result.setdefault("content", msg)
        result.setdefault("response", msg)
        result["action"] = a
        return result
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_gaze_disable(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.gaze_engine import stop_gaze_engine
        result = stop_gaze_engine()
        msg = result.get("message", "Gaze engine stopped.")
        result.setdefault("content", msg)
        result.setdefault("response", msg)
        result["action"] = a
        return result
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_gaze_calibrate(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.gaze_engine import get_calibration_path, needs_calibration
        cal_path = get_calibration_path()
        if not needs_calibration():
            msg = (
                f"Calibration file exists at: {cal_path}\n"
                "To recalibrate, run the calibration script directly:\n"
                "  python experimental/eli_ar_avatar_kit/scripts/eli_gaze_calibrate_plus.py --points 25"
            )
        else:
            msg = (
                "No calibration file found. To calibrate the gaze engine:\n"
                "  1. Open a terminal in the ELI project root\n"
                f"  2. python experimental/eli_ar_avatar_kit/scripts/eli_gaze_calibrate_plus.py --points 25\n"
                f"  3. Follow the on-screen dot targets (takes ~2 minutes)\n"
                f"  Calibration will be saved to: {cal_path}"
            )
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "calibration_path": str(cal_path), "needs_calibration": needs_calibration()}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_gaze_click(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.os_controller import gaze_click as _gaze_click
        _btn = str(args.get("button") or "left").lower()
        _dbl = bool(args.get("double", False))
        _res = _gaze_click(button=_btn, double=_dbl)
        _res.setdefault("action", a)
        return _res
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "GAZE_STATUS": _h_gaze_status,
    "GAZE_ENABLE": _h_gaze_enable,
    "GAZE_DISABLE": _h_gaze_disable,
    "GAZE_CALIBRATE": _h_gaze_calibrate,
    "GAZE_CLICK": _h_gaze_click,
}.items():
    _register(_action, _fn)
