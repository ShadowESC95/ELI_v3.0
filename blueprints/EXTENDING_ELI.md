# Extending ELI — How to Add Your Own Features

*A hands-on, copy-pasteable procedure for adding a new capability to ELI on the
v3 architecture. This is the payoff of the god-file decomposition: a new feature
is a small **drop-in module + one `register()` line**, not surgery on a
14k-line dispatcher. Grounded in the real code paths (`effectors/registry.py`,
`executor_enhanced.py`, `router_enhanced.py`, `capability_updater.py`).*

> Conventions used below: `$` = shell command; all paths are relative to the repo
> root (`~/Desktop/ELI_v3.0`); always use the project venv — `.venv/bin/python`.

---

## 0. The mental model — how a request becomes an action

```
user utterance ──► router.route(text) ──► {action, args, confidence}
                     (eli/execution/router_enhanced.py)
                                   │
                                   ▼
                 executor.execute(action, args)
                     (eli/execution/executor_enhanced.py)
                                   │
             _rh = registry.get(action)      ◄── effectors register here
             if _rh: return _rh(action, args)     (eli/execution/effectors/*)
             else:   legacy if-chain
                                   │
                                   ▼
                 {ok, action, content, response}  ── the StructuredResult
```

To add a feature you touch **two or three files**:
1. an **effector** module — *what the feature does* (the handler).
2. the **router** — *how an utterance reaches it* (optional if you trigger the
   action programmatically or via an existing intent).
3. `SUPPORTED_ACTIONS` + a **capability-manifest** regen — *so ELI advertises it*.

Then add a **test** and commit. That's the whole loop.

---

## 1. Worked example — add `SET_BRIGHTNESS`

Goal: "set brightness to 60%" adjusts the screen. Five edits, one test.

### Step 1 — write the effector (the behaviour)

Create `eli/execution/effectors/display_control.py`. An effector imports **only
stdlib + `_base` + the logger — never the executor** (keeps the dependency
one-directional and cycle-free). A handler is `(action, args) -> dict` and
returns the StructuredResult shape `{ok, action, content, response}`.

```python
"""Display effector — SET_BRIGHTNESS (screen brightness via brightnessctl)."""
from __future__ import annotations

from eli.utils.log import get_logger
from eli.execution.effectors._base import _run   # shared, gated subprocess helper

log = get_logger(__name__)


def _h_set_brightness(a, args):
    try:
        level = max(0, min(100, int(args.get("level") or args.get("percent") or 50)))
        # _base._run is the security-gated subprocess primitive (fails closed).
        r = _run(["brightnessctl", "set", f"{level}%"])
        if not r.get("ok"):
            msg = f"Couldn't set brightness: {r.get('error') or 'tool unavailable'}"
            return {"ok": False, "action": a, "error": r.get("error"),
                    "content": msg, "response": msg}
        msg = f"Brightness set to {level}%."
        return {"ok": True, "action": a, "level": level, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

_register("SET_BRIGHTNESS", _h_set_brightness)
```

That's the entire behaviour + its dispatch registration. No executor edit yet —
just the import wiring next.

### Step 2 — make the executor import your module

Registration only runs when the module is imported. Add one line next to the
other effector imports in `eli/execution/executor_enhanced.py` (near the block of
`import eli.execution.effectors.<name>` lines):

```python
import eli.execution.effectors.display_control  # noqa: F401
```

The executor consults the registry **before** its legacy if-chain
(`executor_enhanced.py`, ~line 2546: `_rh = _registry_get(a); if _rh: return _rh(a, args)`),
so `SET_BRIGHTNESS` is now handled by your module.

### Step 3 — enumerate it as a capability

Add the action string to `SUPPORTED_ACTIONS` (the list starting ~line 1217 in
`executor_enhanced.py`) so ELI counts it among its capabilities:

```python
SUPPORTED_ACTIONS = [
    'ADD_EVENT',
    ...
    'SET_BRIGHTNESS',   # ← add (kept sorted-ish by family is fine)
    ...
]
```

### Step 4 — route an utterance to it (reachability)

`route(text)` in `eli/execution/router_enhanced.py` is a precedence-ordered
matcher chain that returns `_mk(action, args, confidence, matched_by=…)`. Add a
pattern **at the right precedence** (specific before generic). Minimal form:

```python
import re
_m = re.search(r"\b(?:set |dim |)brightness\s*(?:to|=|at)?\s*(\d{1,3})\s*%?", low)
if _m:
    return _mk("SET_BRIGHTNESS", {"level": int(_m.group(1))}, 0.95,
               matched_by="router.display.set_brightness")
```

(`low` is the normalised lowercase utterance the router already computed.) If you
only ever trigger the action programmatically (`engine`/tests/another handler via
`execute("SET_BRIGHTNESS", {...})`), you can skip the router edit.

### Step 5 — regenerate the capability manifest (don't hand-edit it)

`capability_manifest.json` is **auto-discovered**, never edited by hand — it
records, per action, whether it is `routable`, `in_dispatch`, `in_supported_list`:

```bash
$ .venv/bin/python -c "from eli.tools.registry.capability_updater import update_capability_manifest; update_capability_manifest()"
```

### Step 6 — add a test

