"""Router content guards — react-to-pasted-content prepass (opinion/evaluation over quoted text stays conversational) and the proof-of-reading challenge. Moved from router_enhanced.py."""
from __future__ import annotations

import re

from eli.utils.log import get_logger
from eli.execution.routing._helpers import _mk  # noqa: F401


def _eli_react_to_content_prepass(original_text: str, raw: str, low: str):
    """Force CHAT when the user asks ELI to react to / give an opinion on text
    they have pasted or referenced.

    Quoted material must never be scanned for status/command intent: the words
    inside a pasted block belong to the thing being judged, not to an
    instruction. Without this, a long paste that happens to mention ELI's
    internals ("memory", "faiss", "runtime", "context") trips a status regex
    and the turn is hijacked into a deterministic data dump instead of an
    actual reply. Routing to CHAT with the full (whitespace-normalised) text as
    the message also guarantees the referenced content is present in the prompt
    so the model can genuinely evaluate it rather than confabulate.
    """
    if not low:
        return None
    react_frame = re.search(
        r"\b(what(?:'s| is| do| are)?\s+(?:you|your)\s+(?:think|reckon|make|take|opinion|view|thoughts?)"
        r"|what do you (?:think|reckon|make) of"
        r"|your (?:thoughts?|opinion|take|view)\b"
        r"|thoughts on\b"
        r"|respond to\b|react to\b|reply to\b"
        r"|the following (?:reply|response|message|text|paragraph)"
        r"|this (?:reply|response|message|paste|text)\b"
        r"|(?:reply|response|message|text) (?:from|by) \w+"
        r"|i (?:just )?(?:sent|pasted|posted|shared|wrote)\b"
        r"|(?:message|reply|response|text) (?:above|earlier)\b"
        r"|(?:above|earlier) (?:message|reply|response)\b"
        r"|read (?:this|the following)\b)",
        low,
    )
    if not react_frame:
        return None
    src = original_text or ""
    has_paste = ("-->" in src) or (src.count("\n") >= 2) or (len(src) > 600)
    points_at_content = bool(re.search(
        r"\b(the following|this (?:reply|response|message|paste|text)"
        r"|(?:reply|response|message|text) (?:from|by) \w+"
        r"|i (?:just )?(?:sent|pasted|posted|shared)"
        r"|above|earlier|chatgpt|gpt|that (?:reply|response|message))\b",
        low,
    ))
    if not (has_paste or points_at_content):
        return None
    return _mk(
        "CHAT",
        {"message": raw},
        0.9,
        matched_by="chat.react_to_pasted_content",
    )


def _eli_proof_of_reading_challenge(low: str) -> bool:
    """True when the user is challenging ELI to prove it actually read/scanned a
    file or its own code — skepticism about ELI's *own action*.

    This reads the whole utterance for the challenge frame rather than matching
    isolated keywords, so it separates two superficially-similar shapes that a
    bare keyword pair cannot:

      • CHALLENGE  → audit:  "prove that you actually scanned the file",
        "timestamps as proof that you read the file", "did you even open it?"
      • IMPERATIVE → not audit: "read the data in that file and prove it's right"
        — here "prove" is about the *data's* correctness and "read" is a request,
        not a claim that ELI already read anything.

    The discriminator is whether the proof is aimed at ELI's reading action: a
    second-person past/perfect claim ("you read/scanned/opened…"), a "proof that
    you …" / "prove (that) you …" frame, or a "did/have you (actually) read…"
    frame — OR an explicit reference to ELI's own runtime/source surface.
    """
    if not re.search(
        r"\b(prove|proof|timestamps?|don'?t believe|do not believe|"
        r"are you lying|you'?re lying|convince me)\b",
        low,
    ):
        return False
    # (a) explicit reference to ELI's own runtime/source.
    runtime_ref = re.search(
        r"\b(gui|audit|runtime|your (?:code|source|files?)|source code|the codebase)\b",
        low,
    )
    # (b) a challenge aimed at ELI's *own* reading/scanning action. Bare "you
    #     read" is excluded (it collides with the request "can you read…");
    #     it only counts with an emphatic adverb or inside a proof/did-you frame.
    eli_read_claim = (
        re.search(r"\bproof that you\b", low)
        or re.search(r"\bprove (?:to me )?(?:that )?(?:you|u)\b", low)
        or re.search(r"\b(?:did|have|haven'?t|didn'?t) (?:you|u) "
                     r"(?:actually |even |really )?(?:read|scan|open|look)", low)
        or re.search(r"\b(?:you|u) (?:actually|really|even|genuinely|truly) "
                     r"(?:read|scann?ed|looked at|opened|examined|checked)\b", low)
        or re.search(r"\b(?:you|u) (?:scann?ed|looked at|opened|examined)\b", low)
    )
    return bool(runtime_ref or eli_read_claim)
