"""First-run license activation for retail / ELI_REQUIRE_LICENSE builds."""
from __future__ import annotations

import sys
from typing import Optional


def prompt_for_license_key(*, reason: str = "") -> Optional[str]:
    """Return a license key string, or None if the user cancelled."""
    try:
        from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    except ImportError:
        return _prompt_terminal(reason)

    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication(sys.argv[:1] or ["eli"])

    intro = (
        "ELI v3 requires a paid license key on this build.\n\n"
        f"{reason}\n\n"
        "Paste the key from your purchase email (starts with ELIV3-).\n"
        "Purchase: jaybridgeman0095@gmail.com · ko-fi.com/shadowesc95"
    )
    QMessageBox.information(None, "ELI v3 — License required", intro.strip())

    key, ok = QInputDialog.getText(
        None,
        "Activate ELI v3",
        "License key:",
        echo=QInputDialog.EchoMode.Normal,
    )
    if owns_app and app is not None:
        app.quit()
    if not ok:
        return None
    key = (key or "").strip()
    return key or None


def _prompt_terminal(reason: str) -> Optional[str]:
    print("\nELI v3 — license activation required")
    if reason:
        print(f"  {reason}")
    print("  Purchase: jaybridgeman0095@gmail.com")
    try:
        key = input("  Paste license key (ELIV3-…): ").strip()
    except EOFError:
        return None
    return key or None
