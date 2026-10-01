"""Engine stage — reasoning modes. The per-mode reasoning strategies (chain-of-thought, tree-of-thought, self-consistency, constitutional) plus the chat reasoning loop and answer synthesis. Mixed into CognitiveEngine; process() selects the mode and calls into these."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import time

from eli.kernel.stages._common import _clarifier_norm, _eli_test_mode
from eli.kernel.stages.guards import (
    _REPEAT_MIN_SENTENCE_CHARS,
    _eli_bad_identity_self_report_output,
    _eli_identity_self_report_request,
    _failed_executor_is_failed,
    _failed_executor_surface,
    _is_repeat_of_recent,
    _opens_by_echoing,
    _strip_reasoning_scaffold,
    _strip_repeated_opening,
)
from eli.utils.log import get_logger
from eli.cognition.output_governor import govern_output

log = get_logger(__name__)

class ReasoningModesMixin:
    """Engine stage — reasoning modes. The per-mode reasoning strategies (chain-of-thought, tree-of-thought, self-consistency, constitutional) plus the chat reasoning loop and answer synthesis. Mixed into CognitiveEngine; process() selects the mode and calls into these."""

    def _run_tree_of_thoughts(self, user_input: str, working_context: str,
                              gen_overrides: Dict[str, Any], situation_brief: str,
                              k: Optional[int] = None) -> str:
        """Branch-and-develop: propose K approaches, score, develop the best.

        K, per-stage max_tokens, and per-stage temperatures come from
        `mode_presets["tree_of_thoughts"]` written by hardware_profile
        at first-run / re-tune. Nothing static here.
        """
        preset = self._mode_profile("tree_of_thoughts")
        k = int(k if k is not None else preset.get("branches", 3))
        # Tree DEPTH (tier-mapped: small=1 single-level, frontier=4). >1 deepens the tree by
        # refining the strongest path each level (beam-width-1) before the final develop.
        depth = max(1, int(preset.get("depth", 1) or 1))
        # Speed-aware cap: a big-but-slow (CPU-offloaded) model gets the size-tier's wider/deeper
        # tree dialled back toward single-pass so it doesn't spend minutes per extra branch/level.
        try:
            from eli.core.model_tier import speed_passes as _spass
            k, depth = _spass(k), _spass(depth)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        max_tok_propose = int(preset.get("max_tokens_propose", 600))
        max_tok_develop = int(preset.get("max_tokens_develop", 1500))
        temp_propose = float(preset.get("temperature_propose", 0.6))
        temp_develop = float(preset.get("temperature_develop",
                                         gen_overrides.get("temperature", 0.4)))

        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("tree_of_thoughts", 1, 2, "branch_tree_proposal")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        propose_prompt = (
            f"For the following request, propose {k} DISTINCT high-level approaches. "
            f"For each: name it (1-5 words), state the core idea (1 sentence), "
            f"and rate feasibility 1-10 with one-line justification. "
            f"Each approach must be a distinct ANGLE or FRAMING OF THE ANSWER ITSELF — a "
            f"specific aspect, mechanism, or line of explanation to actually deliver — NOT a "
            f"research method ('look at the literature', 'do more research', 'run a simulation', "
            f"'consult experts'). Those are non-answers and are forbidden as approaches. "
            f"Output as a numbered list. Do NOT write the full answer yet — just enumerate angles.\n\n"
            f"REQUEST: {user_input}"
        )
        propose_overrides = dict(gen_overrides or {})
        propose_overrides["temperature"] = temp_propose
        propose_overrides["max_tokens"] = max_tok_propose
        candidates = self._get_chat_response(
            propose_prompt, working_context,
            reasoning_mode="tree_of_thoughts", gen_overrides=propose_overrides,
            situation_brief=situation_brief,
        )
        log.debug(f"[REASONING][ToT] proposed {k} candidates ({len(candidates)} chars, "
              f"max_tok={max_tok_propose}, temp={temp_propose})")

        # Multi-level tree deepening (depth > 1, capable models only). Beam width 1: keep the strongest
        # path and expand it into more specific sub-angles a level at a time, so cost is k proposals per
        # extra level, not k**depth. For the small model depth == 1, so this never runs.
        for _level in range(2, depth + 1):
            try:
                from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
                _frs("tree_of_thoughts", _level, depth + 1, "branch_refinement")
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            refine_prompt = (
                f"Candidate angles for the request (internal — NOT shown to the user):\n\n"
                f"{candidates}\n\n"
                f"Silently pick the STRONGEST angle, then propose {k} DEEPER, more specific "
                f"sub-angles that REFINE it — each a concrete facet, mechanism, or sub-question "
                f"to develop in the final answer. Same rule: real angles OF THE ANSWER, never "
                f"research methods. Numbered list; do NOT write the full answer yet.\n\n"
                f"REQUEST: {user_input}"
            )
            candidates = self._get_chat_response(
                refine_prompt, working_context,
                reasoning_mode="tree_of_thoughts", gen_overrides=propose_overrides,
                situation_brief=situation_brief,
            )
            log.debug(f"[REASONING][ToT] depth {_level}/{depth} refined "
                      f"({len(candidates)} chars)")

        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("tree_of_thoughts", depth + 1, depth + 1, "highest_branch_development")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        develop_prompt = (
            f"You internally evaluated {k} angles (NOT SHOWN TO USER):\n\n"
            f"{candidates}\n\n"
            f"Pick the strongest angle silently and DELIVER THE ACTUAL ANSWER now — the real "
            f"substance the user asked for: the concrete facts, the mechanism, the explanation, "
            f"the analysis. "
            f"FORBIDDEN: describing your method or deferring instead of answering. Never write "
            f"'let's delve into', 'we can explore', 'one promising method is to look at the "
            f"literature', \"I'd be happy to discuss\", 'this will provide a comprehensive "
            f"understanding', or any sentence ABOUT answering — those are non-answers. If you "
            f"lack grounded detail, give the best substantive answer from your own knowledge and "
            f"flag what's uncertain; never substitute a description of how you would answer for "
            f"the answer itself. "
            f"DO NOT mention which approach you picked. No 'Approach 1:', 'Selected:', 'Plan:', "
            f"'Strategy:', no preamble, no reasoning narration. "
            f"Begin directly with the substantive answer in natural prose.\n\n"
            f"ORIGINAL REQUEST: {user_input}"
        )
        develop_overrides = dict(gen_overrides or {})
        develop_overrides["temperature"] = temp_develop
        develop_overrides["max_tokens"] = max_tok_develop
        final = self._get_chat_response(
            develop_prompt, working_context,
            reasoning_mode="tree_of_thoughts", gen_overrides=develop_overrides,
            situation_brief=situation_brief,
        )
        # Strip leftover scaffolding if the model still leaked deliberation prefixes
        final = _strip_reasoning_scaffold(final)
        log.debug(f"[REASONING][ToT] developed final ({len(final)} chars, "
              f"max_tok={max_tok_develop}, temp={temp_develop})")
        return final

    def _run_self_consistency(self, user_input: str, working_context: str,
                              gen_overrides: Dict[str, Any], situation_brief: str,
                              n: Optional[int] = None) -> str:
        """Sample N independent answers, then LLM-pick the most consistent.

        N and per-sample budget come from `mode_presets["self_consistency"]`.
        """
        preset = self._mode_profile("self_consistency")
        n = int(n if n is not None else preset.get("samples", 3))
        # Speed-aware cap: on a big-but-slow (CPU-offloaded) model, dial the size-tier's sample
        # count back toward 1 so a casual turn doesn't run four 3-minute generations. Never caps
        # the per-sample LENGTH — only how many samples.
        try:
            from eli.core.model_tier import speed_passes as _spass
            n = _spass(n)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        sample_max_tok = int(preset.get("max_tokens_per_sample", preset.get("max_tokens", 1100)))
        final_max_tok = int(
            preset.get(
                "max_tokens_final",
                (gen_overrides or {}).get("max_tokens", max(sample_max_tok, int(preset.get("max_tokens", sample_max_tok))))
            )
        )
        sample_temp = float(preset.get("temperature", 0.7))

        samples: list = []
        sample_overrides = dict(gen_overrides or {})
        sample_overrides["temperature"] = max(sample_temp, 0.6)
        sample_overrides["max_tokens"] = sample_max_tok
        _sc_total_stages = n + 1  # n samples + 1 consensus selection pass
        for i in range(n):
            try:
                from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
                _frs("self_consistency", i + 1, _sc_total_stages, "sample_generation")
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            s = self._get_chat_response(
                user_input, working_context,
                reasoning_mode="self_consistency", gen_overrides=sample_overrides,
                situation_brief=situation_brief,
            )
            samples.append(s)
            log.debug(f"[REASONING][SelfConsistency] sample {i+1}/{n} ({len(s)} chars, "
                  f"max_tok={sample_max_tok}, temp={sample_temp:.2f})")

        # One sample (e.g. speed-capped on a slow model) → no consensus to take; return it
        # directly rather than spending a wasted extra "select" generation on a single answer.
        # No agreement signal is possible with one sample, so leave it unset.
        self._last_sc_agreement_ratio = None
        if len(samples) <= 1:
            return _strip_reasoning_scaffold(samples[0]) if samples else ""

        # True consensus first: if a strict majority of the independent samples converge on the same
        # answer, return it. Self-consistency means agreement across samples, not picking the most
        # eloquent one. That's the case for facts, values and short answers; long divergent prose falls
        # through to consensus synthesis below.
        _maj = self._self_consistency_majority(samples)
        if _maj is not None:
            log.debug(f"[REASONING][SelfConsistency] majority consensus ({len(_maj)} chars, "
                      f"of {len(samples)} samples)")
            return _strip_reasoning_scaffold(_maj)

        # Selection: synthesise the CONSENSUS across samples (keep what most agree on).
        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("self_consistency", _sc_total_stages, _sc_total_stages, "consensus_selection")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        labelled = "\n\n".join(f"=== SAMPLE {i+1} ===\n{s}" for i, s in enumerate(samples))
        select_prompt = (
            f"{n} independent attempts to answer the same request are below. They may differ. "
            f"Write the CONSENSUS answer: KEEP every claim that MOST attempts agree on, DISCARD "
            f"any claim that only one attempt makes (likely a hallucination), and where attempts "
            f"conflict, resolve toward the majority. The result must be a complete, substantive "
            f"answer in its own right — not a note about the attempts. "
            f"Output ONLY the final consensus answer, no preamble, no 'Sample N', no mention of "
            f"the attempts.\n\n"
            f"REQUEST: {user_input}\n\n{labelled}"
        )
        select_overrides = dict(gen_overrides or {})
        select_overrides["temperature"] = 0.2
        select_overrides["max_tokens"] = max(sample_max_tok, final_max_tok)
        chosen = self._get_chat_response(
            select_prompt, working_context,
            reasoning_mode="self_consistency", gen_overrides=select_overrides,
            situation_brief=situation_brief,
        )

        # On a Q3 7B the selector often echoes the labelled bundle back instead of choosing ("===
        # SAMPLE 1 ===\nI'll perform a runtime audit..."). Detect that, fall back deterministically to
        # the longest non-trivial sample, and strip stray "=== SAMPLE N ===" headers.
        import re as _re_sc
        # Catch BOTH leaked forms the 7B produces: "=== SAMPLE 2 ===" and a bare
        # "SAMPLE 2:" header prefix.
        _leak_re = r"(?:===\s*)?\bSAMPLE\s+\d+\s*(?:===|:)"
        _has_leak = bool(_re_sc.search(_leak_re, chosen or ""))
        if _has_leak:
            log.debug("[REASONING][SelfConsistency] selector leaked sample markers — using longest-sample fallback")
            _candidates = [s for s in samples if s and len(s.strip()) > 20]
            if _candidates:
                chosen = max(_candidates, key=lambda s: len(s.strip()))
            else:
                chosen = samples[0] if samples else chosen
        # Strip any residual marker (either form), anchored at line start.
        chosen = _re_sc.sub(r"(?m)^\s*(?:===\s*)?SAMPLE\s+\d+\s*(?:===|:)?\s*", "", chosen or "").strip()
        chosen = _strip_reasoning_scaffold(chosen)

        log.debug(f"[REASONING][SelfConsistency] selected ({len(chosen)} chars)")
        return chosen

    def _run_chain_of_thought(self, user_input: str, working_context: str,
                              gen_overrides: Dict[str, Any], situation_brief: str) -> str:
        """Two-stage private CoT: private scratchpad reasoning → final clean answer.

        Stage 1 (private): generate a structured, exhaustive reasoning chain — never
        shown to the user; only used as context for stage 2.
        Stage 2 (final):   condense the private reasoning into a clean, direct answer
        with no step-narration or preamble.

        Per-stage budgets come from `mode_presets["cot"]` keys
        `max_tokens_reasoning` / `max_tokens_final` (or fall back to 60%/80% of
        the total `max_tokens` budget).
        """
        preset = self._mode_profile("chain_of_thought")
        max_tok_total = int(
            preset.get("max_tokens") or (gen_overrides or {}).get("max_tokens") or 2048
        )
        if max_tok_total <= 0:
            max_tok_total = 2048
        max_tok_reason = int(preset.get("max_tokens_reasoning", max(512, int(max_tok_total * 0.60))))
        max_tok_final  = int(preset.get("max_tokens_final",     max(512, int(max_tok_total * 0.80))))
        base_temp      = float(preset.get("temperature", 0.5))
        temp_reason    = float(preset.get("temperature_reasoning", min(0.50, base_temp)))
        temp_final     = float(preset.get("temperature_final",     max(0.10, base_temp - 0.15)))

        # Stage 1: private chain-of-thought scratchpad
        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("chain_of_thought", 1, 2, "private_scratchpad_reasoning")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        reason_prompt = (
            "Think through the following request step by step. "
            "This is your PRIVATE reasoning scratchpad — it will NOT be shown to the user. "
            "Work through: what is being asked, what relevant facts or context apply, "
            "what assumptions are safe to make, what the answer should cover, "
            "and any edge cases or ambiguities. "
            "Be explicit, exhaustive, and structured. Use numbered steps.\n\n"
            f"REQUEST: {user_input}"
        )
        reason_overrides = dict(gen_overrides or {})
        reason_overrides["temperature"] = temp_reason
        reason_overrides["max_tokens"]  = max_tok_reason
        # Suppress the model's own <think> wrapper on a reasoning model. This stage asks for
        # numbered-step prose that stage 2 reads, but the model wraps it in <think> and if it hits
        # max_tokens before closing, _strip_think_text drops everything, so stage 2 gets an empty
        # scratchpad and re-thinks (170-230s burned per pass). With the closed-think prefill the
        # reasoning is written as prose. See gguf_inference.force_no_think.
        try:
            from eli.cognition.gguf_inference import force_no_think as _fnt
        except Exception:
            from contextlib import nullcontext as _fnt  # type: ignore
        with _fnt():
            private_reasoning = self._get_chat_response(
                reason_prompt, working_context,
                reasoning_mode="chain_of_thought", gen_overrides=reason_overrides,
                situation_brief=situation_brief,
            )
        log.debug(
            f"[REASONING][CoT] private scratchpad ({len(private_reasoning)} chars, "
            f"max_tok={max_tok_reason}, temp={temp_reason:.2f})"
        )

        # Stage 2: final answer informed by private reasoning
        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("chain_of_thought", 2, 2, "final_synthesis")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        final_prompt = (
            "Using your internal reasoning below, write ONLY the final answer to the original request. "
            "Do NOT reproduce your reasoning steps or numbered list. "
            "Do NOT include any preamble like 'Based on my reasoning', 'After thinking through this', "
            "'In conclusion', or any meta-commentary about the process. "
            "CRITICAL — GROUNDING RULE: Do NOT invent specific facts (timestamps, times, dates, "
            "names, file paths, or values) that are not explicitly present in the provided context "
            "or the user's own words. If a specific fact is not in the context, say you don't have "
            "that information rather than guessing or approximating. "
            "DELIVER SUBSTANCE: give the ACTUAL content — facts, mechanism, analysis. NEVER "
            "substitute a description of how you would answer ('let's delve into', 'we can "
            "explore approaches', 'I'd be happy to discuss') for the answer itself; those are "
            "forbidden non-answers. If grounded detail is thin, give the best substantive answer "
            "from your own knowledge and flag what's uncertain. "
            "Write in natural prose as if this is your complete, direct response.\n\n"
            f"INTERNAL REASONING (private — do not quote back):\n{private_reasoning}\n\n"
            f"ORIGINAL REQUEST: {user_input}"
        )
        final_overrides = dict(gen_overrides or {})
        final_overrides["temperature"] = temp_final
        final_overrides["max_tokens"]  = max_tok_final
        # Stage 2 already has the scratchpad — it must synthesise, not re-open a
        # second <think> block that truncates to empty. Same closed-think prefill.
        try:
            from eli.cognition.gguf_inference import force_no_think as _fnt2
        except Exception:
            from contextlib import nullcontext as _fnt2  # type: ignore
        with _fnt2():
            final = self._get_chat_response(
                final_prompt, working_context,
                reasoning_mode="chain_of_thought", gen_overrides=final_overrides,
                situation_brief=situation_brief,
            )
        final = _strip_reasoning_scaffold(final)
        log.debug(
            f"[REASONING][CoT] final answer ({len(final)} chars, "
            f"max_tok={max_tok_final}, temp={temp_final:.2f})"
        )
        return final

    def _run_constitutional_ai(self, user_input: str, working_context: str,
                               gen_overrides: Dict[str, Any], situation_brief: str) -> str:
        """Generate → critique against principles → revise.

        Per-stage budgets come from `mode_presets["constitutional_ai"]`.
        """
        preset = self._mode_profile("constitutional_ai")
        max_tok_gen = int(preset.get("max_tokens_generate", 1500))
        max_tok_crit = int(preset.get("max_tokens_critique", 500))
        max_tok_rev = int(preset.get("max_tokens_revise", 1500))
        gen_temp = float(preset.get("temperature", 0.3))

        # Stage 1: initial response
        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("constitutional_ai", 1, 3, "initial_draft")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        gen_overrides_1 = dict(gen_overrides or {})
        gen_overrides_1["max_tokens"] = max_tok_gen
        gen_overrides_1["temperature"] = gen_temp
        initial = self._get_chat_response(
            user_input, working_context,
            reasoning_mode="constitutional_ai", gen_overrides=gen_overrides_1,
            situation_brief=situation_brief,
        )
        log.debug(f"[REASONING][Constitutional] initial draft ({len(initial)} chars, max_tok={max_tok_gen})")

        # Stage 2: critique against principles
        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("constitutional_ai", 2, 3, "principle_critique")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # Grounded trust: when the engine already thinks this turn is well grounded, the weak local
        # critic must not invent factual problems and delete a correct answer (a stored name was once
        # nuked to "[no memories found]"). A prompt instruction here and a post-filter on the issue
        # list below.
        try:
            _grounding_conf = float(getattr(self, "_current_grounding_confidence", 0.0) or 0.0)
        except Exception:
            _grounding_conf = 0.0
        _GROUNDED_TRUST_FLOOR = 0.6
        _grounded_clause = (
            "ELI's own grounded facts (capability count, local model name, architecture, runtime "
            "config, and the memory/session counts shown in context) are TRUE — do NOT raise "
            "accuracy or unsupported-claim problems about them.\n"
            if _grounding_conf >= _GROUNDED_TRUST_FLOOR else ""
        )
        critique_prompt = (
            "You are reviewing a DRAFT answer. Find the CONCRETE problems that must be fixed, "
            "judged against these principles:\n"
            "  P1 Factual accuracy · P2 No unsupported claims · P3 Completeness (it answers what "
            "was actually asked) · P4 Honesty about uncertainty · P5 No harm.\n"
            + _grounded_clause +
            "Output a NUMBERED list of specific, actionable problems. For EACH: name the exact "
            "offending part of the draft, which principle it breaks, and the concrete fix to make. "
            "Be specific — no vague 'could be clearer'. Do NOT rewrite the draft here. "
            "If the draft already satisfies every principle, output EXACTLY: NO ISSUES\n\n"
            f"REQUEST: {user_input}\n\nDRAFT:\n{initial}"
        )
        critique_overrides = dict(gen_overrides or {})
        critique_overrides["temperature"] = max(0.1, gen_temp - 0.1)
        critique_overrides["max_tokens"] = max_tok_crit
        critique = self._get_chat_response(
            critique_prompt, working_context,
            reasoning_mode="constitutional_ai", gen_overrides=critique_overrides,
            situation_brief=situation_brief,
        )
        log.debug(f"[REASONING][Constitutional] critique ({len(critique)} chars, max_tok={max_tok_crit})")

        # Stage 3: revise — but only if the critique found CONCRETE issues. This replaces the
        # gamed P1-P5 PASS/FAIL parse: the 7B is far more reliable producing specific problems
        # than emitting consistent PASS/FAIL tokens (it used to dupe/contradict them).
        crit_text = str(critique or "").strip()
        # Extract numbered / bulleted concrete issues.
        _issues = [l.strip() for l in crit_text.splitlines()
                   if re.match(r"^\s*(?:\d+[\.\)]|[-•*])\s+\S", l)]
        # Explicit "NO ISSUES" sentinel (and no numbered issues) = the draft is sound.
        _no_issues = bool(re.search(r"\bno\s+issues?\b", crit_text, re.I)) and not _issues
        # Grounded-trust post-filter: when the turn is well-grounded, drop any issue that is
        # about factual accuracy / unsupported claims, so the weak critic cannot delete a
        # grounded answer (belt-and-braces with the prompt clause above).
        if _grounding_conf >= _GROUNDED_TRUST_FLOOR and _issues:
            _kept = [l for l in _issues if not re.search(
                r"\b(p1|p2|factual|inaccurat|unsupported|not\s+(?:in|supported)|no\s+evidence|"
                r"fabricat|hallucinat|made\s+up|cannot\s+verify)\b", l, re.I)]
            if len(_kept) != len(_issues):
                log.debug(f"[REASONING][Constitutional] grounding={_grounding_conf:.2f} → "
                          f"dropped {len(_issues) - len(_kept)} grounding-accuracy issue(s)")
            _issues = _kept
        if _no_issues or not _issues:
            log.debug("[REASONING][Constitutional] no actionable issues → returning draft")
            return _strip_reasoning_scaffold(initial)
        _mandatory_block = (
            "\n\nISSUES TO FIX (address every one — keep all correct content):\n"
            + "\n".join(f"  {l}" for l in _issues[:8])
            + "\n  Fix each issue concretely. Do NOT delete a claim the draft can defend — "
            "qualify it ('typically', 'as configured', 'by design') instead of removing it."
        )
        try:
            from eli.world.world_event_bus import fire_reasoning_stage_event as _frs
            _frs("constitutional_ai", 3, 3, "revision_and_finalize")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        revise_prompt = (
            "Revise the draft to address the critique. Output ONLY the revised response — "
            "do not narrate the revision, do not restate the critique. "
            "Do not output a question, the request, a title, or the critique. "
            "For identity/self-report questions, answer in first person as ELI. "
            "Do not swap ELI's identity with the user's identity.\n"
            "DELIVER SUBSTANCE: the revision must contain the ACTUAL answer (facts, mechanism, "
            "analysis) — never replace content with a description of how you would answer "
            "('let's delve into', 'we can explore approaches', 'I'd be happy to discuss'); those "
            "are forbidden non-answers.\n"
            "IMPORTANT: preserve all substantive content from the original draft. "
            "For grounding failures (P1/P2), add hedging language such as 'by design', "
            "'as configured', or 'typically' rather than deleting the claim. "
            "The revised response must be at least as detailed as the original draft."
            f"{_mandatory_block}\n\n"
            f"REQUEST: {user_input}\n\nORIGINAL DRAFT:\n{initial}\n\nCRITIQUE:\n{critique}"
        )
        revise_overrides = dict(gen_overrides or {})
        revise_overrides["max_tokens"] = max_tok_rev
        # Lower revision temperature so the model doesn't just regenerate the same answer
        revise_overrides["temperature"] = max(0.1, gen_temp - 0.15)
        final = self._get_chat_response(
            revise_prompt, working_context,
            reasoning_mode="constitutional_ai", gen_overrides=revise_overrides,
            situation_brief=situation_brief,
        )
        log.debug(f"[REASONING][Constitutional] revised final ({len(final)} chars, max_tok={max_tok_rev})")
        final_text = str(final or "").strip()
        final_low = final_text.lower()
        request_low = str(user_input or "").strip().lower()
        norm_request = re.sub(r"\W+", " ", request_low).strip()
        norm_final = re.sub(r"\W+", " ", final_low).strip()
        identity_request = _eli_identity_self_report_request(user_input)
        bad_final = False
        if not final_text:
            bad_final = True
        elif final_text.endswith("?") and re.match(
            r"(?is)^\s*(who|what|when|where|why|how|do|does|did|is|are|can|could|should|would)\b",
            final_text,
        ):
            bad_final = True
        elif norm_request and norm_final == norm_request:
            bad_final = True
        elif identity_request and (
            re.search(r"\byour (?:identity|persona)\b", final_low)
            or final_low.startswith(("who are you", "what are you based"))
            or _eli_bad_identity_self_report_output(user_input, final_text)
        ):
            bad_final = True
        elif re.search(r"^\s*P[1-5]\s*:?\s*(PASS|FAIL)\b", final_text, re.MULTILINE | re.I):
            # Critique transcript leaked into revision output — model included P-line
            # evaluation instead of just writing the revised answer. Reject and use
            # the initial draft.
            log.debug("[REASONING][Constitutional] revision contained P1-P5 critique lines — leaked critique rejected")
            bad_final = True
        if bad_final:
            log.debug("[REASONING][Constitutional] revised final rejected; returning initial draft")
            return _strip_reasoning_scaffold(initial)
        return _strip_reasoning_scaffold(final)

    def _run_chat_reasoning_loop(self, user_input: str, memory_context: str, intent: Dict[str, Any], reasoning_mode: Optional[
                                 str], trace: Optional[Dict[str, Any]] = None, gen_overrides: Optional[Dict[str, Any]] = None,
                                 situation_brief: str = "") -> Dict[str, Any]:
        """Private reasoning modes bypass the streaming path, so they need their own
        anti-repeat/anti-echo pass: inject the contract, run the loop, strip any
        opening that restates ELI's own prior reply or the user's own message."""
        brief = self._inject_anti_repeat_contract(user_input, situation_brief, recent_turns=None)
        result = self._run_chat_reasoning_loop_inner(
            user_input, memory_context, intent, reasoning_mode,
            trace=trace, gen_overrides=gen_overrides, situation_brief=brief,
        )
        response = str((result or {}).get("response") or "")
        if not response:
            return result
        try:
            _recent_eli: list = []
            _recent_user: list = []
            for _t in (self.memory.get_recent_conversation(limit=16) or []):
                _role = str((_t or {}).get("role", "")).lower()
                _c = str((_t or {}).get("content", "") or "").strip()
                if not _c:
                    continue
                if _role == "user":
                    _recent_user.append(_c)
                elif _role in ("assistant", "eli"):
                    _recent_eli.append(_c)
            echo_sources = [user_input] + _recent_user
            if (_opens_by_echoing(response, echo_sources)
                    or (_recent_eli and _is_repeat_of_recent(response, _recent_eli))):
                novel = _strip_repeated_opening(response, _recent_eli + echo_sources)
                if len(_clarifier_norm(novel)) >= _REPEAT_MIN_SENTENCE_CHARS:
                    result = dict(result)
                    result["response"] = novel
        except Exception:
            log.debug("[ANTI-REPEAT] reasoning-loop echo guard skipped", exc_info=True)
        return result

    def _run_chat_reasoning_loop_inner(self, user_input: str, memory_context: str, intent: Dict[str, Any], reasoning_mode: Optional[
                                 str], trace: Optional[Dict[str, Any]] = None, gen_overrides: Optional[Dict[str, Any]] = None,
                                 situation_brief: str = "") -> Dict[str, Any]:
        try:
            from eli.cognition.reasoning_modes import canonical_mode as _eli_loop_mode
            _loop_mode = _eli_loop_mode(reasoning_mode)
        except Exception:
            _loop_mode = "quick" if not reasoning_mode else str(reasoning_mode)
        smalltalk = self._quick_smalltalk_response(user_input) if _loop_mode == "quick" else None
        if smalltalk:

            return {'response': smalltalk, 'score': 0.92, 'threshold': 0.35,
                'evidence': {'used': False}, 'clarified': False}
        profile = self._mode_profile(reasoning_mode)
        passes = int(profile['passes'])
        threshold = float(profile['threshold'])
        gen_overrides = {
            'max_tokens': int(profile['max_tokens']),
            'temperature': float(profile.get('temperature', 0.7)),
            'top_p': float(profile.get('top_p', 0.9)),
        }
        try:
            _depth_overrides = self._chat_generation_overrides(
                user_input,
                memory_context,
                reasoning_mode=reasoning_mode,
            )
            if isinstance(_depth_overrides, dict):
                for _k, _v in _depth_overrides.items():
                    if _v is not None:
                        gen_overrides[_k] = _v
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            _mode_contract = dict(getattr(self, "_last_mode_execution_contract", {}) or {})
        except Exception:
            _mode_contract = {}
        if trace is not None and _mode_contract:
            trace["mode_execution_contract"] = _mode_contract
            try:
                _rt = _mode_contract.get("runtime") or {}
                self._trace_phase(
                    trace,
                    "mode_contract",
                    mode=str(_mode_contract.get("mode") or profile.get("mode") or reasoning_mode or "quick"),
                    task_count=len(_mode_contract.get("tasks") or []),
                    target_max_tokens=int(((_mode_contract.get("generation_overrides") or {}).get("max_tokens") or gen_overrides.get("max_tokens") or 0)),
                    target_n_ctx=int(_rt.get("target_n_ctx") or _rt.get("n_ctx") or 0),
                    target_n_batch=int(_rt.get("target_n_batch") or _rt.get("n_batch") or 0),
                    target_gpu_layers=int(_rt.get("target_n_gpu_layers") or _rt.get("n_gpu_layers") or 0),
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        intent_conf = float((intent or {}).get('confidence') or 0.6)
        requires_grounding = self._intent_requires_grounding(
            intent, user_input)
        words = len((user_input or '').split())
        if not requires_grounding:
            if words <= 3 and not memory_context:
                threshold = min(threshold, 0.35)
                passes = 1
            else:
                threshold = min(
    threshold, 0.56 if str(
        reasoning_mode or 'quick').strip().lower() == 'quick' else 0.66)
        evidence = self._gather_executor_evidence(
            user_input, intent, trace=trace)
        mode_label = str(reasoning_mode or "quick")
        working_context = f"[Private reasoning strategy: {mode_label}; final answer only]\n{memory_context}"
        if evidence.get('used') and evidence.get('content'):
            working_context = (
    working_context +
    "\n\nDeterministic executor evidence:\n" +
     evidence['content']).strip()
        # Short-circuit: COMMAND with deterministic executor result skips LLM
        _intent_action = str((intent or {}).get('action', '')).upper()
        if _intent_action in {
            'LIST_DIR','SHELL_EXEC','GET_WEATHER','TIME','DATE',
            'SET_CLIPBOARD','GET_CLIPBOARD','OPEN_APP','OPEN_URL',
        } and evidence.get('used') and evidence.get('content'):
            return self._finalize_chat_result(
                user_input=user_input,
                response=evidence['content'],
                trace=trace,
                score=1.0,
                threshold=None,
                clarified=False,
                evidence_used=True,
                reasoning_mode=reasoning_mode,
            )
        if trace is not None:
            self._trace_phase(
    trace,
    'loop_start',
    mode=profile['mode'],
    passes=passes,
     threshold=threshold)
        best_answer = ''
        best_score = 0.0

        # Algorithmic-mode dispatch: ToT / Constitutional / Self-Consistency run a multi-stage LLM
        # pipeline (propose-and-develop, generate-critique-revise, sample-and-select) instead of one
        # call N times. The pass-loop below is still a confidence safety net if the score is too low.
        _mode_str = str(profile.get('mode') or reasoning_mode or '').strip().lower()
        # Expose this turn's grounding confidence to the per-mode algorithms
        # (read by the constitutional grounded-trust override, #3b/Option C).
        try:
            self._current_grounding_confidence = float((trace or {}).get('grounding_confidence') or 0.0)
        except Exception:
            self._current_grounding_confidence = 0.0
        if self._supports_mode_algorithm(_mode_str) and not _eli_test_mode():
            try:
                algo_resp = self._run_mode_algorithm(
                    _mode_str, user_input, working_context, gen_overrides, situation_brief
                )
            except Exception as exc:
                log.debug(f"[REASONING][{_mode_str}] algorithm failed: {exc} — falling through to pass-loop")
                algo_resp = None
            if algo_resp:
                algo_score = self._score_response_confidence(
                    user_input, algo_resp, working_context, intent_conf, evidence
                )
                if _mode_str == "self_consistency":
                    # Fold in how much the samples actually agreed — otherwise a
                    # forced synthesis of contradictory samples scores identically
                    # to genuine consensus.
                    _sc_ratio = getattr(self, "_last_sc_agreement_ratio", None)
                    if _sc_ratio is not None:
                        algo_score = max(0.0, algo_score - (1.0 - _sc_ratio) * 0.35)
                log.debug(f"[REASONING][{_mode_str}] final score={algo_score:.2f} threshold={threshold:.2f}")
                if trace is not None:
                    trace.setdefault('confidence', []).append(
                        {'pass_no': 'algorithm', 'score': algo_score, 'threshold': threshold})
                if algo_score >= threshold:
                    return {'response': algo_resp, 'score': algo_score,
                            'threshold': threshold, 'evidence': evidence, 'clarified': False}
                # Below threshold — seed the pass-loop with the algorithmic
                # output as best-so-far so retries refine from it.
                best_answer = algo_resp
                best_score = algo_score

        for pass_no in range(1, passes + 1):
            if trace is not None:
                self._trace_phase(
    trace,
    'analysis',
    pass_no=pass_no,
    context_chars=len(working_context),
     intent_conf=intent_conf)
            started = time.perf_counter()
            response = self._get_chat_response(
    user_input,
    working_context,
    reasoning_mode=reasoning_mode,
    gen_overrides=gen_overrides,
    situation_brief=situation_brief)
            elapsed = time.perf_counter() - started
            log.debug(f'[COGNITIVE][TIMING] chat_pass_{pass_no}={elapsed:.3f}s')
            # Govern here, once, before scoring and before any of the four return paths. The streaming path
            # is governed at its own choke point, but this loop wasn't and it serves about a third of live
            # turns. Can't go inside _get_chat_response, it also makes private reasoning. Score the governed
            # text so confidence describes what the user actually gets.
            try:
                response = govern_output(
                    response,
                    is_grounded=bool(evidence),
                    evidence=evidence if isinstance(evidence, str) else None,
                    memory_diag=getattr(self, "_memory_diag", None),
                )
            except Exception:
                log.debug("[COGNITIVE] output governor skipped", exc_info=True)
            score = self._score_response_confidence(
    user_input, response, working_context, intent_conf, evidence)
            log.debug(
                f'[COGNITIVE][FINAL] confidence pass_no={pass_no} score={score:.2f} threshold={threshold:.2f}')
            if trace is not None:
                trace.setdefault('confidence', []).append(
                    {'pass_no': pass_no, 'score': score, 'threshold': threshold})
            if score > best_score:
                best_answer = response
                best_score = score
            if score >= threshold:

                return {'response': response, 'score': score,
                    'threshold': threshold, 'evidence': evidence, 'clarified': False}
            if pass_no < passes:
                working_context = (
    working_context +
     "\n\nRevision directive:\n- Previous candidate was below confidence threshold.\n"
     "- If you are genuinely uncertain, say so plainly rather than guessing — do not "
     "remove honest hedging just to sound more certain; that is worse than the "
     "original answer, not better.\n"
     "- Use only grounded facts from context and executor evidence.\n"
     "- If exact file paths or line numbers are required, include them explicitly.\n").strip()
                log.debug(
                    f'[COGNITIVE][FINAL] retry next_pass={pass_no + 1} reason=below_threshold')
        if best_score < threshold and profile.get(
            'clarify', True):
            # ELI_GROUNDED_CONTROL_NO_CLARIFY_CUTPOINT_V2
            _eli_gc_action = ""
            try:
                if isinstance(intent, dict):
                    _eli_gc_action = str(intent.get("action") or "").upper()
            except Exception:
                _eli_gc_action = ""

            from eli.contracts.grounded_control import is_grounded_control_action

            if is_grounded_control_action(_eli_gc_action):
                log.debug(
                    f"[COGNITIVE][FINAL] grounded-control no-clarify v2 suppressed "
                    f"action={_eli_gc_action} score={best_score:.2f} threshold={threshold:.2f}",
                )
                return {
                    'response': best_answer,
                    'score': best_score,
                    'threshold': threshold,
                    'evidence': evidence,
                    'clarified': False,
                    'clarify_suppressed': True,
                }

            log.debug(
                f'[COGNITIVE][FINAL] clarify score={best_score:.2f} threshold={threshold:.2f}'
            )

            return {'response': self._clarifying_response(
                user_input,
                best_score,
                threshold,
                memory_context=working_context,
                evidence=evidence,
                reasoning_mode=reasoning_mode,
            ), 'score': best_score, 'threshold': threshold, 'evidence': evidence, 'clarified': True}

        return {'response': best_answer, 'score': best_score,
            'threshold': threshold, 'evidence': evidence, 'clarified': False}

    # ─────────────────────────────────────────────────────────────────────────
    # Reasoning-mode algorithms.
    #
    # All four private modes run distinct multi-stage GGUF pipelines:
    #   - chain_of_thought:  private scratchpad reasoning → final clean answer
    #   - tree_of_thoughts:  propose K candidate approaches → score → develop best
    #   - constitutional_ai: generate → critique against principles → revise
    #   - self_consistency:  N samples → LLM picks the most consistent
    #   - quick:             single call, no overhead (pass-loop only)
    # All four private modes go through _run_mode_algorithm; quick routes
    # through the standard pass-loop above. All paths still feed the
    # confidence-threshold retry as a safety net.
    # ─────────────────────────────────────────────────────────────────────────

    def _synthesize_answer(self, evidence: str, query: str,
                           reasoning_mode=None,
                           max_tokens_override: Optional[int] = None,
                           action: Optional[str] = None) -> str:
        # Don't call GGUF when the executor already reported failure — it would
        # hallucinate success.  Surface a grounded failure message instead.
        if _failed_executor_is_failed(evidence, action=action or ""):
            return _failed_executor_surface(evidence, query, action=action or "")

        q = (query or "").strip().lower()
        ev = str(evidence or "").strip()
        try:
            from eli.cognition.reasoning_modes import canonical_mode as _eli_syn_mode
            _syn_mode = _eli_syn_mode(reasoning_mode)
        except Exception:
            _syn_mode = str(reasoning_mode or "quick").strip().lower() or "quick"
        _nonquick_depth = _syn_mode != "quick"

        if _eli_test_mode():
            pass
            if "remember that" in q:
                return ""
            if "what do you know about me" in q:
                return ev[:1200] if ev else "I found stored memories."
            if q.startswith("list "):
                return ev[:1200] if ev else "Directory listed."

        persona = self.get_persona()
        # Cap evidence to prevent context overflow. Persona ~2k chars +
        # instructions ~400 chars + query ~200 chars leaves a budget.
        # At n_ctx=16384, 3.5 chars/token, ~60% usable = ~34k chars total.
        _ev_max_chars = 10000
        if len(ev) > _ev_max_chars:
            ev = ev[-_ev_max_chars:]
            nl = ev.find("\n")
            if nl > 0:
                ev = ev[nl + 1:]
        _depth_instruction = (
            "For non-quick reasoning modes, provide a thorough, in-depth answer with concrete grounded detail and clear structure. "
            if _nonquick_depth else
            "For simple command results, answer in one short sentence. "
        )
        prompt = (
            f"{persona}\n\n"
            "INSTRUCTIONS: The data below was gathered by your internal agents. "
            "Answer the user's current request only. "
            "Write a clear, natural, first-person answer using ONLY this data. "
            "Do not output raw JSON, bracket labels, or agent metadata. "
            "Do not mention previous turns, memories, files, paths, or unrelated context unless the user explicitly asked for them. "
            f"{_depth_instruction}"
            "For failed commands, state the failure and the specific reason only. "
            "Include exact file paths only when the user explicitly asks about runtime, memory, files, databases, or paths. "
            "Speak as yourself — same personality, same tone as your regular chat responses.\n\n"
            f"AGENT DATA:\n{ev}\n\n"
            f"USER QUESTION:\n{query}\n\n"
            "YOUR ANSWER:"
        )
        try:
            from eli.runtime.final_response_provider import (
                apply_generation_kwargs as _eli_apply_response_kwargs,
                clear_current_action as _eli_clear_current_action,
                decorate_prompt as _eli_decorate_prompt,
                set_current_action as _eli_set_current_action,
            )
            _contract = _eli_set_current_action(action or "CHAT")
            prompt = _eli_decorate_prompt(prompt, _contract)
            _grounded_short = any(x in q for x in (
                "runtime", "gpu layers", "context size", "model", "previous response",
                "last response", "which agents contributed", "confidence in your last response",
            ))
            _max_tokens = int(max_tokens_override) if (max_tokens_override is not None and max_tokens_override != 0) else -1
            if _nonquick_depth and _max_tokens == -1:
                _max_tokens = 1800
            # A grounded factual answer (web/news lookup) is a few sentences, not an essay. In quick mode
            # max_tokens=-1 means "fill the rest of the context" (~8.8k tokens), which slows the reply and
            # starves the prompt budget so the system prompt and grounding get truncated. Cap it so the
            # evidence fits. Non-quick keeps its larger 1800.
            if _max_tokens == -1 and str(action or "").upper() in ("WEB_SEARCH", "NEWS_FETCH"):
                _max_tokens = 700
            _gen_kwargs = _eli_apply_response_kwargs(
                {"max_tokens": _max_tokens, "temperature": 0.35},
                _contract,
            )
            response = self._get_chat_response(
                prompt,
                gen_overrides=_gen_kwargs,
            )
            _eli_clear_current_action()
            if response and response.strip():
                return response.strip()
        except Exception as e:
            try:
                _eli_clear_current_action()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            log.debug(f"[COGNITIVE] _synthesize_answer LLM call failed: {e}")
        return ""

    def _self_consistency_majority(self, samples: list) -> Optional[str]:
        """True self-consistency vote: if a STRICT majority of the independent samples
        normalise to the same answer, return the fullest original sample in that group;
        else None (caller falls back to LLM consensus-synthesis). Meaningful for short
        factual/value answers — long divergent prose rarely exact-matches and returns None.

        Side effect: records the agreement ratio (``self._last_sc_agreement_ratio``,
        top-answer share of valid samples) so the caller can lower its confidence
        score when samples disagreed instead of discarding that signal."""
        import re as _re
        from collections import Counter

        def _norm(s: str) -> str:
            t = _strip_reasoning_scaffold(str(s or "")).strip().lower()
            t = _re.sub(r"[^a-z0-9 ]+", " ", t)
            return _re.sub(r"\s+", " ", t).strip()

        valid = [s for s in samples if s and len(str(s).strip()) >= 2]
        if len(valid) < 3:
            self._last_sc_agreement_ratio = None
            return None
        norms = [_norm(s) for s in valid]
        counts = Counter(n for n in norms if n)
        if not counts:
            self._last_sc_agreement_ratio = 0.0
            return None
        top_norm, top_count = counts.most_common(1)[0]
        self._last_sc_agreement_ratio = top_count / len(valid)
        # Strict majority, and the agreed answer is short enough that exact-match is a real
        # signal (a fact/value), not coincidental overlap of long prose.
        if top_count > len(valid) / 2 and len(top_norm) <= 400:
            winners = [s for s, nm in zip(valid, norms) if nm == top_norm]
            return max(winners, key=lambda s: len(str(s).strip()))
        return None
