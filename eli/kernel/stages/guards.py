"""Engine guards — output/evidence guards shared by the engine and its stages.

Four cohesive groups:
  * failed-executor: detect a failed executor result in the evidence and surface
    it honestly instead of letting the model narrate around it;
  * identity self-report: spot an identity self-report request and reject a bad
    self-report output;
  * scaffold strip: remove reasoning scaffolding from generated text;
  * anti-repeat: refuse to serve a reply ELI has already given.

Lifted from engine.py so the engine and the reasoning-mode stage share one
implementation rather than each reaching into the monolith.
"""
from __future__ import annotations

import difflib
import json
import re

def _failed_executor_is_failed(evidence: str, action: str = "") -> bool:
    """Return True if evidence indicates an executor action failed."""
    import re as _re
    ev = str(evidence or "")
    low = ev.lower()
    act = str(action or "").upper().strip()

    actionish = bool(act and act not in {"CHAT", "NONE"})
    if not actionish:
        actionish = any(x in low for x in (
            "'action':", '"action":', "action=", "analyze_pdf",
            "runtime_audit", "execute result", "agent:system", "response_mode",
        ))
    if not actionish:
        return False

    return bool(
        _re.search(r'["\']ok["\']\s*:\s*false\b', low)
        or _re.search(r'["\']ok["\']\s*:\s*False\b', ev)
        or _re.search(r'\bok\s*=\s*false\b', low)
        or _re.search(r'\bok:\s*false\b', low)
        or "successful: 0 | failed:" in low
        or "file not found" in low
        or "filenotfounderror" in low
        or ("traceback" in low and "failed" in low)
    )


def _failed_executor_relevant_block(prompt: str) -> str:
    """Extract only the executor-result lines from a full prompt string.

    Avoids scanning persona notes, memory artefacts, or unrelated action names
    that can appear elsewhere in the prompt.
    """
    import re as _re
    text = str(prompt or "")
    lines = text.splitlines()
    selected = []

    for i, line in enumerate(lines):
        low = line.lower()
        if "execute result" in low and ("'ok': false" in low or '"ok": false' in low):
            selected.append(line.strip())
            for j in range(i + 1, min(len(lines), i + 8)):
                nxt = lines[j].strip()
                nl = nxt.lower()
                if not nxt:
                    continue
                if (
                    "filenotfounderror" in nl or "traceback" in nl
                    or "error" in nl or ".pdf" in nl
                    or ("attempt" in nl and "failure" in nl)
                ):
                    selected.append(nxt)

    if selected:
        return "\n".join(selected)

    # Fallback: grounded_evidence block only.
    m = _re.search(r"<grounded_evidence>\s*(.*?)\s*</grounded_evidence>", text, _re.I | _re.S)
    if m:
        block = m.group(1).strip()
        filtered = [
            s.strip() for s in block.splitlines()
            if any(x in s.lower() for x in (
                "'ok': false", '"ok": false', "execute result",
                "filenotfounderror", "file not found", "error", ".pdf", "analyze_pdf",
            ))
        ]
        return "\n".join(filtered) if filtered else block[:3000]

    m = _re.search(
        r"AGENT DATA:\s*(.*?)(?:\n\nUSER QUESTION:|\n\nYOUR ANSWER:|$)",
        text, _re.I | _re.S,
    )
    if m:
        return m.group(1).strip()[:3000]

    return ""


def _failed_executor_is_failed_block(block: str) -> bool:
    low = str(block or "").lower()
    return (
        "'ok': false" in low or '"ok": false' in low
        or "ok=false" in low or "ok: false" in low
        or "filenotfounderror" in low or "file not found" in low
        or "successful: 0 | failed:" in low or "analyze_pdf failure" in low
    )


def _failed_executor_query_from_prompt(prompt: str) -> str:
    import re as _re
    text = str(prompt or "")
    for pat in (
        r"USER ASKED:\s*(.+?)(?:\n\n|$)",
        r"USER QUESTION:\s*(.+?)(?:\n\n|$)",
        r"USER:\s*(.+?)(?:\n\n|$)",
    ):
        m = _re.search(pat, text, _re.I | _re.S)
        if m:
            return m.group(1).strip()[:1200]
    return ""


