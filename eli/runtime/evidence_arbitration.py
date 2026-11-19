from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict

from eli.utils.log import get_logger

log = get_logger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class EvidenceItem:
    kind: str
    source: str
    score: float
    summary: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)


def _trim(text: Any, n: int = 280) -> str:
    s = " ".join(str(text or "").split()).strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def _score_tool_result(rec: Any) -> EvidenceItem:
    # Default to None (unknown), not True -- a missing "ok" field is an
    # unverified outcome, not a confirmed success (it used to score the same
    # 0.86 as a real success).
    ok = _get(rec, "ok", None)
    status = str(_get(rec, "status", "ok") or "ok")
    action = str(_get(rec, "action", "") or "")
    summary = _trim(_get(rec, "summary", "") or action or status)
    payload = _get(rec, "payload", {}) or {}
    created_at = str(_get(rec, "created_at", "") or _utc_now())

    # None (missing) and False (failure) both land on the lower score --
    # unverified isn't trustworthy just because it isn't a known failure.
    # A result that reports success is trusted by the action's own measured reliability (the chance
    # such a run works, from its runs here: evidence_ledger.predict_success), once there is history;
    # the fixed figure stands only until then.
    score = 0.86 if (ok is not None and bool(ok)) else 0.30
    if ok is not None and bool(ok) and action:
        try:
            from eli.runtime.evidence_ledger import predict_success
            measured = predict_success(action)
            if measured is not None:
                score = float(measured)
        except (OSError, ValueError, ImportError) as failure:   # no history to be read: the fixed figure stands
            log.debug("reliability unavailable for the tool result: %s", failure)
    if status in {"approved", "applied", "executed"}:
        score += 0.05
    if status in {"error", "blocked"}:
        score -= 0.08

    return EvidenceItem(
        kind="tool_result",
        source=f"tool:{action or 'unknown'}",
        score=max(0.0, min(1.0, score)),
        summary=summary,
        payload=dict(payload if isinstance(payload, dict) else {"repr": repr(payload)[:400]}),
        created_at=created_at,
    )
