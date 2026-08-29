# ELI v3 — architecture map

**Read this before adding anything.** Its purpose is to stop the most expensive
mistake in this codebase: building something that already exists, somewhere you
did not think to look.

Every figure below was measured from the tree, not remembered. Regenerate them
with the commands in [Keeping this honest](#keeping-this-honest).

> **Updated for v3.0.81 (August 2026).** Cognition parity with v2.3.40+: gradient
> orchestrator for all CHAT modes, shared `eli/memory/retrieval.py`, canonical
> S01–S12 via `pipeline_trace.py`, Stage 12 via `learning_coordinator.py`.

---

## 1. Scale

| | |
|---|---|
| `eli/` Python | **~190,378 lines** across **593 modules** |
| Actions in `capability_manifest.json` | **232** (215 routable or executor-backed) |
| Live SQLite stores | **4** (`user` · `agent` · `system_index` · `coding_memory`) |

The sibling repo **ELI v2** shares the same cognition policy but keeps a more
monolithic layout (~190k LOC in fewer files). They share no git history; port by
content diff (`tools/repo_parity.py` shows drift).

---

## 2. Packages, by weight

| package | role |
|---|---|
| `eli/runtime/` | grounding, evidence, self-model, devices, policy, scheduling |
| `eli/execution/` | router + executor — all manifest actions |
| `eli/gui/` | PySide6 desktop app (decomposed `main_window/` mixins) |
| `eli/cognition/` | agent bus, orchestrator, persona, inference, tone |
| `eli/kernel/` | `CognitiveEngine` + `kernel/stages/` mixins + `pipeline_trace.py` |
| `eli/perception/` | STT, TTS, vision, gaze, screen OCR |
| `eli/memory/` | SQLite + vector store + knowledge graph + `retrieval.py` |
| `eli/tools/` | image engine, capability registry, diagnostics |
| `eli/plugins/` | calendar, media, notes, weather, web, pomodoro… |
| `eli/learning/` | LoRA pipeline, feedback |
| `eli/planning/` | proactive daemon, habits, scheduled tasks |
| `eli/world/` | autonomy engine, goal ecology, world constitution |

Largest modules after decomposition (no single 15k-line engine file):

```
 7,448  eli/kernel/engine.py
 6,921  eli/execution/executor_enhanced.py
 5,098  eli/memory/memory.py
 4,547  eli/execution/router_enhanced.py
 4,376  eli/runtime/deterministic_grounding_gate.py
 3,626  eli/cognition/agent_bus.py
```

---

## 3. How one turn flows (v3.0.79+)

```
user text
   │
   ▼
router_enhanced ── deterministic contracts first
   │               then a grammar-constrained LLM resolver over manifest actions
   ▼
kernel/engine.py + kernel/stages/ — canonical S01–S12 (pipeline_trace.py)
   S01 PERCEIVE_INGEST
   S02 INPUT_GUARDS
   S03 ROUTER
   S04 GROUNDING_GATE
   S05 PLANNER              (mode-aware: fast → deep)
   S06 AGENT_BUS            retrieve_for_turn() + dispatch_specialists()
   S07 CONTEXT_ASSEMBLY     heuristic rerank
   S08 INFERENCE_BROKER
   S09 REASONING_SYNTHESIS  (private mode algo)
   S10 OUTPUT_GOVERNOR
   S11 RESPONSE_DELIVERY
   S12 LEARNING_STATE_UPDATE  (learning_coordinator.finalize_turn)
   ▼
output governor → response
```

**All CHAT modes** (Quick … Expert) run the orchestrator at scaled depth — Quick no
longer skips to a bare bus dispatch. The bus is composed inside S06 or used as a
fallback if orchestration fails.

**Reasoning modes:** `quick`, `chain_of_thought` (Normal), `self_consistency`
(Advanced), `tree_of_thoughts` (Research), `constitutional_ai` (Expert).

**Entry points:** `eli.gui.app:main` (desktop) and `api/server.py` (FastAPI web UI).

---

## 4. Before you build: where to look first

| if you are about to build… | it already exists in |
|---|---|
| turn retrieval (shared bus + orchestrator) | `eli/memory/retrieval.py` |
| pipeline stage logging | `eli/kernel/pipeline_trace.py` |
| Stage 12 learning hooks | `eli/cognition/learning_coordinator.py` |
| device discovery | `runtime/device_server.py` |
| grounding / evidence | `runtime/deterministic_grounding_gate.py`, `runtime/evidence_*` |
| MCP integration | `blueprints/mcp.md`, MCP server modules |

---

## 5. Conventions worth knowing

- **Blueprint markdown** lives in `blueprints/`; regenerate PDFs with
  `blueprints/build_blueprint_pdf.py` or project scripts.
- **Capability doc:** `python -m eli.tools.registry.capability_updater` refreshes
  `capability_manifest.json` and `blueprints/capabilities_and_actions.md`.
- **Version lives in two places:** `pyproject.toml` and
  `eli/kernel/self_upgrade.py`. Releases are tag-triggered CI.

---

## Keeping this honest

```bash
# eli/ scale
git ls-files 'eli/**/*.py' | xargs wc -l | tail -1

# largest modules
git ls-files 'eli/**/*.py' | xargs wc -l | sort -rn | head -20

# actions
python3 -c "import json;print(json.load(open('capability_manifest.json'))['total'])"
```

If a number here disagrees with the tree, the tree is right — update this file.