def _failed_executor_action_name(block: str, query: str = "") -> str:
    import re as _re
    text = str(block or "")
    for pat in (
        r"'action'\s*:\s*'([^']+)'",
        r'"action"\s*:\s*"([^"]+)"',
        r"\baction\s*=\s*([A-Z_]+)",
        r"\baction:\s*([A-Z_]+)",
    ):
        m = _re.search(pat, text)
        if m:
            return m.group(1).upper().strip()
    if "analyze_pdf" in text.lower() or (".pdf" in query.lower() and any(
        x in query.lower() for x in ("read", "summari", "analyse", "analyze")
    )):
        return "ANALYZE_PDF"
    return "ACTION"


def _failed_executor_paths(block: str, query: str = "") -> list:
    import re as _re
    out: list = []
    for m in _re.finditer(r"(/[^,\n\r`\"']+?\.pdf)\b", f"{block}\n{query}", _re.I):
        p = m.group(1).strip().rstrip(" .;:)]}")
        if p not in out:
            out.append(p)
    return out[:8]


def _failed_executor_errors(block: str) -> list:
    import re as _re
    out: list = []
    for pat in (
        r"'error'\s*:\s*'([^']{1,300})'",
        r'"error"\s*:\s*"([^"]{1,300})"',
        r"(FileNotFoundError:\s*[^\n\r]{1,300})",
        r"(No such file or directory[^\n\r]{0,200})",
        r"(This is attempt\s+\d+\s+for the same\s+[A-Z_]+\s+failure\s+`[^`]+`)",
    ):
        for m in _re.finditer(pat, str(block or "")):
            s = m.group(1).strip()
            if s and s not in out:
                out.append(s)
    return out[:8]


def _failed_executor_surface(evidence: str, query: str = "", action: str = "") -> str:
    """Format a clean failure response without calling GGUF."""
    block = _failed_executor_relevant_block(str(evidence or ""))
    if not block:
        block = str(evidence or "")[:3000]
    act = _failed_executor_action_name(block, query)
    paths = _failed_executor_paths(block, query)
    errors = _failed_executor_errors(block)

    lines = (
        ["I did not successfully analyse the PDF request."]
        if act == "ANALYZE_PDF"
        else [f"I did not successfully complete `{act}`."]
    )
    if errors:
        lines += ["", "What failed:"] + [f"- {e}" for e in errors]
    if paths:
        lines += ["", "Path(s) involved:"] + [f"- `{p}`" for p in paths]
    lines += [
        "",
        "I am not going to claim the document was read or summarised, "
        "because the executor evidence says the action failed.",
    ]
    return "\n".join(lines).strip()


def _eli_identity_self_report_request(text: str) -> bool:
    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not low:
        return False
    return bool(re.search(
        r"\b("
        r"who are you"
        r"|who you are"
        r"|what are you(?:\s|$)"
        r"|do you know who you are"
        r"|tell me about yourself"
        r"|tell me .{0,40}who you are"
        r"|as (?:a |an )?(?:person|entity)"
        r"|your identity"
        r"|your persona"
        r"|persona evolved"
        r"|identity evolved"
        r"|defined with .{0,80}memories"
        r"|how .{0,80}(?:persona|identity).{0,80}(?:evolved|defined)"
        r")\b",
        low,
    ))


def _eli_bad_identity_self_report_output(user_input: str, response: str) -> bool:
    if not _eli_identity_self_report_request(user_input):
        return False
    text = str(response or "").strip()
    if not text:
        return True
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            surface = str(parsed.get("surface") or "").lower()
            ident = parsed.get("identity") if isinstance(parsed.get("identity"), dict) else {}
            sources = ident.get("grounding_sources") if isinstance(ident, dict) else []
            if (
                surface in {"identity_evidence", "identity_runtime_evidence", "self_report_evidence"}
                and str(ident.get("name") or "").lower() == "eli"
                and {"persona", "memory"} <= {str(x).lower() for x in (sources or [])}
            ):
                return False
    except Exception:
        pass
    low = text.lower()
    if re.search(r"\byour (?:identity|persona)\b", low):
        return True
    if text.endswith("?") and re.match(
        r"(?is)^\s*(who|what|when|where|why|how|do|does|did|is|are|can|could|should|would)\b",
        text,
    ):
        return True
    has_eli_first_person = bool(re.search(r"\b(i am|i'm)\s+eli\b|\bmy (?:identity|persona)\b", low))
    settings_heavy = bool(re.search(r"\b(model details|model path|context size|gpu layers|batch size|provider=|ctx=)\b", low))
    if not has_eli_first_person:
        return True
    if settings_heavy and not re.search(r"\bpersona|memory|reflection|identity|local files|runtime state\b", low):
        return True
    return False


