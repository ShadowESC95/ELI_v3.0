"""Effectors — the decomposed executor (strangler target, v3 §3.1 planner/effector).

`eli/execution/executor_enhanced.py` is a ~14.6k-line monolith: one `Executor`
class plus ~185 module-level functions covering every action family. It is the
next god-file to strangle, one family at a time, exactly as the labs_tab → eli.gui.labs
slice already demonstrated (verbatim move + re-export shim + verified tests).

Planned modules (each a thin effector: `Plan → one side-effect → StructuredResult`):

    media.py            Spotify / YouTube / MPRIS now-playing + transport
    apps_windows.py     open / close / focus / tile / workspace control
    files_notes.py      file create/read/list, notes, clipboard, conversion
    tts.py              speech synthesis + ducking
    memory_ops.py       remember / recall / profile surface
    system_intel.py     runtime / hardware / capability introspection
    self_maintenance.py improve / patch / self-test / eval
    screen.py           screenshot / OCR / describe / watch

Alongside these, `plan.py` (the typed Plan object) and `dispatch.py` (the thin
Executor facade mapping action → effector) complete the planner/effector split.
`executor_enhanced.py` then becomes a re-export shim, so all 51 importers and the
frozen ELI.spec build keep working untouched.

Empty until the first executor family is moved.
"""


def load_all() -> None:
    """Import every effector module once so each registers its handlers.

    A module that fails to import is written to the log as an error and kept in `failed()`; the
    others still load. One failure used to stop the loop with the loaded mark already set: the
    first caller got the exception, and every caller after it a registry silently missing that
    module's actions and those of every module after it in the alphabet."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True      # first: an effector that reads the registry while it imports must not start this again
    import importlib
    import logging
    import pkgutil
    for _m in pkgutil.iter_modules(__path__):
        try:
            importlib.import_module(f"{__name__}.{_m.name}")
        except Exception as exc:
            _FAILED[_m.name] = f"{type(exc).__name__}: {exc}"
            logging.getLogger(__name__).error("effector module %s did not load; its actions are missing",
                                              _m.name, exc_info=True)


def failed() -> dict:
    """The effector modules that did not import, each with why: {} when every action is there."""
    load_all()
    return dict(_FAILED)


_LOADED = False
_FAILED: dict = {}
