<div align="center">

# ELI v3
Website: https://geteli.tech (public v2 line) · this repo is **private development**

**A strictly local, private AI assistant and cognitive runtime.**

Everything runs on your own machine — the model, your data, and all processing. Nothing is ever
sent to a server. No cloud, no accounts, no telemetry. Offline by default, enforced at the network socket.

![License](https://img.shields.io/badge/license-proprietary-red)
![Platform](https://img.shields.io/badge/platform-Linux%20·%20macOS%20·%20Windows-lightgrey)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Offline](https://img.shields.io/badge/network-offline%20by%20default-informational)
![Models](https://img.shields.io/badge/models-local%20GGUF-orange)
[![Support on Ko-fi](https://img.shields.io/badge/support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/shadowesc95)

</div>

---

ELI is an AI assistant that runs **entirely on your own machine** — and I mean *entirely*. It holds
a real conversation, drives your computer, reads your screen and your documents, writes and fixes
code, and quietly builds up a private picture of who you are the more you use it. Talk to it or type
to it — your call. The model and every last byte of your data stay on your hardware and never leave:
no cloud, no account, nobody else in the loop. It's offline by default and it *enforces* that down at
the network socket — not on the honour system. Point it at whatever local model you like and it tunes
itself to the hardware you've got, from a laptop to a multi-GPU tower.

> **v3.0.137 — voice input works in the packaged app again, and the first reply no longer reloads the model.** The release build had taken the newest PyAV, which refuses an argument faster-whisper passes, so every transcription failed; ELI now reads a recording itself and hands faster-whisper the samples, and the build keeps to the lock. A model picked at start-up that differs from the one in the settings is no longer "repaired" by loading it a second time. ELI's voice plays on sound devices that take only their own sample rate.
>
> **v3.0.136 — ELI is "it" throughout.** The last places in ELI's code and guides that called it "he" now say "it".
>
> **v3.0.135 — Normal mode's second pass and the intent resolver no longer read their whole prompt again.** On a hybrid model such as Qwen3.6-35B-A3B, llama.cpp cannot reuse the start two prompts share, so each call read everything (about 65 s for 7.5k tokens on an RTX 2060 SUPER). The model's state at the end of the system prompt is now kept and given back, and every pass of a turn shares one system prompt: Normal mode's second pass took 3.2 s instead of 63.8 s with the same answer, and the intent resolver 2.5 s instead of 26.6 s after its first call. ELI's own instructions to itself are no longer read as your words by the tone and emotion reads.
>
> **v3.0.134 — ELI has a world a body can enter, and builds in it.** A client that gives ELI a body (ELI VR) now agrees a protocol version with ELI on connecting, tells ELI which room it and the user are in and what they are looking at, and passes on what is said there. Asked to, ELI makes, moves, changes and takes away things in its rooms, with any GGUF: the answer is held to an exact grammar, and what was plainly said (a colour, "behind me", a thing that is not there) is worked out without the model. A thing is a named shape or anything made part by part. ELI has a room of its own whose sky and ground are its to choose, and when an insight forms or a goal opens it may make something of that there, tied to the record; nothing is scheduled and nothing is chosen for it. Every change goes through its world's policy and ledger. ELI's model can be chosen from inside the body, and a bundle opened from ELI's window is served by that window, so the model loaded there answers. See `docs/EMBODIMENT.md`.
>
> **v3.0.133 — a command is carried out only when a sentence gives it, and a reply cannot say it did what it did not.** A complaint that named Spotify stopped the film that was playing, and "Spotify is opening now" was written with nothing run. A message is now read sentence by sentence: its commands are carried out and the rest is answered, and a report of an act with nothing behind it is removed. "open nasa website" opens the site (its address comes from the conversation or a search, never from the words), "maximise browser" acts on the browser, a folder named by its path is listed and read before the model answers, and a short remark no longer waits on the intent model.
>
> **v3.0.132 — a command typed at a first-run question is carried out, not stored as your name.** "resume" answered "what should I call you?" and became the name; the interview now asks the router what is a command. The end-of-session note is written by the model again (closing the window used to stop it every time). The KV cache setting reaches llama.cpp, as q8_0: measured to answer the same as the default, where q4_0 answered wrongly. The Linux download prints no GLib errors on a system newer than its build machine and carries no GTK. The installers run on a Mac's bash, finish on Windows, keep to the locked versions, and are now run on Linux, macOS and Windows on every change. ELI VR is a bundle with its own edition pack.
>
> **v3.0.131 — a world feed for spatial clients such as ELI VR.** The local server gives the room ELI is in, its avatar state, awareness and world objects as one small message: `GET /v1/world/state`, `WS /v1/world/ws` (again whenever it changes) and `POST /v1/world/event` for a short fixed list of things a client may report. It only reads the world and carries nothing from the logs. The `.deb` now builds on a Python with no setuptools.
>
> **v3.0.130 — the window opens again (3.0.129's did not); every registered action runs in its effector module, and the executor is 9,697 lines, from 16,324; bundles: ELI Legal and later field apps open on ELI v3's core; the "deallocating None" crash is gone and the packaged app runs on Python 3.12; an install picks its Python and its PyTorch build from the machine; sizes come from the model and the machine; Spotify plays the song asked for on Wayland and closing no longer freezes the window; AppImage programs get the system's libraries; memory questions get dated, grounded answers, and questions about the conversation stay off the web.**
>
> **v3.0.129 — ELI follows up on what it offered or asked, keeps its own calendar and reminders, answers "what did we talk about last week" from the record, and explains itself from its own code; the installer survives a system Python upgrade and works on a clean Debian/Ubuntu; MoE experts load in place; the bundled llama.cpp is pinned to the GPU packs' version; the Linux build image is named (glibc 2.39 or newer).** Ports from v2. Not ported yet: per-request identity for concurrent API calls, the orchestrator audit ledger, capability state.
>
> **v3.0.128 — the quick-mode verbatim-answer gate is now audited end to end, and GPU/hardware detection is genuinely vendor-agnostic everywhere; port of v2.4.38–v2.4.45.** `READ_FILE`, `SHELL_EXEC`, and `MCP_CALL` (raw file content / raw command output / raw live-tool output) were never protected from the LLM's "compact synthesis" pass in non-quick modes — added those plus 65 more audited actions to the verbatim set. Consolidated ~8 independent `nvidia-smi` reimplementations (`GPU_STATUS`, `self_status`, `truth_report`, `deterministic_grounding_gate` ×2, `user_visible_response_surface`, `contracts.runtime_status`, `gguf_inference`'s adaptive loader) into one canonical `get_live_gpu_telemetry()`, so none of them say "unavailable" on AMD/Intel/Apple hardware or on an NVIDIA machine with a driver hiccup anymore. Also ported: real per-model VRAM lookup instead of a flat 4GB guess on live-probe failure, an uncapped unified-memory (Apple Silicon/AMD APU) budget, `nvidia-smi`'s NVML-mismatch error text no longer misread as a real GPU name in `install.sh`/`hardware_policy.py` (found and fixed two v3-specific instances of this same class of bug along the way: an unconditional lspci/WMI fallback that could re-assert `nvidia=True` even after the driver was confirmed broken, and a missing `\b` word-boundary in the AMD/ATI/Radeon regex that misread "VGA **compati**ble controller" as an AMD card), a SIGILL-killed GPU-pack verify probe no longer treated as a pass, sanitized subprocess env before shelling out to `nvidia-smi`/`rocm-smi`/`lspci`, a stale `llm.n_ctx()` read that could oversize a prompt past the context the model actually enforces, and retry-with-backoff for the release pipeline's flaky parallel large-file asset uploads.
>
> **v3.0.127 — scrub GNOME-dead Desktop Path= stubs; port of v2.4.37.**
>
> **v3.0.126 — desktop_launchers: no silent except:pass (smoke ceiling); port of v2.4.36.**
>
> **v3.0.125 — PowerShell installer ASCII-only (smoke).** Port of v2.4.35.
>
> **v3.0.124 — redistribution-safe desktop icons (no extract-folder paths).** Port of v2.4.34.
>
> **v3.0.123 — load-probe when ctx/batch exceed smart-fit (+ LAN localhost hint).** Port of v2.4.33.
>
> **v3.0.122 — CI smoke: DATE meta GUI guard is observable.** Port of v2.4.32.
>
> **v3.0.121 — check-in is not a maintenance dump.** Port of v2.4.31.
>
> **v3.0.120 — DATE meta-route + day awareness + no stale GPU volunteer.** Port of v2.4.30.
>
> **v3.0.119 — keep CUDA pack when verify probe crashes after device enum.** Port of v2.4.29.
>
> **v3.0.118 — memory continuity + mpv install confirm + desktop refresh.** Ports v2.4.28: gate stale plan volunteering; Phase-45 remediation confirm; AppImage menu icons refresh on version bump.
>
> **v3.0.117 — CUDA pack verify: pin cudart to 12.6+ and trust device enum.**
> CI CUDA packs no longer vendor too-old 12.4 cudart; verify keeps packs that
> already enumerated NVIDIA devices. Port of the 2.4.27 fix.
>
> **v3.0.116 — complete GPU packs per hardware (port of v2.4.26).** Iris/AMD/`--vulkan` get real `vulkan-` wheels; CUDA packs vendor cudart/cublas or fail closed; NetGuard scoped allow for pack install; AppImage `--integrate` refreshes `~/.local/bin/eli`. Guide: **`first time Installation/INSTALLATION_GUIDE.md`**. **[Releases](https://github.com/ShadowESC95/ELI_v3.0/releases/latest)**.
>
> **v3.0.115 — docs + release cut on the unified wizard.** Same hardware-aware one-click setup as
> 3.0.114 (NVIDIA/AMD/Arc/Metal → GPU; Iris Xe / ≤8 GB → CPU). Wizard hard-fails until nomic,
> Piper/Whisper, and a chat GGUF are present. Windows Setup: one desktop ELI + GUI singleton.
> Guide: **`first time Installation/INSTALLATION_GUIDE.md`**.
> **[Releases](https://github.com/ShadowESC95/ELI_v3.0/releases/latest)**.

## Contents
- [What is ELI?](#what-is-eli)
- [What it does](#what-it-does)
- [Design principles](#design-principles)
- [Features](#features)
- [Quick Start](#quick-start)
- [Choose your model](#choose-your-model)
- [Optional: a remote view over your own network](#optional-a-remote-view-over-your-own-network)
- [Privacy](#privacy)
- [Documentation](#documentation)
- [Under the hood](#under-the-hood)
- [Tested on & known limitations](#tested-on--known-limitations)
- [Security](#security)
- [Project status & contributing](#project-status--contributing)
- [Licensing & purchasing](#licensing--purchasing)
- [License](#license) · [Contact](#contact)

---

## What is ELI?

ELI is not a chatbot bolted onto someone else's cloud API — I had no interest in building another
one of those. It's a complete, self-contained cognitive runtime — a 12-stage reasoning pipeline, a
fleet of specialist agents on a DAG orchestrator, layered
memory (SQLite + a FAISS vector index + a knowledge graph), local voice and vision, its own
smart-home server, and a full desktop app *and* web dashboard — all running with no external service
of any kind. It's model-, user-, and hardware-agnostic by design.

Beyond answering questions, it acts on your machine, remembers you across sessions, reports its own
state from real runtime evidence rather than guessing, and can even read its own source, write and
repair code, and fine-tune on your conversation history so it gets better at serving *you*.
**242 capabilities** sit behind a single typed or spoken interface.

And because the model is *yours* and swappable, ELI's ceiling isn't fixed — it rises with the
open-model frontier and the hardware you give it. The quality is a dial you control, not a
subscription tier someone sells you back.

## Why ELI exists

I built ELI on one conviction: **your AI should belong to you — the person using it — not the
company serving it.** That isn't a slogan I put on a badge; it's the constraint every single
architectural decision in here has to answer to.

- **Everything is local. Nothing phones home.** Offline isn't a setting you toggle —
  `eli/core/netguard` enforces it at the socket layer, so the default is silence on the wire.
- **Your model of *you* stays on your machine.** ELI learns your patterns and preferences and writes
  them to your disk, never a data centre.
- **It improves for you, not for a vendor.** Self-training means ELI gets better at serving *you* —
  not at serving someone's next corporate model update.
- **Privacy isn't a feature checkbox. It's the founding constraint of the whole design.**

There's an underdog logic to it, too. The people who most need a capable assistant — those who can't
justify a monthly subscription, who live with bad or no internet, who simply don't want their
conversations sitting on a stranger's server — are exactly the people cloud AI underserves. A
capable, free, offline, self-improving assistant that runs on hardware you already own is
**genuinely democratising**, in a way most "AI for everyone" copy only pretends to be.

## What it does

You talk to it or type — it's not fussy about exact wording. Here's the sort of thing I use it for
day to day:

| Area | Examples |
|---|---|
| Computer control | open / close / focus applications, tile windows, set volume, type text, click |
| Media | play / pause / skip on Spotify or YouTube; "what's playing?" |
| Screen & documents | "what's on my screen?"; summarise a PDF; describe an image; analyse a CSV |
| Writing & code | draft a grounded report; write a script; fix bugs in a file; examine a codebase |
| Memory | "remember that…"; "what do you know about me?" — a private profile that updates as you talk |
| Planning & automation | alarms, timers, calendar, overnight / scheduled jobs; learns routines and offers to automate them |
| Information | news synthesis, weather, web search — network-gated, and it tells you when it goes online |
| Voice | configurable wake word, dictation, text-to-speech; adapts to your voice and tone |

Asking *"what can you do?"* lists the full surface.

<details>
<summary><b>Full capability breadth — 242 capabilities across 17 areas</b></summary>

| Area | What's in it |
|---|---|
| **Conversation & persona** | chat, persona lock, explain-its-last-answer, multi-command chaining |
| **App & window control** | open/close/focus apps, tile/minimise/maximise, workspaces, smart-home |
| **Input & screen control** | volume/mute, type text, mouse move/click, key presses |
| **Gaze (webcam eye-tracking)** | enable/calibrate, click where your eyes rest |
| **Media** | play/pause/skip/shuffle on Spotify or YouTube, "what's playing?", skip ads |
| **Files & documents** | create/read/list, notes, clipboard, summarise, **convert between formats** |
| **Vision & screen** | read your screen, describe images, OCR, analyse PDFs/CSVs, ambient watching |
| **Coding & repair** | solve tasks (plan→verify→repair), fix bugs, examine a codebase, scaffold projects |
| **Generation** | grounded documents/reports from your own evidence, scripts, test data |
| **Memory & identity** | remember facts, recall, a deep sourced profile of what it knows about you |
| **Grounded introspection** | runtime/memory/cognition status reported from *real* evidence, not guessed |
| **Self-maintenance** | analyse failures, self-patch (with rollback), run tests, **LoRA fine-tune itself** |
| **Tasks, time & planning** | alarms, timers, calendar, pomodoro, overnight/scheduled background jobs |
| **Proactive & goals** | morning briefing, learned habits it offers to automate, self-generated proposals |
| **Voice** | wake word (set your own), dictation, TTS, learns your voice & tone |
| **Plugins** | install / enable / disable tools at runtime |
| **System & web** | CPU/RAM/GPU status, time/date, weather, news synthesis, web search (net-gated) |

Every action is real and traceable to code — full per-action reference with example phrases is
generated from the live capability manifest.
</details>

## Design principles

What sets ELI apart from a typical assistant isn't cosmetic — it's baked into the architecture. I
can stand behind every one of these because each is a real mechanism in the code, not a line I liked
the sound of:

1. **Local and offline-first.** Everything runs on your hardware. A process-wide network guard
   (`eli/core/netguard`) fail-closes at the socket layer: with networking disabled, no outbound
   connection can be made, even by a component that tries. Online actions (search, news) are explicit
   and individually gated.

2. **Model-agnostic.** No model name or size is hardcoded on the inference path. ELI loads any local
   GGUF model, detects its chat template, and sizes context to the model's real `n_ctx_train`. Newer
   open models can be dropped in without code changes.

3. **Grounded introspection.** Asked about its own state, ELI reports from live runtime evidence —
   actual database row counts, the loaded model, the active pipeline — rather than generating a
   plausible answer. A no-fake-actions guard prevents it from claiming an action it did not perform.

4. **Self-maintaining.** It logs its own failures and can generate, syntax-check, apply, and
   automatically roll back patches to its own source. A LoRA/QLoRA pipeline (PyTorch/PEFT) can
   fine-tune the model on your own conversation history.

5. **Embodied.** It operates the desktop directly — applications, windows, input, screenshots,
   clipboard, image and live-screen understanding, and optional webcam gaze control — not just text.

6. **User-aware.** A continuous, semantic user model is read on every turn and feeds the persona,
   proactive, reflection, and memory subsystems, so context persists across sessions and adapts to
   how you work.

## Features

### Conversation and reasoning
Five reasoning modes — Quick, Normal, Advanced, Research, Expert. Quick uses a **single-pass
reasoning algorithm** at **light orchestrator depth**; Normal through Expert use genuinely
multi-pass strategies (self-consistency sampling, tree-of-thoughts branches, draft → critique).
**Every CHAT mode — including Quick — runs the gradient orchestrator and 15-agent bus** at scaled
depth; Quick is faster, not a bypass. The mode is auto-selected by how deep the question is, and
when supporting evidence is weak ELI deepens on its own: it re-gathers harder and escalates one
tier to raise its confidence *before* answering. For a Quick reply it can keep working in the
background and surface a better-grounded answer afterwards. Retrieval combines HyDE query expansion
(when eligible), vector + full-text + knowledge-graph search, re-rank, and synthesis — inside the
**12-stage cognition pipeline** (S01–S12 in `eli/kernel/pipeline_trace.py`), orchestrated with
parallelism, retries, caching, and fallback.

### Memory that persists
A four-store memory — a FAISS vector index, full-text search, a knowledge graph, and working memory —
maintains a living, versioned profile of you. It is dynamic: active projects stay current while
abandoned ones fade, so its picture of you reflects the present. It is read on every turn, so you
don't repeat yourself across sessions.

### Operates your computer
Open, close, focus, tile, minimise, or maximise applications and windows; switch workspaces; open
system / audio / power / network settings; open URLs and your IDE. Application launch is backed by a
live index of your machine's own executables — and if an app isn't installed, ELI offers to install
it for you (real `apt` / `snap` / `flatpak` on your confirmation). It also controls volume, types
text, and moves and clicks the mouse.

### Voice, hands-free
Always-listening with a wake word **you can train** — and that hears you over background music. It
ducks your media to listen, waits for an unfinished command to complete, ignores its own spoken
output, and builds a per-user voice profile. Includes dictation, audio transcription, and a Kokoro
text-to-speech voice (36 voices, American and British English and Mandarin, on the CPU of any machine;
on a GPU with room for it, the more human Qwen3-TTS in ten languages, which also clones a voice from your
recording; elsewhere your system's own voice for a reply in another language) that never voices garbled
fragments. A separate "train my voice" session learns
your pitch, energy, and tone so its delivery adapts to how you sound.

### Vision and screen understanding
Local vision-language models describe any image or your live screen; OCR extracts text from
pictures; "find the button that says X" locates UI elements on screen; optional ambient glances keep
a rolling awareness. All local — no cloud vision APIs.

### Gaze control (webcam)
Eye-tracking via MediaPipe with calibration and smoothing — "open / click that" moves the cursor to
where you're looking and clicks it. A genuine hands-free and accessibility capability.

### Image generation
A from-scratch procedural renderer with 10+ scene types (landscape, space, city, poster, emblem,
abstract, product, …) — composition planning, palettes, atmosphere, and post-processing, no model
required. Plus optional SSD-1B diffusion with VRAM hot-swap, and matplotlib plotting from your data.

### Documents and files
Create, read, and list files and folders; summarise any file; analyse CSVs, PDFs (single or whole
folders), and images. **Convert any document** to PDF, PDF-via-LuaLaTeX, `.docx`, `.odt`, `.rtf`,
HTML, Markdown, `.tex`, EPUB, or `.txt` (pandoc + a LibreOffice fallback). Two standout tools:
- **Report Builder** — drop in your sources (PDFs, data, code, notebooks) and ELI writes a full
  document grounded in your evidence: every claim is tied to a source or marked `[source needed]` —
  no fabricated citations or numbers.
- **File Chat** — open a file or folder and have a conversation about its actual contents.

Even a quick "generate a document about X" runs a multi-stage grounded pipeline — gather evidence →
plan an outline → draft section by section against that evidence → review and revise.

### Coding agent
Describe a task and it plans it, decomposes it into a dependency graph, writes it, runs it in a
sandbox, tests it, and repairs its own bugs — remembering fixes for next time. Plus examine-and-fix
on your existing files (tiered scan → offer → verified, auto-reverting patch), project scaffolding,
diffs, and a built-in Sim-IDE. The IDE ships with ELI's own native code editor (line numbers,
Python syntax highlighting, current-line highlight, auto-indent); on a PyQt6 install with
`PyQt6-QScintilla` present it upgrades to QScintilla automatically.

### Scheduling and automation
Defer any command to a time — "open Spotify at 8pm", "get the news at 7am", "morning report ready
for 7:15" — to durable background workers that survive restarts ("every morning" makes it recurring).
Chain several commands in one sentence ("close Steam and set an alarm for 7am"). Alarms, timers, and
pomodoro included.

### Proactive and self-aware
A background daemon notices your patterns and *offers* (never silently) to automate routines, builds
your morning briefing, and surfaces things worth your attention through a governed, approval-gated
layer. On a 30-minute beat it runs a self-awareness tick: it watches its own code for changes,
refreshes its self-model, and advances goals into proposals for your approval — nothing destructive
runs unattended.

### Maintains and improves itself
Logs its own failures and runs a self-repair cycle (generate → verify → apply → auto-revert); runs
maintenance (update, rebuild indexes, refresh capabilities); audits its own runtime from live health
probes; detects a missing dependency and heals its environment; and can **train a LoRA adapter on
your own conversations**, locally.

### Web, news, and weather (network-gated)
With the Net switch on, ELI fetches web answers, weather, and a **synthesised news digest** — a
rolling, interest-matched read rather than raw headlines. With it off, networking is sealed at the
socket and nothing leaves your machine.

### Interfaces — desktop, terminal, and an optional local screen-share
The primary interface is a desktop GUI (Chat plus Labs, Report Builder, Coding, Tasks, and an
embodied self-model view); there's also a headless CLI. **ELI itself always runs on your computer
and only your computer** — the AI never runs on, or sends anything to, another device. Optionally,
you can open a window onto it from a phone or tablet browser **on your own home network** via a
built-in local server (loopback-only and token-gated by default). The phone is just a remote screen:
no AI runs on it, and nothing touches the internet.

### Make it yours
Swap the model (any local GGUF). Tune the mind via a dedicated Cognition settings panel that exposes
every knowledge-gathering limit and the synthesis budget. Extend it with a real plugin system
(weather, web, calendar, notes, pomodoro, and your own). Teach it routines it proposes. And control
the boundary with a single network toggle.

## Quick Start

**Linux / macOS** — `install.sh` gives you a system report → a plan → installs the right CPU/GPU
build → offers to download a model sized to your hardware:

```bash
cd ELI_v3.0                    # private distribution — obtain from the licensor
bash install.sh                 # interactive: report → plan → install → pick model(s)
./scripts/eli_launch.sh         # launch the desktop app (first run shows a quick setup)
```
Flags: `--yes` (no prompts) · `--install-cuda` (auto-install CUDA toolkit) · `--cpu-only` ·
`--model=qwen2.5-7b` / `--no-model`.
With several Pythons installed, the installer uses the one the most of ELI's packages come
ready-made for on your machine, and the newest PyTorch build your GPU and driver can run.

**Windows** (double-click `install.bat`, or PowerShell):
```powershell
.\install.bat            # CUDA + frozen lock + GPU verify
.\eli.bat                # launch
```

That's it — the first run is a **blank slate** (no preloaded data) with a 10-second, skippable
"what should I call you?" intro.

## Choose your model

ELI needs one local **model** (the brain). The installer offers to fetch one sized to your
hardware, or pick your own — you can grab several and switch anytime:

```bash
python -m eli.core.model_download --choose   # multi-select menu — pick ANY number
python -m eli.core.model_download --auto      # one best-fit for your VRAM
```

| key | model | size | needs |
|---|---|---|---|
| `qwen2.5-3b` | Qwen2.5-3B-Instruct | ~1.8 GB | 4 GB GPU / CPU |
| `qwen2.5-7b` | Qwen2.5-7B-Instruct *(default)* | ~4.4 GB | 8 GB GPU |
| `qwen3-8b` | Qwen3-8B (40K ctx, reasoning; LoRA base) | ~4.7 GB | 8 GB GPU |
| `falcon3-10b` | Falcon3-10B-Instruct | ~5.9 GB | 12 GB GPU |
| `phi-4` | Phi-4 (14B dense, MIT) | ~8.4 GB | 12 GB GPU |
| `qwen3.6-35b-a3b` | Qwen3.6-35B-A3B (MoE, Apache-2.0) | ~20.6 GB | 24 GB GPU / CPU |
| `falcon-h1-34b` | Falcon-H1-34B-Instruct | ~18.9 GB | 24 GB GPU / CPU |

You can also drop **any `.gguf`** into `models/`, or point ELI at your own catalog
(`ELI_MODEL_CATALOG`). The tiny **embedder** (memory/RAG) installs automatically to
`models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf` (~80 MiB); vision is an optional extra.
Default TTS is Kokoro's **`af_heart`** (Kokoro-82M + whisper STT, fetched pinned and checksum-checked
on install or first use; the release builds carry the voice) — see
**[models/MODEL_LICENSES.md](models/MODEL_LICENSES.md)**. Want ELI to *speak in its own voice*
out of the box? Fine-tune your own model — see **[`docs/TRAINING_YOUR_OWN_MODEL.md`](docs/TRAINING_YOUR_OWN_MODEL.md)**.

## Optional: a remote view over your own network

The desktop app is the main event, but I also gave ELI a small built-in **home server** (a local
FastAPI app) so you can pull it up in any phone/tablet/desktop browser **on your own home network**
as a second screen. **The AI still runs entirely on your computer** — the browser is just a window
onto it. Nothing runs on the device, and nothing reaches the internet.

```bash
./scripts/eli_serve.sh             # this computer only  → http://127.0.0.1:8081/
./scripts/eli_serve.sh --lan       # your home network   → prints a token-protected URL
```

### The web app — eight tabs
The web app is installable (PWA: "Add to Home Screen", offline shell), has light/dark themes and a
sidebar, and lets you recolour the chat to taste.
- **Overview** — a live dashboard: clock, GPU/CPU/RAM gauges, audit-integrity status, device + corpora
  counts, quick actions, and a recent-activity feed that refreshes on its own.
- **Chat** — talk to ELI from any device; inference stays on the host. Streams replies token-by-token,
  renders **markdown + code** (with copy), keeps **multiple sessions/history**, and has **stop** and
  **regenerate**. Plus **"Talk to ELI"** voice: tap the mic to speak (your phone's audio is transcribed
  by ELI's *local* whisper model), and tick **Speak replies** to have answers read back in ELI's *local*
  voice — no cloud STT/TTS, the audio never leaves your machine.
- **Commands** — a searchable catalogue of every documented action (with descriptions and example
  phrases). Tap a phrase to drop it into the chat box.
- **Home** — ELI's **own** device server (see below): **find** devices on your network, group them into
  **rooms**, control them (toggles, brightness, per-room "all on/off"), build **scenes** and
  **automations** — all over **MQTT**, no Home Assistant, no cloud. ELI learns how you use them and
  suggests automations.
- **System** — ELI's own **measured** telemetry: GPU temperature/utilisation/VRAM, CPU load/temp,
  RAM, the loaded model, and uptime — the same grounded source ELI reports from, so the numbers are
  real, never guessed.
- **Research** — **shared** local document corpora for working alone or **together**: collaborators on
  the server ingest files (`.pdf` / `.txt` / `.md`), paste notes, and ask questions **grounded only in
  those sources, with citations** — all on ELI's own embedder + FAISS. Every contribution is
  **attributed** (who added/edited/asked what), shown as members + a per-corpus activity feed and
  recorded in the tamper-evident Audit trail. Documents never mix with ELI's memory; nothing leaves the box.
- **Audit** — a **tamper-evident** trail of every action (who did what, with what outcome). Each record
  is **HMAC-SHA-256 chained** to the one before it, keyed with a secret stored *separately* from the
  database (`config/.audit_hmac_key`, owner-only, or `$ELI_AUDIT_HMAC_KEY`). So any edited / deleted /
  reordered / downgraded record is detected — and a local attacker who can write the database but can't
  read the key can't forge a clean chain. The tab shows a live "verified intact / tampering detected"
  verdict and a per-user event list. (Keep the key off the DB's backups/media to preserve that property.)
- **Admin** — an enterprise management console (admin role): chain-integrity status, totals, a
  **per-user activity** rollup (click a user to drill into their recent actions), the **approval /
  risk-gate policy**, and **user management** — create **admin / member / viewer** accounts (each gets a
  one-time token) and revoke them.

### ELI device server (the Home tab) — a local home-AI, MQTT, no Home Assistant
ELI keeps its **own** device registry and talks to devices **directly over MQTT** — the open DIY-IoT
standard (ESPHome / Tasmota / Zigbee2MQTT, or anything that speaks MQTT). No Home Assistant, no vendor
cloud. It is a full local home-AI:
- **Find devices** — scan the LAN over **mDNS** for MQTT brokers + smart devices (one click fills the
  broker for you), or register a device by its command/state topics, or auto-populate from the standard
  retained MQTT discovery messages.
- **Control** — toggles, brightness, rooms with per-room "all on/off" — from the web, or by **voice**
  ("turn on the desk lamp", "turn off the kitchen").
- **Scenes** — name a group of device states ("Movie mode"); activate from the tab or by voice
  ("activate movie mode").
- **Automations** — a **trigger** runs **action(s)**: triggers are a clock time, **sunrise/sunset**
  (±offset, computed locally from your lat/lon — no network), or **when a device turns on/off**; actions
  control a device or activate a scene; optional **conditions** ("only if the TV is off") and
  **multi-action**.
- **Learns + proactive** — ELI tracks how you use devices (recorded as a preference in its memory),
  is **aware of the home state** when you ask in chat, and **suggests automations** from your patterns
  ("you usually turn the lamp on around 20:00 — automate it?") — accept and it's scheduled for real.

The broker/mDNS are local-network only: ELI registers **only your broker host** with its offline socket
guard (`netguard`), so the offline-by-default policy is unchanged for everything else. Credentials are
stored locally and never returned by the API.

### Security
- **The auth gate fails closed.** By default — no `ELI_API_TOKEN`, no explicit opt-out — every
  action endpoint returns `401`. The guarantee no longer depends on the launcher script: a raw
  `uvicorn api.server:app`, a Docker `CMD`, a systemd `ExecStart`, or any other ASGI-direct launch
  that never runs `main()` is **locked down**, not wide open.
- **Roles (admin / member / viewer), opt-in.** Define users (`python -m eli.runtime.api_users add <id>
  <role>`, or the Admin tab) and every request's token resolves to a **(user, role)**: a **viewer** is
  read-only (browse dashboards, no actions), a **member** acts but is `403`'d from the Admin console,
  an **admin** manages everything. **Attribution is authenticated** — a member can't claim to be someone
  else in the audit trail. With no users defined it stays single-operator (the loopback owner is admin).
  Tokens are stored only as **SHA-256 hashes**; the raw token is shown once.
- **Loopback is zero-friction.** Launch via `scripts/eli_serve.sh` (or `python -m api.server` on the
  default `127.0.0.1`) and same-machine use is tokenless — `main()` enables tokenless serving *only*
  for a genuine loopback bind (set `ELI_API_ALLOW_TOKENLESS=0` to require a token even locally).
- **Any non-loopback bind requires a token.** Expose beyond loopback (`--lan`, `ELI_API_HOST=0.0.0.0`)
  with no token and ELI **auto-generates one and prints it** rather than ever serving unauthenticated.
  Every endpoint — chat, command execution, smart-home/system controls, research ingest — sits behind it.
- **Research ingest is sandboxed.** The Research tab can only read documents under a configured root
  (default `artifacts/research/_sources/`, or set `ELI_RESEARCH_ROOT`); paths outside it are rejected and
  a directory ingest is bounded by file-count / total-byte caps. A client can never make the server read
  arbitrary host files.
- Keep it on your **own LAN**. Don't port-forward it to the public internet; it's a personal-network tool.

### Maintenance
- **Stable token:** set `ELI_API_TOKEN=<your-token>` (env or your service unit) so clients don't break
  on restart; otherwise a fresh token is generated each non-loopback start and printed to the logs.
- **HA token expired/rotated?** Re-enter it in the Home tab — it overwrites the stored one.
- **Port/host:** `ELI_API_PORT` (default 8081) and `ELI_API_HOST` (default `127.0.0.1`).

Full details: **[`docs/SERVER_AND_WEB_APP.md`](docs/SERVER_AND_WEB_APP.md)**.

## Privacy

Nothing leaves your computer unless you ask for something online (news, a search, downloading a
model) — and ELI tells you flat out when it does. No accounts, no telemetry, no subscription, no
asterisks. A fresh install knows **nothing** about you until you talk to it, and you can wipe your
data whenever you want.

## Documentation

- **[First-time installation](first%20time%20Installation/INSTALLATION_GUIDE.md)** — downloads, wizard completeness, hardware matrix
- **[Server & Web App](docs/SERVER_AND_WEB_APP.md)** — self-hosted FastAPI server + phone/web UI
- **[Train your own model](docs/TRAINING_YOUR_OWN_MODEL.md)** — A-to-Z LoRA/QLoRA into an ELI GGUF
- **[Cross-platform coverage](docs/CROSS_PLATFORM.md)** — capability × platform matrix
- **[Model runtime policy](docs/model_runtime_policy.md)** — how ctx/layers/batch are sized

## Under the hood

<details>
<summary><b>Architecture / project layout</b></summary>

- `eli/core` — paths, settings, contracts, hardware profile
- `eli/kernel` — control loop, cognitive engine, state models (`stages/` pipeline)
- `eli/cognition` — reasoning, grounding, agent bus, working memory
- `eli/memory` — episodic, semantic, FAISS vector index, knowledge graph
- `eli/planning` — goals, jobs, autonomy, proactive daemon, scheduling
- `eli/execution` — router + executor + **`effectors/`**: a dispatch **registry** where each action
  family is its own module. **199 actions across 42 effector modules** (of 224 `SUPPORTED_ACTIONS`);
  adding a capability is a drop-in module + one `register()` call, never a god-file edit. Each
  module loads on its own, and a packaged build that is missing one fails its self-test
- `eli/perception` — audio, screenshots, OS controller, TTS/STT
- `eli/runtime` — arbitration, verification, security policy
- `eli/plugins` — tool plugins (install/enable/disable at runtime)
- `eli/gui` — PySide6 desktop app, decomposed into `main_window/` (mixin-composed `EliMainWindow`)
  and `labs/` (one file per Labs tab)
- `eli/editions` · `eli/bundles` · `eli/licensing` — edition packs (a manifest each), bundles
  (separate ELI applications opened on this core) and the licence gate; a licence names products,
  and editions overlay the core, never fork it
- `eli/cli` — headless REPL (`eli --headless`)
- `config` — portable default settings · `models` — local GGUF payloads (gitignored)
- `tests` — a large pytest suite (including a `claims/` layer that checks the project against
  its own documentation); CI gates a cross-platform portable subset (no GGUF/display/GPU) on
  Linux, macOS, and Windows, and runs the whole suite on a Linux runner on every push to `main`
  (that run does not hold a release back yet)

**Scaling:** the loader reads each model's real `n_ctx_train` from GGUF metadata and fits
layers/batch/ctx to the hardware present; VRAM is summed across all GPUs. One path runs a 3B on a
laptop and a 35B on a workstation. Multi-GPU: enable a profile in `config/gpu_profiles.json` or set
`tensor_split`. Always local, never cloud, at every size.
</details>

<details>
<summary><b>Developer setup (from a source checkout)</b></summary>

```bash
python3 -m venv .venv && . .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-full.txt
python -m pip install -e .[full]
python -m eli            # GUI   ·   python -m eli --headless   # terminal REPL
```
Per-platform requirement profiles: `requirements.lock.txt` (frozen, reproducible),
`requirements-windows.txt`, `requirements-macos.txt`, `requirements-android.txt`,
`requirements-full.txt`. Headless slash commands: `/status`, `/mode`, `/reset`, `/help`, `/quit`.

Use the path helpers in `eli.core.paths` (`project_root`, `data_dir`, `models_dir`,
`user_db_path`, …) instead of hard-coded machine paths — the repo is designed to be movable.
Most users leave `ELI_PROJECT_ROOT` unset (auto-detected). Env reference: `.env.example`,
`.env.full.example`.
</details>

<details>
<summary><b>Packaging & releases</b></summary>

```bash
bash scripts/package_eli_release.sh                 # wheel/sdist
bash scripts/package_desktop_app.sh                 # portable Linux desktop package
bash build_packages.sh wheel deb appimage macos windows
```
A real Windows `.exe`/`.msi` must be built on Windows; a signed/notarized macOS `.dmg` on macOS.
Large model/voice binaries are distributed separately (GitHub Release assets) via
`scripts/upload_github_asset_files.py` / `restore_github_asset_files.py` — they exceed Git's
100 MB blob limit.
</details>

<details>
<summary><b>Cross-platform limits</b></summary>

Guards + aliases cover Windows, macOS, Linux, BSD, and Android/Termux, but no package makes every
OS permission instant: Windows may need SmartScreen approval + audio/COM packages; macOS needs
Screen-Recording/Accessibility permissions; Linux desktop control depends on Wayland/X11 +
PulseAudio/PipeWire; Android/Termux is headless-only; GPU acceleration depends on local drivers
(NVIDIA CUDA / AMD ROCm / Apple Metal — the installer auto-detects and builds for whichever you
have, falling back to CPU otherwise). Full matrix: **[`docs/CROSS_PLATFORM.md`](docs/CROSS_PLATFORM.md)**.
</details>

## Tested on & known limitations

*Last updated 2026-07-04.*

I'd rather tell you exactly what I've run than pretend it's flawless everywhere.

**What I've actually run, end to end:** Linux (x86_64) with an NVIDIA GPU. That's my machine, so
it's the one platform where I can honestly say the whole thing — install, first-run, the full test
suite (10,078+ passing), voice, vision, the server, all of it — genuinely works. If you're on
Linux + NVIDIA, you're on the tested path.

**What the code handles but I haven't run on real hardware yet:** AMD (ROCm), Windows, and macOS.
The installers, per-OS requirements, cross-platform paths, GPU detection (NVIDIA / AMD / Apple), and
the OS-abstraction layer are all there and I've read every line — but "correct in the code" isn't
the same as "confirmed on the metal," and I won't claim it is. If you run ELI on one of these, I'd
genuinely love the output (working or broken) so I can close the gap for everyone.

**The honest edges:**
- **Desktop control varies by OS.** The core — chat, memory, the model, the server, vision — is
  portable. The part that drives your actual desktop (window control, some audio, screenshots) is
  built on Linux and routed through a cross-platform layer; it's the piece most likely to be rough
  on Windows/macOS on first run.
- **macOS needs permissions.** The first time ELI takes a screenshot or moves the mouse, macOS asks
  for Screen-Recording and Accessibility access (System Settings → Privacy). Grant them and retry.
- **AMD voice is CPU-only.** The speech-to-text engine (CTranslate2) has no ROCm support, so on an
  AMD GPU it stays on the CPU — it works, just not accelerated. Everything else uses your AMD GPU.
- **Windows secret-file permissions are weaker.** ELI locks its token/key files to `0600` on Unix;
  Windows ignores that bit, so those files lean on your user profile being private (which it is by
  default under `%APPDATA%`). A proper Windows ACL is on the list.
- **Big models can be slow to load or run out of memory.** A large model takes a minute on first
  use, and if VRAM/RAM is tight the server can drop mid-reply. Running the server on its own (not
  alongside the desktop app) loads the model once and avoids most of that.
- **Android/Termux is headless** — server + CLI only, no desktop GUI.

None of these are hidden gotchas — they're the honest edges of a local, embodied assistant that
touches real hardware. I'd rather you know them going in.

## Security

Defence-in-depth, all local:

| Layer | Protects against |
|---|---|
| Prompt-injection guard | Strips `[INST]`, `<\|im_start\|>system`, jailbreak phrases before the model sees input |
| SQL identifier validation | Allowlist regex on every f-string SQL identifier — no injection via table/column names |
| Shell gate (`RUN_CMD`) — denylist | Destructive patterns (`rm -rf /`, `mkfs`, fork bombs) and dangerous executables (`dd`, `fdisk`, …) are **denylisted**. With Full Control off (the default), every command in the line is checked — after `;`, `&&`, `\|`, inside `$( )` and backticks: commands that only read run (read-only subcommands of tools like `git status` count), anything else must be listed in `ELI_ALLOWED_CMDS`, and writing a file needs Full Control |
| Shell gate — Full Control caveat | The **ELI Full Control** toggle (off by default) **bypasses the shell gate entirely** — every command runs, *including* the destructive denylist. Enable it only when you intend ELI to have full machine access |
| Custom-agent trust | SHA-256 registry — unregistered/tampered agent files are skipped at load |
| Offline-by-default | A process-wide network guard fails closed unless a task is explicitly authorised online |

## Project status & contributing

I work on ELI most weeks and keep shipping. I'm the one who decides what goes in and when it
releases — but bug reports and ideas are genuinely welcome, and I'll keep it moving as long as it's
useful to people. So far, it very much is.

- **Found a bug or have an idea?** Email [jaybridgeman0095@gmail.com](mailto:jaybridgeman0095@gmail.com).
- **Want to contribute code?** Please read **[CONTRIBUTING.md](CONTRIBUTING.md)** first. ELI is
  proprietary, so contributions include a short **inbound license grant**.
- **Security issue?** See **[SECURITY.md](SECURITY.md)** — report it privately, not in a public issue.
- **Want to build on ELI?** Plugins, MCP servers and bundles for the marketplace are yours to license
  and price — see **[docs/MARKETPLACE.md](docs/MARKETPLACE.md)** and the developer terms.

## Licensing & purchasing

ELI v3 is **paid, proprietary software**: you buy it once and keep it. The full terms are the
**[ELI End User Licence Agreement](COMMERCIAL_EULA.md)** (final wording shown at checkout), plus the
supplement for your tier in [docs/eula/](docs/eula/).

| | |
|---|---|
| **Payment** | One-time. No subscription |
| **Updates** | Every 3.x release is included. ELI v4 will be a separate, paid upgrade |
| **Tiers** | Personal (1 machine) · Pro (up to 3 machines, commercial use) · Enterprise (per order) — a 7-day trial runs first |
| **Internet** | Not needed to keep using it — the key is checked on your machine, and nothing phones home |
| **Reinstall / new machine** | Allowed — re-activate with your key |
| **Your data** | Yours. ELI never sends your conversations, memories or files anywhere |
| **Modify** | For your own licensed machines; **no redistribution** |
| **Bundles** | ELI Legal, ELI VR and the other official bundles are separate products, bought separately ([schedules](docs/eula/bundles/)) |

**Buying a license:** see **[docs/STORE.md](docs/STORE.md)** (Ko-fi / email until automated checkout).
Pro, Enterprise, hosting rights, or bulk: **[jaybridgeman0095@gmail.com](mailto:jaybridgeman0095@gmail.com)**.

### License key & activation

1. Purchase → receive a **license key** (receipt / email).
2. First run → **enter the key** (or `eli --activate-key …` from the CLI). A bundle's key is
   entered the same way; each product has its own key.
3. The key is verified **on your machine** against a signature built into ELI. A key bought from the
   store is **activated once online**: ELI sends the key and this computer's activation code (a
   one-way code made from the operating system's install id) to ELI's licence server, which counts
   the computer against the licence's seats. After that no connection is needed. No account, and
   activation never uploads your chats, memories or documents.
4. Moving to a new computer: `eli --deactivate-license` on the old one gives its seat back. A
   computer that cannot go online: `eli --activation-code` shows its code; activate it from any
   other device at https://licences.geteli.tech/activate.

Details: **[COMMERCIAL_EULA.md](COMMERCIAL_EULA.md)** §5 · how it works:
**[docs/COMMERCIAL_LICENSING.md](docs/COMMERCIAL_LICENSING.md)**.

## License

ELI v3 is **proprietary commercial software** — © 2026 Jason Fitzgibbon Bridgeman (GitHub: [ShadowESC95](https://github.com/ShadowESC95), <jaybridgeman0095@gmail.com>). All rights
reserved. It is licensed only under the **[ELI End User Licence Agreement](COMMERCIAL_EULA.md)**
(see [LICENSE](LICENSE)); having a copy of the software or its source does not by itself grant a
licence. Third-party components keep their own licences — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Seeing ELI redistributed, hosted, or sold somewhere?** That is not permitted.
Please report it — with a link — to [jaybridgeman0095@gmail.com](mailto:jaybridgeman0095@gmail.com).

## Contact

Questions, feedback, or interested in a license/services beyond the terms above?

- **Email:** [jaybridgeman0095@gmail.com](mailto:jaybridgeman0095@gmail.com)

For **purchasing**, **commercial licensing**, redistribution rights, or hosting beyond the
[license](LICENSE), see **[Licensing & purchasing](#licensing--purchasing)** or email directly.
