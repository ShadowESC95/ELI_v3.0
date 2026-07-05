"""Shared license gate for GUI and CLI entry points."""
from __future__ import annotations

import sys


def ensure_license_or_exit(*, allow_gui_prompt: bool = True) -> None:
    from eli.licensing.activation import (
        LicenseRequiredError,
        activate_license_key,
        validate_activation,
    )

    status = validate_activation()
    if status.active or not status.required:
        return

    if allow_gui_prompt and sys.stdin.isatty():
        try:
            from eli.gui.license_dialog import prompt_for_license_key

            key = prompt_for_license_key(reason=status.message or "")
            if key:
                activate_license_key(key)
                if validate_activation().active:
                    return
        except Exception:
            pass

    raise LicenseRequiredError(
        "ELI v3 license activation required.\n"
        f"  Reason: {status.message}\n"
        "  Activate:  eli --activate-key YOUR-KEY\n"
        "  Status:    eli --license-status\n"
        "  Purchase:  jaybridgeman0095@gmail.com · docs/STORE.md\n"
        "  Terms:     COMMERCIAL_EULA.md"
    )
