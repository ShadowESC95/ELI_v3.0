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
    reasoning_modes.py ReasoningModesMixin — chain-of-thought / tree-of-thought /
                      self-consistency / constitutional, the chat reasoning loop,
                      and answer synthesis
    prompt_assembly.py PromptAssemblyMixin — builds the enhanced system prompt,
                      gathers/attaches executor evidence, assembles the grounded
                      evidence + precise working-memory context, splices the
                      persona hand-off
    generation.py     GenerationMixin — the model-facing half: calls the local
                      model (streaming or one-shot), governs/normalises the
                      visible text, runs the rapport/semantic conversational
                      guards, finalises the chat result
    guards.py         output/evidence guards shared by the engine and the stages:
                      failed-executor detection + honest surfacing, identity
                      self-report guards, reasoning-scaffold strip
    persona_text.py   persona + identity text prep shared by the engine and the
                      prompt_assembly/generation stages (persona load, identity-
                      context sanitise); a leaf module so mixins import it without
                      a cycle back to engine
    output_text.py    assistant-output text normalisation shared by the engine
                      (process/correction) and the generation stage
    model_runtime.py  model-runtime access seam (soft-imported inference module +
                      broker) shared by the engine and the generation stage — one
                      binding both resolve to
    fast_actions.py   Phase 45 direct fast-actions (authoritative executor results)

Planned next (pipeline order, per blueprints/v3_monolith_decomposition.md §3):
    lifecycle.py       _init_gguf / shutdown / background loops (__init__ stays
                       on CognitiveEngine — it is the class's own constructor)
"""
from eli.kernel.stages.generation import GenerationMixin
from eli.kernel.stages.memory_commit import MemoryCommitMixin
from eli.kernel.stages.prompt_assembly import PromptAssemblyMixin
from eli.kernel.stages.reasoning_modes import ReasoningModesMixin
from eli.kernel.stages.retrieval import RetrievalMixin

__all__ = [
    "GenerationMixin",
    "MemoryCommitMixin",
    "PromptAssemblyMixin",
    "ReasoningModesMixin",
    "RetrievalMixin",
]
