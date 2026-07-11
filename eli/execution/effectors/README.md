# eli.execution.effectors — the executor's action layer

The former `executor_enhanced.py` god-file is being strangled into small, cohesive
**effector** modules, one per action family. Each owns its logic *and* its dispatch
handlers, so the executor's giant `if a == "…"` chain shrinks toward a thin façade.

## Layout

```
effectors/
  registry.py          action → handler map (the extensible dispatch layer)
  _base.py             cross-cutting primitives: security gate, subprocess, browser-open
                       (leaf — imports only stdlib + logger, never the executor)
  media.py             spotify / mpv / youtube / mpris transport + ducking + now-playing
  apps_windows.py      launch/open apps, browser, settings; window tiling
  files_notes.py       file open, notes, safe read/write/screenshot
  tts_speech.py        piper synth, voice selection, ducking-aware speak
  memory_ops.py        store/recall/search, conversation log, session history
  inference_backends.py Ollama HTTP transport + default-model resolution
  system_intel.py      runtime/cognition/memory/gpu status + report formatters
  self_maintenance.py  proactive-daemon control + self-improvement log
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

Actions are moving off the if-chain family by family (media is done — 8 actions). The
executor keeps a re-export shim for each moved name so existing imports and the frozen
build are unaffected; those shims retire once callers point at the effectors directly.
See `blueprints/v3_monolith_decomposition.md` for the full grouping and sequence.
