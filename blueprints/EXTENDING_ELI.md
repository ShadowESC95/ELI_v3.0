# Extending ELI — How to Add Your Own Features

*A hands-on, copy-pasteable procedure for adding a new capability to ELI on the
v3 architecture. This is the payoff of the god-file decomposition: a new feature
is a small **drop-in module + one `register()` line**, not surgery on the
dispatcher. Grounded in the real code paths (`effectors/registry.py`,
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
             effectors.load_all()            ◄── imports every effector module once
             h = registry.get(action)        ◄── effectors register here
             if h: return h(action, args)        (eli/execution/effectors/*)
             else: the actions the executor answers itself
                                   │
                                   ▼
                 {ok, action, content, response}  ── the StructuredResult
```

To add a feature you touch **one or two files**:
1. an **effector** module — *what the feature does* (the handler). Registering the
   action makes it a supported one: `SUPPORTED_ACTIONS` reads the registry live.
2. the **router** — *how an utterance reaches it* (optional if you trigger the
   action programmatically or via an existing intent).
3. a **capability-manifest** regen — *so ELI advertises it*.

Then add a **test** and commit. That's the whole loop.

---

## 1. Worked example — add `SET_BRIGHTNESS`

Goal: "set brightness to 60%" adjusts the screen. One new module, one router rule, one test.

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

That's the entire behaviour + its dispatch registration.

### Step 2 — nothing to wire

There is no import line to add. `eli.execution.effectors.load_all()` imports every
module in the package once, and the executor calls it before it looks an action up
in the registry, so a module dropped into `eli/execution/effectors/` is found.

Each module loads on its own. One that fails to import is written to the log as an
error and listed, with the reason, by `eli.execution.effectors.failed()`; the others
still load. A release build's self-test fails if `failed()` is not empty, so a build
that is missing an action module does not pass.

### Step 3 — it is a capability already

`SUPPORTED_ACTIONS` in `executor_enhanced.py` is a live view
(`effectors.registry.SupportedActions`): the short list of actions the executor
answers itself, then every action an effector registers. Registering
`SET_BRIGHTNESS` is what makes it supported; nothing else lists it.

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

# 2. the action is registered, and no effector module failed to load
$ .venv/bin/python -c "import eli.execution.effectors as eff; eff.load_all(); \
    from eli.execution.effectors.registry import get; print('registered:', bool(get('SET_BRIGHTNESS')), 'failed:', eff.failed())"

# 3. it's in the capability surface
$ .venv/bin/python -c "import eli.execution.executor_enhanced as ex; print('SET_BRIGHTNESS' in ex.SUPPORTED_ACTIONS)"

# 4. the utterance routes to it
$ .venv/bin/python -c "from eli.execution.router_enhanced import route; print(route('set brightness to 60%'))"

# 5. your test + the contract/surface tests stay green
$ .venv/bin/python -m pytest -q tests/test_set_brightness.py \
    tests/claims/test_supported_actions.py tests/test_effector_registry_contract.py
```

Then commit — **one feature per commit**:

```bash
$ git add eli/execution/effectors/display_control.py eli/execution/router_enhanced.py \
          tests/test_set_brightness.py capability_manifest.json
$ git commit -m "feat(effector): SET_BRIGHTNESS (display control)"
```

---

## 3. Files involved — the checklist

| # | File | Edit | Required? |
|---|---|---|---|
| 1 | `eli/execution/effectors/<family>.py` | new handler + `register()` | **yes** — the behaviour |
| 2 | `eli/execution/router_enhanced.py` | `_mk("ACTION", …)` intent | only if voice/chat-triggered |
| 3 | `capability_manifest.json` | regenerate (script) | **yes** — auto, don't hand-edit |
| 4 | `tests/test_<action>.py` | handler + surface test | **yes** — the safety net |

New **family**? Add `effectors/<family>.py` (step 1); group related actions in the
same module, as the existing modules under `eli/execution/effectors/` do.

---

## 4. Two other extension surfaces

### 4a. Plugins — `eli/plugins/`
For self-contained integrations (a weather source, a web tool), subclass the
plugin base (`eli/plugins/base/base.py`) and drop the module in `eli/plugins/`; the
plugin **manager** (`eli/plugins/manager.py`) discovers it, and the router's
plugin pre-pass (`_route_plugin_bridge_prepass`, first thing in `route()`) can
bridge an utterance straight to it. Plugins are enabled/disabled at runtime via
the `PLUGIN_ENABLE/DISABLE/LIST/…` actions — no restart. Use a plugin when the
feature is an *optional add-on*; use an effector when it's a *core action*.

### 4b. Editions and bundles — `eli/editions/`, `eli/bundles/`, `eli/licensing/`
An **edition pack** (`eli/editions/<sector>/`) is a folder with a `manifest.json`
naming its personas and templates. `eli/editions/__init__.py` only discovers packs
from their manifests; its one consumer is `eli.bundles.bundle_apps`, which reads the
`app` block of an official bundle's manifest. A bundle is a separate application on
the same core (`docs/BUNDLES.md`). Licences name **products**
(`eli/licensing/products.py`), not editions. Editions overlay the core and never
fork it: the core stays domain-blind.

---

## 5. The rules that keep ELI extendable (not diminished)

- **Behaviour-preserving + verbatim.** When moving or wrapping existing logic,
  never thin it to "tidy" — the AST/pyflakes gate and the test sweep exist to
  catch a dropped import, a bare helper, or a lost edge case.
- **One direction only.** Effectors import `_base`/stdlib, **never** the executor.
  Shared logic is promoted into `_base.py` or a primitive module, not duplicated.
- **The registry is the extension point.** New capability = new module +
  `register()`. Never add an inline branch to the executor for an action an
  effector owns (`tests/test_effector_dispatch_order.py` holds this).
- **Enumerable + tested.** Every registered action is in `SUPPORTED_ACTIONS`, discoverable in
  the manifest, and covered by a handler + contract test — so nothing silently
  vanishes and the capability surface is always diff-able.
- **One feature per commit**, verified green, revertible.

See also: `MONOLITH_DECOMPOSITION_MAP.md` (where every family lives),
`executor_decomposition.md` (the registry design), `REFACTOR_LOG_V3.md`,
`eli/execution/effectors/README.md`.
