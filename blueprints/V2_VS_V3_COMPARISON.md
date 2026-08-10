# ELI v2 vs v3 — What Changed

*Factual comparison of the two lines. v2 is the shipping public product
(2.1.x); v3 is in active private development (3.0.x) on the
`v3-modularisation` branch. Same cognitive runtime and capability set — v3 is
a structural and productization upgrade, not a feature rewrite.*

---

## At a glance

| | ELI v2 | ELI v3 |
|---|---|---|
| Version line | 2.1.x (public, shipping) | 3.0.x (private, in development) |
| Repository | `ShadowESC95/ELI_v2.0` (public) | `ShadowESC95/ELI_v3.0` (private) |
| Release installers | Windows exe/Inno, macOS dmg, Linux AppImage, tarball | pipeline ported, builds parked |
| Cognitive runtime | 12-stage pipeline, DAG orchestrator, layered memory | identical, inherited |
| Dispatch | monolithic `if action == …` chain | effector **registry** + shrinking if-chain |
| Productization | none | editions + licensing layer |

Both run the same local GGUF models, the same action/capability surface (174
`SUPPORTED_ACTIONS`, 205 manifest capabilities), the same offline-by-default
network guard. Nothing about *what ELI does* changed; v3 changes *how the code
is organised* and *how it can be sold*.

---

## 1. Modularisation — the god-files are being dismantled

v2 concentrates most logic in a few very large files. v3 strangles them into
cohesive packages, verified behaviour-for-behaviour against v2.

| Monolith | v2 LOC | v3 LOC | Δ | Where it went in v3 |
|---|---:|---:|---:|---|
| `executor_enhanced.py` | 14,592 | 6,442 | **−56%** | `execution/effectors/` (39 files) |
| `engine.py` | 13,507 | 6,977 | **−48%** | `kernel/stages/` (13 files, 6 mixins) |
| `gui_eli_original.py` | 11,573 | 7 | gutted | `gui/main_window/` (16 files) |
| `router_enhanced.py` | 7,267 | 5,315 | **−27%** | `execution/routing/` (13 files) |
| `labs_tab.py` | 5,694 | 30 | gutted | `gui/labs/` (17 files) |
| **Total** | **52,633** | **18,771** | **−64%** | |

The executor uses a **dispatch registry** (`execution/effectors/registry.py`):
each effector module registers the actions it owns, and the executor consults
the registry before its legacy if-chain. Adding a capability becomes a
drop-in module + `register()` call instead of god-file surgery. **169 actions**
(of 185 total `SUPPORTED_ACTIONS`) are registry-owned across **34 effector
modules**: window control, introspection, coding, media, status reports,
plugins, system-open, gaze, self-improve, input control, voice input,
datetime/calendar, persona-lock, wake-word, web/info, apps/windows, content
analysis, pomodoro, self-maintenance, document analysis/creation, jobs, timers.

The two GUI monoliths are **fully decomposed** in v3. The **engine** is decomposed
into six stage mixins composed onto `CognitiveEngine` (`process()` and `__init__`
stay on the class). The executor is ~56% strangled and continuing; the router
lifted its intent families to `execution/routing/`.

**Conservation of code** — nothing was deleted, only relocated (a function
inventory confirms 0 of the 310 baseline executor functions are missing, and
every `CognitiveEngine` method is present across the six stage mixins + the
class). The monoliths shed 33,862 LOC; it landed in new packages totalling 36,630
(main_window 11,640 · effectors 9,751 · stages 7,023 · labs 5,891 · routing
2,325), plus 1,138 LOC of genuinely new productization (editions + licensing).
The whole `eli/` package *grew* ~12k (v2 ≈143k → v3 155,039 LOC / 519 files) —
decomposition trades concentration for structure. Full accounting:
`MONOLITH_DECOMPOSITION_MAP.md` §6.

## 2. Editions + licensing — the productization layer (v3 only)

v3 adds `eli/editions/` (29 files) and `eli/licensing/` (9 files): the
"shared core + edition packs" model. One maintained core, plus domain packs —
**base, coding, content, enterprise, healthcare, legal, research** — each with
its own `capabilities/`, `personas/`, `templates/`, `tools/`. A signed licence
carries a **tier** (trial / personal / pro / enterprise) and entitles one or
more **editions**. The layer is manifest-driven and inert until composed, so it
never affects existing imports. v2 has no equivalent — it is a single edition.

## 3. Release pipeline

v2 has the mature, field-tested pipeline: tag-triggered CI builds Windows
(exe + Inno installer), macOS (dmg), Linux (AppImage + tarball), with bundled
voices, GPU packs (NVIDIA CUDA / AMD Vulkan / Apple Metal), first-run GPU and
model choosers, and a per-user data root that survives upgrades. v3 has the
**same pipeline ported** but builds are parked until the product is release-ready
(and its private repo's Actions billing is enabled).

## 4. What is identical

Everything in the cognitive core: the 12-stage reasoning pipeline, the DAG
orchestrator with its specialist agents, the layered memory (SQLite + FAISS +
knowledge graph), local voice and vision, the smart-home MQTT server, the
socket-level offline-by-default guard, and the action surface (185 actions,
205 manifest capabilities).
v3 inherits all v2 field fixes (packaging, GPU stack, path resolution, TLS,
download hardening) commit-for-commit.

---

## Summary

- **v2** = the finished, shipping product. Use it.
- **v3** = the same product, being re-architected for maintainability (kill the
  god-files) and commercialisation (editions + licensing). Not yet released.

The honest one-liner: *v3 is v2's brain in a cleaner, sellable body — still
under construction.*