def _strip_reasoning_scaffold(text: str) -> str:
    """Strip leftover internal-deliberation prefixes that small LLMs leak into the
    final user-visible answer. Covers role/mode/stage/approach labels at the very
    start of the response. Conservative — only removes the opening label line(s),
    leaves the answer body intact."""
    t = str(text or "").lstrip()
    if not t:
        return t
    # Forbidden opening forms — each pattern only strips when it matches the START
    # of the response, then re-strips leading whitespace and re-checks.
    _scaffold_re = re.compile(
        r"^(?:"
        # Speaker/role label: "As ELI:", "ELI:", "As an AI:", "As a/an X:"
        r"as\s+eli[:,]?\s*|"
        r"eli[:,]\s*|"
        r"as\s+an?\s+(?:ai|assistant|llm|agent)[:,]?\s*|"
        # Mode label: "Quick:", "CoT:", "Constitutional:", "Chain of Thought:"
        r"(?:quick|cot|chain\s+of\s+thought|self[-\s]?consistency|"
        r"tree\s+of\s+thoughts?|constitutional(?:\s+ai)?)[:,]?\s*|"
        # Approach/stage/step preamble: "Approach 3:", "Stage 1:", "Step 2:"
        r"(?:approach|stage|step|option|candidate|path|plan|strategy|selected)"
        r"\s*[0-9]*\s*[:.\-]\s*[^\n]*\n+|"
        # Generic single-line announcement followed by colon-list intro
        r"selected\s+approach[:,]?\s*[^\n]*\n+"
        r")",
        re.IGNORECASE,
    )
    # Apply iteratively until no more leading scaffold matches (handles stacked prefixes).
    for _ in range(4):
        new_t = _scaffold_re.sub("", t, count=1).lstrip()
        if new_t == t:
            break
        t = new_t
    return t


# ── Anti-repeat: verification, not just instruction ──────────────────────────
# The prompt-level contract in the generation stage ("YOU HAVE ALREADY SAID THE
# FOLLOWING — do not repeat any of it") is advisory. In one observed v2 session an
# 8B model ignored it four times, serving the same 40-word paragraph verbatim at
# 13:52, 13:52, 13:55 and 13:56 — including right after the user told it to stop and
# it answered "I'll stop". Asking a model not to repeat itself is not a control;
# checking whether it did is.
#
# Self-contained by necessity: engine.py imports the stages, so reaching back for
# engine._clarifier_norm would close an import cycle.
_REPEAT_HEAD_CHARS = 200      # buffered before first paint
_REPEAT_RATIO = 0.86          # difflib ratio over normalised text
_REPEAT_COMPARE_AGAINST = 4   # how many recent ELI turns to test


def _repeat_norm(text: str) -> str:
    """Punctuation- and case-insensitive form. Apostrophes are deleted rather than
    spaced so "what's" and "whats" collapse together instead of leaving "what s"."""
    lowered = str(text or "").lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", lowered)).strip()


_REPEAT_REQUESTED_RE = re.compile(
    r"\b(say (that|it) again|repeat (that|it|yourself)|come again|again please"
    r"|what did you (just )?say|read (that|it) back|one more time"
    r"|(say|tell) (me )?(that|it) (once )?more)\b",
    re.I,
)


def _user_asked_for_a_repeat(user_input: str) -> bool:
    """True when repeating IS the request.

    Without this the anti-repeat guard becomes its own bug: "say that again" would
    be answered, correctly, with the previous reply — which the guard would then
    reject as a repeat and regenerate into something the user did not ask for.
    """
    return bool(_REPEAT_REQUESTED_RE.search(str(user_input or "")))


