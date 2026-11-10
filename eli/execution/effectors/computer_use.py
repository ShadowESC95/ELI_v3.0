"""Computer-use effector — the closed observe → decide → act → verify loop.

ELI already had every primitive this needs: SCREEN_READ_ANALYZE (vision + OCR),
SCREEN_LOCATE (find a thing and get its coordinates), and os_controller's mouse,
keyboard and clipboard. What was missing was the *loop* that binds them, so a goal
like "open the settings page and turn on dark mode" could only ever be answered with
a single screenshot and a description of it.

Three properties matter more than step count here:

1. **Every step is verified by re-observation.** The loop never reports having done
   something because it decided to do it; it re-observes and says what actually
   changed. That is the no-fake-actions rule applied to physical control, where the
   cost of confabulation is highest — a claimed click that never landed leaves the
   user believing their machine is in a state it is not.
2. **The step vocabulary is closed.** The decision call is grammar-constrained to the
   verbs below, so the model cannot invent a capability mid-loop for the same reason
   it cannot invent an action in the intent resolver.
3. **It is bounded and gated.** Step budget, wall-clock budget, and a no-progress
   detector, with every step passed through the authority gate first. An agent that
   drives the real mouse must be interruptible and must stop when it is stuck rather
   than thrash.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

# Closed step vocabulary. Kept deliberately small: these are the primitives that
# already exist and are already gated, not a new control surface.
_STEPS = ("LOCATE_AND_CLICK", "TYPE_TEXT", "PRESS_KEY", "WAIT", "DONE", "ABORT")

_DEFAULT_MAX_STEPS = 12
_DEFAULT_TIME_BUDGET_S = 180.0
_NO_PROGRESS_LIMIT = 3

_GRAMMAR_CACHE: Dict[str, Any] = {}


def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)


def _step_grammar():
    """GBNF admitting only {"step": <one of _STEPS>, "target": str, "text": str,
    "reason": str}. None when the backend has no grammar support, in which case the
    free-text path below still parses and validates the same shape."""
    key = hashlib.sha1("|".join(_STEPS).encode()).hexdigest()
    if key in _GRAMMAR_CACHE:
        return _GRAMMAR_CACHE[key]
    grammar = None
    try:
        from llama_cpp import LlamaGrammar
        alts = " | ".join('"\\"%s\\""' % s for s in _STEPS)
        gbnf = (
            'root   ::= "{" ws "\\"step\\":" ws step ws "," ws "\\"target\\":" ws string'
            ' ws "," ws "\\"text\\":" ws string ws "," ws "\\"reason\\":" ws string ws "}"\n'
            'step   ::= ' + alts + '\n'
            'string ::= "\\"" ([^"\\\\] | "\\\\" ["\\\\/bfnrt])* "\\""\n'
            'ws     ::= [ \\t\\n]*\n'
        )
        grammar = LlamaGrammar.from_string(gbnf, verbose=False)
    except Exception:
        log.debug("computer_use: grammar unavailable — using free-text JSON", exc_info=True)
        grammar = None
    _GRAMMAR_CACHE[key] = grammar
    return grammar


def _observe() -> Dict[str, Any]:
    """One honest look at the screen. Returns the description plus a cheap digest used
    to tell whether anything actually changed between steps."""
    res = _execute_impl("SCREEN_READ_ANALYZE", {"prompt": (
        "Describe the current screen precisely: the focused window, visible controls, "
        "buttons, fields and any text or error. Do not speculate about what is not visible."
    )})
    text = str(res.get("content") or res.get("response") or "").strip()
    ocr = str(res.get("ocr_text") or "")
    digest = hashlib.sha1((text + "\x00" + ocr).encode("utf-8", "replace")).hexdigest()
    return {"ok": bool(res.get("ok", True)), "text": text, "ocr": ocr, "digest": digest,
            "screenshot_path": res.get("screenshot_path", "")}


def _decide(goal: str, observation: str, history: List[str]) -> Dict[str, Any]:
    """Choose exactly one next step. Grammar-constrained where supported."""
    from eli.cognition import gguf_inference

    system = (
        "You are ELI operating the user's computer. Choose the SINGLE next step toward "
        "the goal, based only on what is actually visible on screen. "
        "Steps: LOCATE_AND_CLICK (target = the on-screen thing to click), "
        "TYPE_TEXT (text = what to type), PRESS_KEY (target = a key like Return or Tab), "
        "WAIT (let the screen settle), DONE (goal is visibly achieved), "
        "ABORT (goal cannot be achieved from here). "
        "Answer DONE only if the screen shows the goal is already complete. "
        'Output ONE JSON object: {"step":...,"target":...,"text":...,"reason":...}.'
    )
    prompt = (
        f"GOAL: {goal}\n\n"
        f"WHAT IS ON SCREEN NOW:\n{observation[:2000]}\n\n"
        + ("STEPS ALREADY TAKEN:\n" + "\n".join(history[-8:]) + "\n\n" if history else "")
        + "Next step:"
    )

    grammar = _step_grammar()
    response = None
    if grammar is not None:
        try:
            response = gguf_inference.chat_completion(
                prompt, system=system, max_tokens=200, temperature=0.1, grammar=grammar)
        except TypeError:
            response = None  # backend does not accept a grammar kwarg
        except Exception:
            log.debug("computer_use: grammar decode failed — falling back", exc_info=True)
            response = None
    if response is None:
        try:
            response = gguf_inference.chat_completion(
                prompt, system=system, max_tokens=200, temperature=0.1)
        except Exception as e:
            log.debug("computer_use: decision call failed", exc_info=True)
            return {"step": "ABORT", "target": "", "text": "",
                    "reason": f"could not reach the model ({e})"}

    cleaned = re.sub(r"```(?:json)?|```", " ", str(response or ""))
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return {"step": "ABORT", "target": "", "text": "",
                "reason": "the model did not return a usable step"}
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return {"step": "ABORT", "target": "", "text": "",
                "reason": "the model's step was not valid JSON"}

    step = str(parsed.get("step") or "").strip().upper()
    if step not in _STEPS:
        step = "ABORT"
    return {"step": step,
            "target": str(parsed.get("target") or "").strip(),
            "text": str(parsed.get("text") or ""),
            "reason": str(parsed.get("reason") or "").strip()}


def _gate(action: str, args: Dict[str, Any]) -> Optional[str]:
    """Returns a refusal reason, or None when the step is permitted."""
    try:
        from eli.runtime import authority_gate
        verdict = authority_gate.check(action, args) or {}
        if verdict.get("allowed") is False:
            return str(verdict.get("reason") or "the authority gate declined this step")
    except Exception:
        log.debug("computer_use: authority gate unavailable", exc_info=True)
    return None


def _act(step: Dict[str, Any]) -> Dict[str, Any]:
    """Perform one step through the existing, already-gated primitives."""
    kind = step["step"]
    if kind == "WAIT":
        time.sleep(1.0)
        return {"ok": True, "detail": "waited for the screen to settle"}

    if kind == "LOCATE_AND_CLICK":
        target = step["target"]
        if not target:
            return {"ok": False, "detail": "no target given to click"}
        refusal = _gate("SCREEN_LOCATE", {"query": target, "click": True})
        if refusal:
            return {"ok": False, "detail": refusal}
        res = _execute_impl("SCREEN_LOCATE", {"query": target, "click": True})
        # SCREEN_LOCATE reports found-but-not-clicked distinctly; do not upgrade that
        # to success — a click that did not land is the exact failure worth surfacing.
        clicked = bool(res.get("clicked"))
        return {"ok": clicked,
                "detail": str(res.get("content") or res.get("response") or "").strip()}

    if kind == "TYPE_TEXT":
        text = step["text"]
        if not text:
            return {"ok": False, "detail": "no text given to type"}
        refusal = _gate("KEYBOARD", {"text": text})
        if refusal:
            return {"ok": False, "detail": refusal}
        from eli.perception.os_controller import type_text
        res = type_text(text) or {}
        return {"ok": bool(res.get("ok", True)), "detail": f"typed {len(text)} characters"}

    if kind == "PRESS_KEY":
        key = step["target"] or step["text"]
        if not key:
            return {"ok": False, "detail": "no key given to press"}
        refusal = _gate("KEYBOARD", {"key": key})
        if refusal:
            return {"ok": False, "detail": refusal}
        from eli.perception.os_controller import press_key
        res = press_key(key) or {}
        return {"ok": bool(res.get("ok", True)), "detail": f"pressed {key}"}

    return {"ok": False, "detail": f"unsupported step {kind}"}


def _h_computer_use(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    goal = str(args.get("goal") or args.get("task") or args.get("text")
               or args.get("message") or "").strip()
    if not goal:
        msg = "Tell me what you want done on screen, and I'll work through it step by step."
        return {"ok": False, "action": a, "error": "missing goal",
                "content": msg, "response": msg}

    if os.environ.get("ELI_COMPUTER_USE", "1") == "0":
        msg = "Computer control is switched off (ELI_COMPUTER_USE=0), so I won't touch the screen."
        return {"ok": False, "action": a, "content": msg, "response": msg}

    try:
        max_steps = max(1, min(40, int(args.get("max_steps") or _DEFAULT_MAX_STEPS)))
    except Exception:
        max_steps = _DEFAULT_MAX_STEPS
    deadline = time.time() + _DEFAULT_TIME_BUDGET_S

    trace: List[Dict[str, Any]] = []
    history: List[str] = []
    no_progress = 0
    outcome = "budget"

    obs = _observe()
    if not obs["ok"] and not obs["text"]:
        msg = ("I can't see the screen right now, so I won't pretend to drive it. "
               "Check that screenshots and the vision model are working.")
        return {"ok": False, "action": a, "content": msg, "response": msg, "steps": []}

    for step_no in range(1, max_steps + 1):
        if time.time() > deadline:
            outcome = "timeout"
            break

        decision = _decide(goal, obs["text"], history)
        kind = decision["step"]

        if kind == "DONE":
            outcome = "done"
            trace.append({"n": step_no, "step": "DONE", "reason": decision["reason"],
                          "ok": True, "detail": "goal reported complete"})
            break
        if kind == "ABORT":
            outcome = "abort"
            trace.append({"n": step_no, "step": "ABORT", "reason": decision["reason"],
                          "ok": False, "detail": decision["reason"]})
            break

        result = _act(decision)
        before = obs["digest"]
        obs = _observe()
        changed = obs["digest"] != before

        trace.append({"n": step_no, "step": kind, "target": decision["target"],
                      "reason": decision["reason"], "ok": bool(result["ok"]),
                      "detail": result["detail"], "screen_changed": changed})
        history.append(f"{step_no}. {kind} {decision['target']} -> "
                       f"{'ok' if result['ok'] else 'failed'}"
                       f"{'' if changed else ' (screen did not change)'}")

        # A step that neither succeeded nor moved the screen is the signature of being
        # stuck. Stopping and saying so beats burning the budget repeating it.
        if result["ok"] and changed:
            no_progress = 0
        else:
            no_progress += 1
            if no_progress >= _NO_PROGRESS_LIMIT:
                outcome = "stuck"
                break

    done_steps = [t for t in trace if t.get("ok") and t["step"] not in ("DONE", "ABORT")]
    if outcome == "done":
        head = f"Done — {goal}."
    elif outcome == "abort":
        reason = trace[-1]["reason"] if trace else "no route from the current screen"
        head = f"I stopped without finishing: {reason}"
    elif outcome == "stuck":
        head = ("I got stuck — the last few steps didn't change anything on screen, "
                "so I stopped rather than keep clicking.")
    elif outcome == "timeout":
        head = "I ran out of time on this one and stopped where it stands."
    else:
        head = f"I used all {max_steps} steps without finishing."

    lines = [head, ""]
    if trace:
        lines.append(f"What I actually did ({len(done_steps)} step(s) that landed):")
        for t in trace:
            mark = "ok" if t.get("ok") else "failed"
            lines.append(f"  {t['n']}. {t['step']} {t.get('target', '')} — {mark}: {t.get('detail', '')}")
    else:
        lines.append("I didn't take any steps.")
    body = "\n".join(lines).strip()

    return {"ok": outcome == "done", "action": a, "goal": goal, "outcome": outcome,
            "steps": trace, "content": body, "response": body}


from eli.execution.effectors.registry import register as _register

_register("COMPUTER_USE", _h_computer_use, shown="always")   # the actions really taken, as they were
