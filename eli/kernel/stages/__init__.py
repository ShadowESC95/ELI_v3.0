"""Engine stages — the decomposed CognitiveEngine.

`eli/kernel/engine.py` holds one CognitiveEngine class (~94 methods) plus ~76
module-level helpers. Its pipeline stages move here one at a time, verbatim and
verified, the same way labs_tab became eli.gui.labs and the main window became
eli.gui.main_window.

Shape
-----
Stages are **mixins** composed onto CognitiveEngine — the class keeps one
identity and one `process()` orchestrator, while each stage owns a cohesive
slice of behaviour in its own file:

    class CognitiveEngine(MemoryCommitMixin, ...):
        def process(...):   # stays: the orchestrator that calls the stages

`_common.py` holds primitives shared by the engine and its stages (e.g. the
optional response-governance soft-import), so they agree on one source instead
of each re-deriving it.

`fast_actions.py` stays module-level (not a mixin): it is the Phase-45 direct
fast-action table, not engine behaviour.

Named `stages` (not `pipeline`) — eli.kernel.pipeline is the existing
pipeline-description module.

Moved so far
------------
    _common.py        shared stage primitives: governance soft-import,
                      _eli_path_get, _is_brief_phatic_prompt (used by the engine
                      AND the stages — one source, not re-derived per module)
    memory_commit.py  MemoryCommitMixin — Stage 12: decide what is worth
                      remembering, store the user/assistant turns, learn from
                      the executed result
    retrieval.py      RetrievalMixin — tiered memory fetch: relevant-memory
                      recall, recent-topic summary, runtime memory snapshot
    fast_actions.py   Phase 45 direct fast-actions (authoritative executor results)

Planned next (pipeline order, per blueprints/v3_monolith_decomposition.md §3):
    lifecycle.py       __init__ / _init_gguf / shutdown / background loops
    prompt_assembly.py persona brief + evidence/working-memory assembly
    reasoning_modes.py chain-of-thought / tree-of-thought / self-consistency /
                       constitutional / chat-reasoning loop
    generation.py      streaming + generate + chat-response finalisation
"""
from eli.kernel.stages.memory_commit import MemoryCommitMixin
from eli.kernel.stages.retrieval import RetrievalMixin

__all__ = ["MemoryCommitMixin", "RetrievalMixin"]