# The contract block injected into situation_brief, matched so a retry can remove it.
_ANTI_REPEAT_BLOCK_RE = re.compile(
    r"YOU HAVE ALREADY SAID THE FOLLOWING.*?do not describe it again unless asked\.",
    re.S,
)


def _deprimed_persona_brief(situation_brief: str) -> str:
    """Strip the verbatim prior replies from the PERSONA HANDOFF before regenerating.

    Named for its argument on purpose. The first version took `prompt` and was a
    no-op: in the generation stage `prompt` is the user's own message
    (``prompt = str((args or {}).get("message") or user_input or "").strip()``),
    while the anti-repeat contract is injected into `situation_brief`, which reaches
    the model as ``wm.persona_handoff``. Stripping the user's message removed
    nothing and prepended an instruction to their words instead. The live v2 log
    said so plainly — "prior replies stripped from prompt: 26 -> 143 chars", and 26
    is the length of "fair point pal, fair point".

    Observed live (v2.1.60, 15:13): the guard caught a repeat, retried, and the
    retry produced the SAME opening — prompt_tokens barely moved, 4501 -> 4495,
    because the retry was `instruction + prompt` and `prompt` still carried the
    anti-repeat contract quoting three prior replies at up to 220 chars each.

    So the retry handed the model the exact text it must not produce and asked it
    not to produce it. For a small model that is priming, not prevention: the
    surest way to make it say something is to put it in the context window. The
    remedy is subtraction — remove the quotes, keep a constraint that names no
    content. (A turn earlier the same guard retried successfully, 'You' -> 'Hello';
    the failure is not universal, which is why it needed a live session to find.)
    """
    stripped = _ANTI_REPEAT_BLOCK_RE.sub(
        "Do not repeat anything you have already said in this conversation, and do "
        "not re-ask a question the user has already answered.",
        str(situation_brief or ""),
    )
    return (
        "Your previous attempt repeated an earlier reply. Respond only to the user's "
        "latest message, with different content.\n\n" + stripped
    ).strip()


def _repeat_ratio(a: str, b: str) -> float:
    na, nb = _repeat_norm(a), _repeat_norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _is_repeat_of_recent(candidate: str, prior_replies, *,
                         ratio: float = _REPEAT_RATIO) -> bool:
    """True when `candidate` restates something ELI already said.

    Compared head-to-head over the same number of characters: a reply that opens
    identically and then continues is still a repeat to the reader, and comparing a
    200-char head against a 600-char prior reply would score low purely on the
    length difference and let it through.
    """
    cand = str(candidate or "").strip()
    if len(_repeat_norm(cand)) < 40:
        return False                      # too short to judge; "Okay." is not a repeat
    for prior in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]:
        p = str(prior or "").strip()
        if not p:
            continue
        n = min(len(cand), len(p))
        if n < 40:
            continue
        if _repeat_ratio(cand[:n], p[:n]) >= ratio:
            return True
    return False


class _RepeatDetected(Exception):
    """Raised inside the streaming guard when the opening restates a recent reply.

    An exception rather than a return value because the check lives inside a
    generator: it has to unwind the partially-consumed stream without having
    yielded any of it to the caller.
    """


def _stream_holding_back_repeats(stream, recent_replies, *, allow_retry: bool):
    """Yield `stream`, withholding the opening until it is cleared as non-repeating.

    Once a chunk is yielded it is on the user's screen, so the check must happen
    before first paint: the first `_REPEAT_HEAD_CHARS` are buffered, tested, then
    released untouched in the common case.
    """
    head, released = [], False
    for chunk in stream:
        piece = str(chunk or "")
        if not piece:
            continue
        if released:
            yield piece
            continue
        head.append(piece)
        if sum(len(h) for h in head) < _REPEAT_HEAD_CHARS:
            continue
        opening = "".join(head)
        if allow_retry and recent_replies and _is_repeat_of_recent(opening, recent_replies):
            raise _RepeatDetected(opening)
        released = True
        yield opening
    if not released:
        # The stream ended inside the buffer: a short reply, still worth checking.
        opening = "".join(head)
        if opening:
            if allow_retry and recent_replies and _is_repeat_of_recent(opening, recent_replies):
                raise _RepeatDetected(opening)
            yield opening
