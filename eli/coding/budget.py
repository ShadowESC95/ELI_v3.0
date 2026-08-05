"""Shared wall-clock budget for the coding agent.

The coding agent's loops (tree search roots/refinements, DAG nodes, critique
passes) each used to carry their own local `time_budget_s`, re-armed per
invocation and checked only AFTER a generation returned. On a CPU-offloaded
local model a single call runs 30-500s, so a per-loop budget bounded nothing:
one self-repair request fanned out to ~110-130 sequential calls.

`Deadline` is a single ABSOLUTE instant shared by every loop in one solve. Pass
one instance down the call chain and check it BEFORE starting each model call.
It also reports expiry when the runtime is shutting down, so the same check that
enforces the budget also stops the loops from spinning through a shutdown.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

# Default wall budget for one self-repair solve. Deliberately generous relative
# to a single call but far below the ~10-15h the unbounded path could reach.
DEFAULT_SOLVE_BUDGET_S = 600.0


def _shutting_down() -> bool:
    try:
        from eli.cognition.gguf_inference import is_shutting_down
        return bool(is_shutting_down())
    except Exception:
        return False


class Deadline:
    """An absolute wall-clock deadline, safe to share across threads.

    `Deadline(None)` never expires on time alone but still expires at shutdown.
    """

    __slots__ = ("expires_at", "_label", "_fired")

    def __init__(self, budget_s: Optional[float] = DEFAULT_SOLVE_BUDGET_S,
                 *, label: str = "coding") -> None:
        self.expires_at: Optional[float] = (
            time.monotonic() + float(budget_s) if budget_s and budget_s > 0 else None
        )
        self._label = str(label)
        self._fired = False

    @classmethod
    def coerce(cls, value: "Deadline | float | None",
               *, label: str = "coding") -> "Deadline":
        """Accept a Deadline, a raw budget in seconds, or None."""
        if isinstance(value, cls):
            return value
        if isinstance(value, (int, float)):
            return cls(float(value), label=label)
        return cls(None, label=label)

    def remaining(self) -> float:
        if self.expires_at is None:
            return float("inf")
        return max(0.0, self.expires_at - time.monotonic())

    def expired(self) -> bool:
        if _shutting_down():
            return True
        return self.expires_at is not None and time.monotonic() >= self.expires_at

    def check(self, stage: str = "") -> bool:
        """True when work should stop. Logs the first expiry only."""
        if not self.expired():
            return False
        if not self._fired:
            self._fired = True
            reason = "shutdown" if _shutting_down() else "wall budget exhausted"
            log.info("[CODING][DEADLINE] %s halted at %s (%s)",
                     self._label, stage or "?", reason)
        return True

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        rem = self.remaining()
        rem_s = "inf" if rem == float("inf") else f"{rem:.0f}s"
        return f"<Deadline {self._label} remaining={rem_s}>"
