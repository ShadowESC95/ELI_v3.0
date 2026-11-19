# eli.execution.effectors — the executor's action layer

The former `executor_enhanced.py` god-file is being strangled into small, cohesive
**effector** modules, one per action family. Each owns its logic *and* its dispatch
handlers, so the executor's giant `if a == "…"` chain shrinks toward a thin façade.

## Layout

```
effectors/
  registry.py          action → handler map (the extensible dispatch layer)
  _base.py             cross-cutting primitives: Full Control state, allowlisted subprocess, browser-open
                       (leaf — imports only stdlib + logger, never the executor)
  media.py             spotify / mpv / youtube / mpris transport + now-playing
  apps_windows.py      open a URL or named site, the mail app, the browser
  files_notes.py       safe file read
  tts_speech.py        the SPEAK action (speech is perception/tts_router's)
  memory_ops.py        store / recall / forget / profile actions
  system_intel.py      what a GPU reading means (gpu_use_facts)
  self_maintenance.py  proactive-daemon control
```

## Adding a new action (the whole point)

No executor edit. In the relevant effector module:

```python
from eli.execution.effectors.registry import register

def _h_set_brightness(action, args):
    level = int(args.get("level") or 50)
    return set_brightness(level)          # your effector function

register("SET_BRIGHTNESS", _h_set_brightness)
```

A handler is `(action: str, args: dict) -> dict` and returns the StructuredResult.
`_execute_impl` consults the registry before its legacy if-chain, so a registered action
is handled by your module and never touches the executor.

New effector family? Add `effectors/<family>.py`, import only stdlib + `_base` (never the
executor — that keeps the dependency one-directional and cycle-free), register its handlers,
and make sure the executor imports the module so registration runs at load.

## Migration status

Every registered action is served by its effector: `_execute_impl` looks the action up in the
registry right after alias normalisation and the generative-evidence pre-step, and no inline branch
remains for a registered action. `tests/test_effector_dispatch_order.py` fails if a port copies an
inline branch back, or if the lookup moves below the inline chain again (both happened once, and
left 158 effector handlers unreachable).

About 20 actions still live only in the executor's if-chain (OPEN_APP, HELP, the voice family,
SEQUENCE, …); moving one is: add a handler here, delete its branch. Helpers the handlers share are
partly still defined in the executor and reached through lazy shims; the media layer is the
exception: it lives here and the executor re-exports it, so there is one `_MEDIA_STATE`.
