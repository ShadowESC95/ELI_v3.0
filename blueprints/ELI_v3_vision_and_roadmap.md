# ELI v3 — Vision & Roadmap

v2 proved the *concept* end-to-end: a local-first, offline-by-default, embodied assistant
that talks, sees, remembers, learns, acts on your machine, and does it all on your own
hardware with no cloud. **v3 is where that stops being an accretion and becomes an
architecture.** v2 is the baseline; the good parts carry over, the god-files don't.

This is the founding doc. It's opinionated on purpose.

---

## North star

> A private assistant that **understands you** deeply, **acts** reliably, and **gets
> better on its own** — architected so every part of it is testable, auditable, and
> replaceable.

Three principles, in priority order:
1. **Honest by construction** — offline by default, fail-closed, no fabricated actions or
   answers. (v2 already lives this; v3 keeps it as a hard invariant.)
2. **Testable by construction** — logic is separated from side-effects so it *can* be
   verified. v2 caps at ~49% coverage because logic and I/O are tangled; v3 fixes the
   shape, not the symptom.
3. **Emergent, not scripted** — the persona and the proactivity *emerge* from a real,
   deepening model of you. We enrich the inputs, we never hand-write the character.

---

## The core architectural bet — planner / effector split

**The problem in v2:** `executor_enhanced.py` is a ~14k-line file where *decide → validate
→ format* is welded to *actually open the app / run the shell*. That's why coverage stalls
at 41% there, why bugs hide (the dead `tool`/`name` alias lived there unnoticed until a
test found it), and why the approval gate has no clean seam.

**v3:** every action is two pieces.
- **Planner** — pure: takes intent + args + state, returns a *plan* (what to do, with what,
  and why) or a refusal. No side-effects. Unit-testable to 90%+.
- **Effector** — thin: takes a plan, performs the one side-effect, returns a structured
  result. The only untestable part, and it's tiny.

Everything good hangs off this seam: the approval gate wraps plans, the audit ledger logs
plans, autonomy proposes plans, and the whole decision surface becomes verifiable.

**This is the first thing to build.** The rest stands on it.

---

## The intelligence bets

### 1. A genuinely *learned* user model
v2's `profile_extractor` is a regex→canned-phrase table (it literally hardcodes
`active_project = "developing ELI"`). The persona is emergent — but it's emerging from a
shallow profile. **v3:** a background pass LLM-extracts an evolving model of you —
interests, projects, tone, relationships, routines — each with a confidence and a decay.
The emergent voice gets dramatically richer because it draws from a real understanding of
the person, with nothing about the *character* scripted.

### 2. Close the autonomy loop
v2 has the parts but they don't connect: frequency-habits are *detected* but never become
rules, so they're never *proposed*; the `FrontierAgent` is dormant. **v3** makes it a loop:
**detect a pattern → propose it → learn from whether you accept → adjust future
proposals.** Autonomy stays proposal-only and approval-gated (the safety model is right in
v2) — but it actually *improves* at anticipating you instead of re-detecting the same
things forever.

### 3. Elevate every agent to the coding-agent standard
The coding agent is v2's template: *plan → DAG → verify → repair.* The other bus agents are
retrieval-level (fetch context, hand to the model). **v3:** give research, memory, and file
agents the same act→verify→self-correct loop. ELI becomes genuinely *capable* across
domains, not just conversational.

---

## The systems bets

### 4. A real model-router
v2 loads one big model and does everything with it. **v3** routes by task on the DAG
substrate v2 already built: a tiny fast model for intent/routing, the big model for
reasoning, the vision model on demand. Faster and cheaper — and it honours the "keep the
35B" call by spending its latency *only* where it earns it.

### 5. One tuned memory service
v2 has four SQLite stores + FAISS + KG, but three retrieval-fusion weights are still
un-tuned defaults and there's no episodic/semantic split or consolidation. **v3:** a single
coherent memory service with a tuned ranker and a nightly **consolidation** pass — promote
recurring facts, decay noise. Sleep, for the assistant.

---

## What carries over from v2 (the proven parts)

Keep, largely as-is — these work and are hard-won:
- **Offline-by-default netguard** (process-wide socket failsafe) and the **fail-closed**
  posture.
- **The approval engine + audit ledger** security model (proposal → policy → confirm).
- **The DAG orchestrator** as the execution substrate (v3 leans on it *more*).
- **The emergent-persona philosophy** and the crisis-safety guard.
- **The web/phone server + smart-home (own MQTT)**, model hot-swap, and VRAM smart-loader.
- **The offscreen-Qt testability pattern** for the GUI.

## What v2 taught us to leave behind
- **God-files** (`executor_enhanced`, the big `engine`) — decompose behind the
  planner/effector seam.
- **Regex-scraped intelligence** (profile, habit→rule) — replace with learned passes.
- **Retrieval-level agents** — raise them to the coding-agent bar.
- **Untuned defaults** (memory fusion weights) — measure and tune.

---

## Sequencing (what to build first)

1. **Planner/effector split** on one action family (e.g. app/window control) — prove the
   pattern, get it to 90% coverage, wire the approval gate to plans.
2. **Roll the split across the executor**, retiring the god-file incrementally.
3. **Learned user model** (background extractor) — feeds the persona immediately.
4. **Autonomy loop closure** (habit → rule → proposal → learn).
5. **Model-router** + **memory consolidation** — the systems polish.

Everything is test-gated: no capability lands without a behavioural eval, and the eval
harness (carried from v2) becomes the regression gate from day one.

---

*v2 got ELI to "it works." v3 is "it's built right." Same soul — private, embodied,
honest, yours — on an architecture that can actually grow.*