Create `tests/test_set_brightness.py`. Patch the effector's subprocess primitive
at *its own module* (that's the v3 rule — patch where the code now lives):

```python
import eli.execution.effectors.display_control as disp
from eli.execution.effectors.registry import get


def test_set_brightness_clamps_and_dispatches(monkeypatch):
    calls = {}
    monkeypatch.setattr(disp, "_run", lambda cmd, **k: calls.setdefault("cmd", cmd) or {"ok": True})
    r = get("SET_BRIGHTNESS")("SET_BRIGHTNESS", {"level": 150})
    assert r["ok"] and r["level"] == 100          # clamped
    assert calls["cmd"] == ["brightnessctl", "set", "100%"]
```

---

## 2. Verify — the same gate every extraction passes

Run these before committing (this is exactly the pipeline the refactor used):

```bash
# 1. compiles + no dropped imports / undefined names
$ .venv/bin/python -m py_compile eli/execution/effectors/display_control.py
$ .venv/bin/python -m pyflakes eli/execution/effectors/display_control.py

# 2. the action is registered and routes through the registry
$ .venv/bin/python -c "import eli.execution.executor_enhanced; \
    from eli.execution.effectors.registry import get; print('registered:', bool(get('SET_BRIGHTNESS')))"

# 3. it's in the capability surface
$ .venv/bin/python -c "import eli.execution.executor_enhanced as ex; print('SET_BRIGHTNESS' in ex.SUPPORTED_ACTIONS)"

# 4. the utterance routes to it
$ .venv/bin/python -c "from eli.execution.router_enhanced import route; print(route('set brightness to 60%'))"

# 5. your test + the contract/surface tests stay green
$ .venv/bin/python -m pytest -q tests/test_set_brightness.py \
    tests/claims/test_supported_actions.py tests/regression/test_supported_action_surface.py
```

Then commit — **one feature per commit**:

```bash
$ git add eli/execution/effectors/display_control.py eli/execution/executor_enhanced.py \
          tests/test_set_brightness.py capability_manifest.json
$ git commit -m "feat(effector): SET_BRIGHTNESS (display control)"
```

---

## 3. Files involved — the checklist

| # | File | Edit | Required? |
|---|---|---|---|
| 1 | `eli/execution/effectors/<family>.py` | new handler + `register()` | **yes** — the behaviour |
| 2 | `eli/execution/executor_enhanced.py` | `import …effectors.<family>` | **yes** — so registration runs |
| 3 | `eli/execution/executor_enhanced.py` | add action to `SUPPORTED_ACTIONS` | **yes** — capability enumeration |
| 4 | `eli/execution/router_enhanced.py` | `_mk("ACTION", …)` intent | only if voice/chat-triggered |
| 5 | `capability_manifest.json` | regenerate (script) | **yes** — auto, don't hand-edit |
| 6 | `tests/test_<action>.py` | handler + surface test | **yes** — the safety net |

New **family**? Just add `effectors/<family>.py` and one import line (steps 1–2);
group related actions in the same module (see the 23 existing families in
`MONOLITH_DECOMPOSITION_MAP.md`).

---

## 4. Two other extension surfaces

### 4a. Plugins — `eli/plugins/`
For self-contained integrations (a weather source, a web tool), subclass the
plugin base (`eli/plugins/base.py`) and drop the module in `eli/plugins/`; the
plugin **manager** (`eli/plugins/manager.py`) discovers it, and the router's
plugin pre-pass (`_route_plugin_bridge_prepass`, first thing in `route()`) can
bridge an utterance straight to it. Plugins are enabled/disabled at runtime via
the `PLUGIN_ENABLE/DISABLE/LIST/…` actions — no restart. Use a plugin when the
feature is an *optional add-on*; use an effector when it's a *core action*.

### 4b. Editions — `eli/editions/` + `eli/licensing/`
To ship a feature only to a sector/tier, put its effectors, persona, templates
and `policy.py` hooks in an **edition pack** (`eli/editions/<sector>/`). Editions
**overlay, never fork** the core: `editions.active_editions(entitled)` resolves
which packs compose, gated by a signed licence **tier** (`eli/licensing/`,
trial/personal/pro/enterprise). The core stays domain-blind and calls the hooks.
This is how "the same core" presents as a research analyst or a staff engineer
without branching code.

---

## 5. The rules that keep ELI extendable (not diminished)

- **Behaviour-preserving + verbatim.** When moving or wrapping existing logic,
  never thin it to "tidy" — the AST/pyflakes gate and the test sweep exist to
  catch a dropped import, a bare helper, or a lost edge case.
- **One direction only.** Effectors import `_base`/stdlib, **never** the executor.
  Shared logic is promoted into `_base.py` or a primitive module, not duplicated.
- **The registry is the extension point.** New capability = new module +
  `register()`. Never grow the legacy if-chain; it only shrinks.
- **Enumerable + tested.** Every action is in `SUPPORTED_ACTIONS`, discoverable in
  the manifest, and covered by a handler + contract test — so nothing silently
  vanishes and the capability surface is always diff-able.
- **One feature per commit**, verified green, revertible.

See also: `MONOLITH_DECOMPOSITION_MAP.md` (where every family lives),
`executor_decomposition.md` (the registry design), `REFACTOR_LOG_V3.md`,
`eli/execution/effectors/README.md`.
