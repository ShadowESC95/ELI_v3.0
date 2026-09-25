# ELI v3 — architecture map

**Read this before adding anything.** Measured from the **v3** tree on
2026-08-29 — not v2 monolith numbers.

> **Updated for v3.0.85.** Cognition policy matches v2.3.44+ (gradient
> orchestrator, shared retrieval, S01–S12). Layout differs: decomposed
> `kernel/stages/`, `gui/main_window/`, `execution/effectors/`.

---

## 1. Scale (measured)

| | |
|---|---|
| `eli/` Python | **232,507 lines** · **595 modules** |
| `api/server.py` | **5,303 lines** (FastAPI web app + dashboard) |
| `capability_manifest.json` | **232** total actions |
| Doc-routable (`routable` ∨ `in_supported_list`) | **215** |
| Executor `SUPPORTED_ACTIONS` | **211** |
| Router-routable (`routable` flag) | **189** |
| User data dir (installed) | `~/.local/share/eli` (Linux) · `%LOCALAPPDATA%/eli` · `~/Library/Application Support/eli` |

v2 and v3 share cognition **policy** but not layout: v2 keeps larger monolith
files (`engine.py` ~15k, GUI god-file ~12k); v3 decomposes those into packages.

---

## 2. Packages, by weight (v3)

| package | files | lines | notes |
|---|---:|---:|---|
| `eli/runtime/` | 97 | 33,255 | grounding, evidence, devices |
| `eli/execution/` | 81 | 29,297 | router, executor, **42 effectors** |
| `eli/gui/` | 61 | 27,612 | **`main_window/`** + **`labs/`** |
| `eli/cognition/` | 39 | 19,112 | bus, orchestrator, inference |
| `eli/kernel/` | 23 | 17,305 | engine + **`stages/`** + pipeline_trace |
| `eli/perception/` | 26 | 10,152 | STT, TTS, vision, gaze |
| `eli/core/` | 33 | 9,698 | config, netguard, paths |
| `eli/memory/` | 15 | 8,266 | memory.py + **retrieval.py** |
| `eli/tools/` | 29 | 7,460 | image engine, registry |
| `eli/plugins/` | 34 | 5,935 | bundled plugins |
| `eli/planning/` | 26 | 4,734 | proactive, habits |
| `eli/learning/` | 14 | 4,311 | LoRA pipeline |
| `eli/coding/` | 15 | 2,684 | CodeAgent |
| `eli/world/` | 26 | 1,943 | world / autonomy |
| `eli/integrations/` | 12 | 1,765 | Ollama, MCP |

Largest single modules:

```
 7,448  eli/kernel/engine.py
 6,921  eli/execution/executor_enhanced.py
 5,098  eli/memory/memory.py
 4,547  eli/execution/router_enhanced.py
 4,376  eli/runtime/deterministic_grounding_gate.py
 3,626  eli/cognition/agent_bus.py
```

GUI is **not** one file — see `eli/gui/main_window/` (~12.5k LOC) and
`eli/gui/labs/` (~6k LOC).

---

## 3. How one turn flows (v3.0.79+)

Same cognition policy as v2.3.40 — see `blueprints/orchestration_and_agents.md`.

```
user text → router_enhanced → kernel/engine.py (+ kernel/stages/)
  S01 … S12 via pipeline_trace.py
  S06: retrieve_for_turn() + dispatch_specialists()
  S12: learning_coordinator.finalize_turn()
→ output governor → response
```

---

## 4. v3-only surfaces (not in v2 docs)

| surface | where |
|---|---|
| Effector registry | `eli/execution/effectors/` |
| Decomposed engine stages | `eli/kernel/stages/` |
| Decomposed GUI | `eli/gui/main_window/`, `eli/gui/labs/` |
| MCP host + client | `eli/integrations/mcp/`, `blueprints/mcp.md` |
| Editions / licensing | productization layer (see `V2_VS_V3_COMPARISON.md`) |

---

## Keeping this honest

```bash
python3 scripts/audit_v3_doc_stats.py
python3 -m eli.tools.registry.capability_updater   # manifest + capabilities_and_actions.md
```

If a number here disagrees with the tree, the tree wins — update this file.
