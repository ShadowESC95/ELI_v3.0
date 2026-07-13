"""Engine stages — the decomposed CognitiveEngine (strangler target).

`eli/kernel/engine.py` is ~13.5k lines: one CognitiveEngine class plus ~80 helpers.
This package is where its stages move, one at a time (verbatim + shim + verified),
the same way labs_tab became eli.gui.labs.

Planned stages (pipeline order):
    retrieval.py         tiered memory fetch + HyDE expansion
    prompt_assembly.py   persona brief + working-memory + awareness assembly
    reasoning_modes.py   quick / reflective / critique-revise / tree-of-thought
    streaming.py         Stage 11 generation (streaming + fallback)
    status_injection.py  self-status / profile / home-awareness injection
    learning_commit.py   Stage 12 — learning + state commit
    fast_actions.py      Phase 45 direct-fast-actions (authoritative executor results)
    guards/              echo / failed-executor / prompt-injection guards

Named `stages` (not `pipeline`) — eli.kernel.pipeline is the existing pipeline-
description module. Empty until the first stage moves.
"""
