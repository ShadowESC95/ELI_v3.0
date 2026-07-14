"""AI-mode effector — SET_AI_MODE (reasoning mode selection)."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_set_ai_mode(a, args):
    try:
        # Public mode names are Quick / Normal / Advanced / Research / Expert.
        # canonical_mode() accepts those AND every legacy alias (cot/tot/…) and
        # maps to the stable internal strategy key; mode_display() gives the public
        # label so the user never sees the internal name.
        from eli.cognition.reasoning_modes import canonical_mode as _cm, mode_display as _md
        raw = str(args.get("mode") or args.get("reasoning_mode") or args.get("value") or "quick")
        canonical = _cm(raw)
        from eli.core.runtime_settings import save_settings as _ss
        _ss({"reasoning_mode": canonical})
        try:
            from eli.kernel.engine import get_engine as _ge
            _ge()._reasoning_mode = canonical
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        msg = f"Reasoning mode set to {_md(canonical)}."
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "mode": canonical, "mode_display": _md(canonical)}
    except Exception as _sme:
        return {"ok": False, "action": a, "error": str(_sme), "content": str(_sme), "response": str(_sme)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "SET_AI_MODE": _h_set_ai_mode,
}.items():
    _register(_action, _fn)
