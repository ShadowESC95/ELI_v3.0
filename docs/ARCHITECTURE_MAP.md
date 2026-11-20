# ELI v3 — architecture map

**Read this before adding anything.** Measured from the **v3** tree at
3.0.170 — not v2 monolith numbers.

> **Updated for v3.0.170.** Cognition policy matches v2.3.44+ (gradient
> orchestrator, shared retrieval, S01–S12). Layout differs: decomposed
> `kernel/stages/`, `gui/main_window/`, `execution/effectors/`.

---

## 1. Scale (measured)

| | |
|---|---|
| `eli/` Python | **233,521 lines** · **608 modules** |
| `api/server.py` | **5,647 lines** (FastAPI web app + dashboard) |
| `capability_manifest.json` | **242** total actions |
| Doc-routable (`routable` ∨ `in_supported_list`) | **230** |
| Executor `SUPPORTED_ACTIONS` | **224** |
| Router-routable (`routable` flag) | **200** |
| User data dir (installed or packaged) | `~/.local/share/ELI_v3` (Linux) · `%LOCALAPPDATA%/ELI_v3` · `~/Library/Application Support/ELI_v3` |

v2 and v3 share cognition **policy** but not layout: v2 keeps larger monolith
files (`engine.py` ~15k, GUI god-file ~12k); v3 decomposes those into packages.

---

## 2. Packages, by weight (v3)

| package | files | lines | notes |
|---|---:|---:|---|
| `eli/runtime/` | 104 | 41,701 | grounding, evidence, devices |
| `eli/execution/` | 76 | 31,311 | router, executor, **42 effector modules** (199 registered actions) |
| `eli/gui/` | 59 | 28,226 | **`main_window/`** + **`labs/`** |
| `eli/cognition/` | 57 | 27,289 | bus, orchestrator, inference |
| `eli/kernel/` | 23 | 19,604 | engine + **`stages/`** + pipeline_trace |
| `eli/core/` | 36 | 13,261 | config, netguard, paths, load plan |
| `eli/perception/` | 27 | 12,246 | STT, TTS, vision, gaze |
| `eli/memory/` | 13 | 11,583 | memory.py + **retrieval.py**, claims, document index |
| `eli/world/` | 38 | 8,502 | world, building, games, the feed a body reads |
| `eli/plugins/` | 33 | 6,637 | bundled plugins |
| `eli/tools/` | 25 | 6,083 | image engine, registry |
| `eli/planning/` | 22 | 5,151 | proactive, habits |
| `eli/learning/` | 14 | 4,207 | LoRA pipeline |
| `eli/integrations/` | 15 | 4,155 | Ollama, MCP, media |
| `eli/coding/` | 15 | 2,872 | CodeAgent |
| `eli/licensing/` | 12 | 2,239 | licence keys, trial, tiers |

Largest single modules:

```
 8,521  eli/kernel/engine.py
 7,588  eli/execution/executor_enhanced.py
 7,196  eli/execution/router_enhanced.py
 5,524  eli/memory/memory.py
 3,729  eli/cognition/agent_bus.py
 3,431  eli/core/hardware_profile.py
```

GUI is **not** one file — see `eli/gui/main_window/` (~12.9k LOC) and
`eli/gui/labs/` (~5.8k LOC).

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
| Effector registry | `eli/execution/effectors/` (each module loads on its own; `effectors.failed()` lists any that did not) |
| Decomposed engine stages | `eli/kernel/stages/` |
| Decomposed GUI | `eli/gui/main_window/`, `eli/gui/labs/` |
| MCP host + client | `eli/integrations/mcp/`, `blueprints/mcp.md` |
| Editions / licensing / bundles | `eli/editions/`, `eli/licensing/`, `eli/bundles/` (see `docs/BUNDLES.md`) |
| World feed for a body (ELI VR) | `eli/world/spatial_feed.py`, `docs/EMBODIMENT.md` |

---

## Keeping this honest

```bash
python3 scripts/audit_v3_doc_stats.py
python3 -m eli.tools.registry.capability_updater   # manifest + capabilities_and_actions.md
```

If a number here disagrees with the tree, the tree wins — update this file.
