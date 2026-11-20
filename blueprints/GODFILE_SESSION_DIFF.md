# ELI v3 — The Five God-Files: Session Change Report

> **Historical record.** Line counts, test counts and file names below are as of when this was written. For current figures see `project_overview.md` and `architecture.md`.
>
> **Gone since (noted 2026-11-20, at v3.0.167).** The `gui_eli_original.py` and `labs_tab.py` shims under `eli/gui/` were removed in v3.0.167; the window is `eli/gui/main_window/`, the Labs tabs `eli/gui/labs/`. `effectors/inference_backends.py` was removed in v3.0.166 (nothing imported it; the Ollama client is `eli/integrations/ollama/client.py`).

*What changed across the five monolith "god-files" over this refactor session, in
full. "Session start" is the pre-refactor baseline (git merge-base `dc5f04b`, the
v2-lineage code, all five monoliths untouched); "now" is `HEAD` on
`v3-modularisation`. Every number is `wc -l` measured at that commit, not
estimated. Every step is behaviour-preserving — verified route-through-registry
(or import shim) + a test sweep before commit.*

---

## 1. Headline — the five god-files, start → now

| File | Baseline | Now | Δ | Where its mass went |
|---|---:|---:|---:|---|
| `gui_eli_original.py` | 11,573 | 7 | **−11,566 (−99.9%)** | `gui/main_window/` (16 files) |
| `executor_enhanced.py` | 14,592 | 6,442 | **−8,150 (−55.9%)** | `execution/effectors/` (39 files) |
| `engine.py` | 13,507 | 6,977 | **−6,530 (−48.3%)** | `kernel/stages/` (13 files) |
| `labs_tab.py` | 5,694 | 30 | **−5,664 (−99.5%)** | `gui/labs/` (17 files) |
| `router_enhanced.py` | 7,267 | 5,315 | **−1,952 (−26.9%)** | `execution/routing/` (13 files) |
| **Total** | **52,633** | **18,771** | **−33,862 (−64.3%)** | |

**Two fully decomposed** (gutted to a re-export shim: main window, labs). **The
engine decomposed** into six stage mixins on `CognitiveEngine` (`process()` +
`__init__` stay). **One 56 % strangled and continuing** (executor). **The router**
lifted its intent families to `execution/routing/`.

---

## 2. The executor strangle — commit by commit

The executor is the file actively decomposed this session: 14,592 → 6,442 LOC
(−56 %). The table below is the **first phase** (to `jobs`, 7,851 LOC) one
cohesive family per commit; a second phase continued the strangle to **6,442 LOC
/ 169 actions / 34 modules** (file-ops, timers, voice I/O, media, screen, memory,
coding, shell, and more — see `REFACTOR_LOG_V3.md` for the full current map).
Each row is the executor's LOC *after* that commit landed and the effector module
it produced.

| Step | Effector produced | Executor LOC | Cumulative Δ |
|---|---|---:|---:|
| — | *baseline (session start)* | 14,592 | — |
| 1 | `inference_backends` | 12,833 | −1,759 |
| 2 | `system_intel` | 11,979 | −2,613 |
| 3 | `self_maintenance` | 11,857 | −2,735 |
| 4 | `media` + **dispatch registry** | 11,741 | −2,851 |
| 5 | `apps_windows` + proactive/date-cal | 11,666 | −2,926 |
| 6 | `gaze` | 11,564 | −3,028 |
| 7 | `input_control` | 11,479 | −3,113 |
| 8 | `window_control` | 11,361 | −3,231 |
| 9 | `content_analysis` | 11,178 | −3,414 |
| 10 | `document_analysis` | 10,722 | −3,870 |
| 11 | `document_creation` | 10,457 | −4,135 |
| 12 | `introspection` | 10,407 | −4,185 |
| 13 | `status_reports` | 10,200 | −4,392 |
| 14 | `voice_input` | 10,143 | −4,449 |
| 15 | `coding` | 9,108 | −5,484 |
| 16 | `plugins` | 8,933 | −5,659 |
| 17 | `persona_lock` | 8,921 | −5,671 |
| 18 | `pomodoro` | 8,871 | −5,721 |
| 19 | `wake_word` | 8,776 | −5,816 |
| 20 | `self_improve` | 8,462 | −6,130 |
| 21 | `system_open` | 8,225 | −6,367 |
| 22 | `web_info` | 7,974 | −6,618 |
| 23 | `timers` | 7,894 | −6,698 |
| 24 | `jobs` | 7,851 | −6,741 |

*(Steps 4–5 bundle a couple of small families landed in the same commit; the
`media` commit is where the dispatch **registry** itself went load-bearing.)*

