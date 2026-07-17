"""Engine guards — output/evidence guards shared by the engine and its stages.

Three cohesive groups:
  * failed-executor: detect a failed executor result in the evidence and surface
    it honestly instead of letting the model narrate around it;
  * identity self-report: spot an identity self-report request and reject a bad
    self-report output;
  * scaffold strip: remove reasoning scaffolding from generated text.

Lifted from engine.py so the engine and the reasoning-mode stage share one
implementation rather than each reaching into the monolith.
"""
from __future__ import annotations

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
