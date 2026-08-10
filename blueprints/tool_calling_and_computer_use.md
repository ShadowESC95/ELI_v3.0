# Tool calling and computer use

Two capabilities that turn ELI from an assistant that *describes* actions into one that
takes them and can prove it. They share a single design commitment: **an action ELI
cannot actually perform must be impossible to claim**, not merely discouraged.

See `mcp.md` for the Model Context Protocol integration, which has its own page.

---

## 1. Grammar-constrained tool calling

`eli/cognition/llm_intent.py` resolves free text to one of ELI's real actions. It used
to ask the model for JSON and then salvage whatever came back — hence the markdown-fence
stripper and the `{.*}` regex still visible in its parsing path.

That salvage had three failure modes:

| Failure | What the model emitted | Old result |
|---|---|---|
| Fenced output | ` ```json {...} ``` ` | stripped by regex, usually recovered |
| Truncation | `{"action": "OPEN_APP", "args": {"path": "/very/long/…` | unparseable → CHAT |
| Invented action | `{"action": "ORDER_TAXI"}` | not in catalogue → CHAT |

Each collapse to CHAT is worse than it looks: the model, having decided it was going to
do the thing, then *narrates* doing it. That is the exact confabulation ELI is built to
prevent.

Now a GBNF grammar is built from the live catalogue and passed to the decoder:

```
root    ::= "{" ws "\"action\":" ws action ws "," ws "\"args\":" ws object
            ws "," ws "\"confidence\":" ws number ws "}"
action  ::= "\"CHAT\"" | "\"DATE\"" | "\"OPEN_APP\"" | …   (every real action)
```

An invented capability is now *unrepresentable* rather than rejected after the fact,
and the JSON cannot be malformed or truncated mid-object.

Notes:

* `CHAT` is admitted alongside the catalogue even though it is an internal action.
  Omitting it would make ordinary small talk unrepresentable and force every greeting
  to be answered as a command.
* The grammar is cached on a hash of the sorted action names, so it is built once per
  distinct catalogue rather than per turn.
* Backends without grammar support (Ollama, remote) raise `TypeError` on the kwarg and
  fall through to the original free-text path, which still parses and validates exactly
  as before.

---

## 2. Computer use — the closed loop

`eli/execution/effectors/computer_use.py`, action `COMPUTER_USE`.

Every primitive already existed: `SCREEN_READ_ANALYZE` (vision + OCR), `SCREEN_LOCATE`
(find a thing, get coordinates, optionally click), and `os_controller`'s mouse, keyboard
and clipboard. What was missing was the loop binding them, so a goal spanning several
steps could only ever be answered with one screenshot and a description.

```
observe ──► decide ──► gate ──► act ──► verify ──┐
   ▲                                             │
   └─────────────── repeat until ────────────────┘
        DONE · ABORT · budget · timeout · stuck
```

* **observe** — one honest look (`SCREEN_READ_ANALYZE`), reduced to a digest used to
  detect whether anything actually changed.
* **decide** — one step, grammar-constrained to a closed vocabulary:
  `LOCATE_AND_CLICK`, `TYPE_TEXT`, `PRESS_KEY`, `WAIT`, `DONE`, `ABORT`. An invented
  step degrades to `ABORT`.
* **gate** — every step passes `authority_gate.check()` before it happens.
* **act** — through the existing, already-gated primitives.
* **verify** — re-observe. A step that neither succeeded nor changed the screen counts
  as no progress; three in a row stops the run.

Bounds: 12 steps by default (40 max), a 180-second wall clock, and the no-progress
detector. `ELI_COMPUTER_USE=0` disables it entirely.

The report lists what actually happened, step by step. A click that was attempted but
did not land is reported as failed — `SCREEN_LOCATE` distinguishes *found* from
*clicked*, and the loop never upgrades one to the other. A claimed click that never
landed leaves the user believing their machine is in a state it is not, which is the
most expensive form of confabulation ELI can commit.

---

## Failure reporting

A click that misses, a model that cannot be reached, a step budget that runs out — each
returns its real reason. Reporting an action as done when it was not is the failure mode
both of these features exist to make impossible.
