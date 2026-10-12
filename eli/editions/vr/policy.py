"""Edition policy hooks for the ELI VR edition.

The core approval/output layer calls these when this edition is active, so an
edition can add disclaimers, tighten the action allow-list, or inject a domain
system-prompt addendum — WITHOUT the core knowing anything domain-specific.

Everything here is a scaffold stub returning safe/neutral defaults.
"""
from __future__ import annotations


def system_prompt_addendum() -> str:
    """Extra system-prompt text prepended when this edition is active."""
    return ""


def output_disclaimer() -> str | None:
    """A disclaimer appended to user-facing output, or None."""
    return None


def blocked_actions() -> set[str]:
    """Capability/action names this edition additionally refuses (defence in depth)."""
    return set()