After this session the registry owns **169 actions across 34 effector modules**
(of the executor's **185** `SUPPORTED_ACTIONS`; **205** manifest capabilities).
The remaining façade holds CHAT, the orchestration verbs (MULTI_COMMAND,
SEQUENCE, SCHEDULE_TASK, GET_PROPOSALS), and a few entangled families (memory
ops, screen/vision, file ops, self-upgrade) — the next extraction targets.

---

## 3. Where the mass went — packages created / grown this session

| Package | Files | LOC | From which god-file |
|---|---:|---:|---|
| `eli/gui/main_window/` | 16 | 11,640 | `gui_eli_original.py` |
| `eli/execution/effectors/` | 39 | 9,751 | `executor_enhanced.py` |
| `eli/kernel/stages/` | 13 | 7,023 | `engine.py` (6 stage mixins + shared leaves) |
| `eli/gui/labs/` | 17 | 5,891 | `labs_tab.py` |
| `eli/execution/routing/` | 13 | 2,325 | `router_enhanced.py` (intent families) |

The relocated mass is **larger** than the LOC removed from the monoliths — the
decomposition adds package structure (one file per family, `__init__` wiring,
shared `_base` primitives, a registry) that did not exist inside the god-files.

**Conservation of code** (relocated, not deleted — a function-inventory diff
confirms 0 of the 310 baseline executor functions are missing):

```
  MONOLITHS LOSE                          NEW PACKAGES GAIN
  gui_eli_original.py    -11,566   -->    gui/main_window/       11,640   (16 files)
  executor_enhanced.py    -8,150   -->    execution/effectors/    9,751   (39 files)
  engine.py               -6,530   -->    kernel/stages/          7,023   (13 files)
  labs_tab.py             -5,664   -->    gui/labs/               5,891   (17 files)
  router_enhanced.py      -1,952   -->    execution/routing/      2,325   (13 files)
  ------------------------------          ------------------------------
  removed from monoliths -33,862          relocated               36,630
```

Plus two genuinely new packages (`eli/editions/` 343 · `eli/licensing/` 795 = 1,138
LOC). The whole `eli/` package is **519 files / 155,039 LOC** — it *grew* ~12k over
v2 (≈370 / 143k) because structure has a cost, and that cost buys testability +
extensibility. Full accounting: `MONOLITH_DECOMPOSITION_MAP.md` §6.

---

## 4. What each god-file is now

- **`gui_eli_original.py` (7 lines)** — a re-export shim. The real window is a
  mixin-composed `EliMainWindow` under `main_window/` (settings/model, chrome/IO,
  tabs, conversation, features, image, screen-capture, quick-actions,
  self-improve), plus `adapters.py`, `models.py`, `widgets.py`.
- **`executor_enhanced.py` (6,442 lines)** — a shrinking façade. `execute()`
  consults `effectors/registry.py` before the legacy if-chain; 34 effector
  modules own 169 actions; shared primitives live in `effectors/_base.py`.
- **`labs_tab.py` (30 lines)** — a re-export shim. Each Labs sub-tab is its own
  file under `labs/` (report, workspaces, sim-IDE, memory-conversations,
  test-review, calculator, file-chat, jupyter, notebook, orchestration, physics).
- **`router_enhanced.py` (5,315 lines)** — order-independent helpers and whole
  intent families lifted to `execution/routing/` (13 modules); the precedence-
  critical `route` chain stays put and the lifted families re-import so its
  `globals()` lookups keep resolving.
- **`engine.py` (6,977 lines)** — decomposed with the mixin pattern:
  `CognitiveEngine` composes six stage mixins under `kernel/stages/` (generation,
  prompt_assembly, reasoning_modes, retrieval, memory_commit, lifecycle) plus
  shared leaf modules (`_common`, `guards`, `persona_text`, `output_text`,
  `model_runtime`, `fast_actions`). `process()` (the orchestrator) and `__init__`
  (the constructor) stay on the class.

---

## 5. Net effect of the session

- **−33,862 LOC (−64 %)** across the five monoliths (52,633 → 18,771).
- **+36,630 LOC** re-shelved into cohesive, single-responsibility packages.
- **169 actions** are now drop-in modules on a registry instead of god-file
  surgery — adding a capability is a new module + one `register()` line; the
  **engine** is six stage mixins composed onto `CognitiveEngine` (a new stage is a
  new mixin file + one base-class entry).
- **Zero behavioural change** — every extraction verified against the registry,
  `pyflakes`/MRO, and the test suite (8,471 passed).
- **Companion docs:** `MONOLITH_DECOMPOSITION_MAP.md` (full function residency),
  `REFACTOR_LOG_V3.md` (before/after + effector→action map),
  `V2_VS_V3_COMPARISON.md`.
