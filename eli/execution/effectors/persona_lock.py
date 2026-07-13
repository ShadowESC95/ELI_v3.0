"""Persona-lock effector — lock the model/persona, refresh, status."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)

def _eli_persona_refresh(*a, **k):
    from eli.execution.executor_enhanced import _eli_persona_refresh as _f
    return _f(*a, **k)

def persona_lock_clear(*a, **k):
    from eli.execution.executor_enhanced import persona_lock_clear as _f
    return _f(*a, **k)

def persona_lock_set(*a, **k):
    from eli.execution.executor_enhanced import persona_lock_set as _f
    return _f(*a, **k)

def persona_lock_status(*a, **k):
    from eli.execution.executor_enhanced import persona_lock_status as _f
    return _f(*a, **k)


def _h_persona_lock_set(a, args):
    model = args.get("model") or None
    modelfile = args.get("modelfile_path") or args.get("modelfile") or None
    return persona_lock_set(model=model, modelfile_path=modelfile)


def _h_persona_lock_clear(a, args):
    return persona_lock_clear()


def _h_persona_lock_status(a, args):
    return persona_lock_status()


def _h_persona_refresh(a, args):
    return _eli_persona_refresh(reason=args.get("reason", "manual"))


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "PERSONA_LOCK_SET": _h_persona_lock_set,
    "PERSONA_LOCK_CLEAR": _h_persona_lock_clear,
    "PERSONA_LOCK_STATUS": _h_persona_lock_status,
    "PERSONA_REFRESH": _h_persona_refresh,
}.items():
    _register(_action, _fn)
