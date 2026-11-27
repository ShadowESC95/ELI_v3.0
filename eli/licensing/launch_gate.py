"""Shared license gate for GUI and CLI entry points.

Launch flow for a retail build with nothing activated:

1. no trial used yet  -> grant this install's own 7-day trial, tell the user, run
2. trial still valid  -> run (the activation state carries the expiry)
3. trial expired      -> revoke it permanently, then show the purchase dialog,
                         which doubles as the "paste your purchased key" path
4. key pasted         -> activate and run; cancelled -> exit with instructions

The prompt is offered whenever a display is available. It deliberately does not
depend on ``sys.stdin.isatty()``: a GUI app started from a desktop icon or file
manager has no tty, which is precisely the case that most needs the dialog.
"""
from __future__ import annotations

import logging
import os
import sys

# The licence store (eli-licensing): what is on sale for ELI v3, with a claim so ELI collects the
# key itself; support is on the store's pages.
# Where to buy: the website, opened in the browser when the person asks. ELI itself never goes
# online for its licence (a key per computer, checked here).
STORE_URL = "https://geteli.tech"
PURCHASE_URL = STORE_URL
PURCHASE_EMAIL = ""

log = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def gui_prompt_available() -> bool:
    """Whether an interactive dialog can plausibly be shown."""
    if _truthy("ELI_NO_GUI_PROMPT"):
        return False
    if sys.platform in {"win32", "darwin"}:
        return True
    # Offscreen/minimal Qt platforms can create widgets but nobody can see them;
    # treat those as non-interactive so we fall through to a readable message.
    if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() in {"offscreen", "minimal", "vnc"}:
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _notify(message: str) -> None:
    print(message, file=sys.stderr)


def _announce_trial(days: int, reference: str, *, allow_gui_prompt: bool = True) -> None:
    _notify(
        f"ELI v3 — {days}-day trial started (ref {reference}).\n"
        f"  Purchase a full license any time: {PURCHASE_URL}"
    )
    if not allow_gui_prompt or not gui_prompt_available():
        return
    try:
        from eli.gui.license_dialog import show_trial_started

        show_trial_started(days=days, reference=reference)
    except Exception:
        log.debug("trial-started notice could not be shown", exc_info=True)


def _ask_for_key(*, reason: str, expired: bool) -> str | None:
    """Ask for a license key via GUI if possible, else a tty prompt."""
    if gui_prompt_available():
        try:
            if expired:
                from eli.gui.license_dialog import prompt_purchase_or_key

                return prompt_purchase_or_key(reason=reason)
            from eli.gui.license_dialog import prompt_for_license_key

            return prompt_for_license_key(reason=reason)
        except Exception:
            log.debug("GUI key prompt failed; falling back to terminal", exc_info=True)
    if sys.stdin.isatty():
        try:
            from eli.gui.license_dialog import prompt_terminal

            return prompt_terminal(reason)
        except Exception:
            return None
    return None


def _requirement_error(message: str, *, expired: bool) -> "Exception":
    from eli.licensing.activation import LicenseRequiredError

    headline = (
        "ELI v3 trial has ended — a paid license key is required."
        if expired
        else "ELI v3 license activation required."
    )
    return LicenseRequiredError(
        f"{headline}\n"
        f"  Reason: {message}\n"
        "  Activate:  eli --activate-key YOUR-KEY\n"
        "  Status:    eli --license-status\n"
        f"  Purchase:  {PURCHASE_URL}\n"
        "  Terms:     COMMERCIAL_EULA.md"
    )


def ensure_license_or_exit(*, allow_gui_prompt: bool = True) -> None:
    from eli.licensing.activation import activate_license_key, validate_activation
    from eli.licensing.trial import ensure_trial_activation, revoke_trial, trial_duration_days

    from eli.licensing.integrity import refusal

    altered = refusal()  # checked here as well as in validate_activation: taking one out is not enough
    if altered:
        raise _requirement_error(altered, expired=False)

    status = validate_activation()
    if status.active or not status.required:
        return

    if status.trial == "clock_behind":
        raise _requirement_error(
            "the computer's clock is behind the last time ELI ran: set the date and time right "
            "and start ELI again (the trial is not lost)", expired=False)

    # ── Trial expired: revoke first, so a clock change cannot resurrect it ──
    if status.trial == "expired":
        revoke_trial(reason="expired")
        status = validate_activation()
        if status.active or not status.required:
            return

    # ── First run on a retail build: start this install's own trial. A trial
    #    already running but missing its activation state (wiped config) is
    #    restored here rather than treated as a lockout. ───────────────────────
    if status.trial in {"available", "active"}:
        fresh = status.trial == "available"
        state = ensure_trial_activation()
        if state is not None:
            if fresh:
                _announce_trial(trial_duration_days(), state.customer_ref or "",
                                allow_gui_prompt=allow_gui_prompt)
            if validate_activation().active:
                return

    expired = status.trial in {"expired", "revoked"}
    # After revocation the activation state is gone, so validate_activation()
    # reports "no license activated" — true but misleading. Say what happened.
    reason = (
        f"the {trial_duration_days()}-day trial for this machine has ended and been revoked"
        if expired
        else (status.message or "no license activated")
    )

    if allow_gui_prompt:
        key = _ask_for_key(reason=reason, expired=expired)
        if key:
            try:
                activate_license_key(key)
            except Exception as exc:
                _notify(f"Activation failed: {exc}")
            else:
                if validate_activation().active:
                    return

    raise _requirement_error(reason, expired=expired)


def launch_allowed(*, allow_gui_prompt: bool = True) -> bool:
    """Whether this run may go on. Every way into ELI asks here (the window, the terminal, the
    phone/web server, the MCP server): one left out served the whole engine with no licence.
    When the answer is no, why and what to do is written to stderr."""
    from eli.licensing.activation import LicenseRequiredError

    try:
        ensure_license_or_exit(allow_gui_prompt=allow_gui_prompt)
    except LicenseRequiredError as exc:
        _notify(str(exc))
        return False
    return True
