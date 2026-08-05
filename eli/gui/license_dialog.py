"""License activation + purchase dialogs for retail / ELI_REQUIRE_LICENSE builds.

Presented as a single dialog with the key field in it, rather than an
information box followed by a separate input box — the two-step version left
users staring at an OK button with nowhere to type.

Any ``QApplication`` created here is deliberately left alive for the main window
to reuse (see ``eli.gui.main_window.window.main``): Qt allows only one
application object per process, so tearing this one down after activation would
break the launch it just unblocked.
"""
from __future__ import annotations

import logging
import sys
import webbrowser
from typing import Optional

PURCHASE_EMAIL = "jaybridgeman0095@gmail.com"
PURCHASE_URL = "https://ko-fi.com/shadowesc95"

log = logging.getLogger(__name__)


def _qt():
    """Qt widget names, or None when no binding is importable."""
    try:
        from eli.gui.qt_compat import (  # noqa: F401
            QApplication,
            QDialog,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMessageBox,
            QPushButton,
            Qt,
            QVBoxLayout,
        )
    except Exception:
        return None
    if QApplication is None or getattr(QApplication, "__name__", "") == "_Stub":
        return None
    return {
        "QApplication": QApplication,
        "QDialog": QDialog,
        "QHBoxLayout": QHBoxLayout,
        "QLabel": QLabel,
        "QLineEdit": QLineEdit,
        "QMessageBox": QMessageBox,
        "QPushButton": QPushButton,
        "Qt": Qt,
        "QVBoxLayout": QVBoxLayout,
    }


# Holds any QApplication this module creates. Without a strong reference the
# object is garbage-collected the moment the creating call returns, and the next
# widget dies with "Must construct a QApplication before a QWidget".
_OWNED_APP = None


def _ensure_app(qt: dict):
    """Return a live QApplication, creating one only if none exists."""
    global _OWNED_APP
    app = qt["QApplication"].instance()
    if app is None:
        app = qt["QApplication"](sys.argv[:1] or ["eli"])
        _OWNED_APP = app
    return app


def _run(dialog) -> int:
    """exec() across PySide6/PyQt6 (exec) and PyQt5 (exec_)."""
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return int(runner())


def _open_purchase_page() -> None:
    try:
        webbrowser.open(PURCHASE_URL)
    except Exception:
        log.debug("could not open the purchase page in a browser", exc_info=True)


def _key_dialog(*, title: str, heading: str, body: str, buy_label: str) -> Optional[str]:
    qt = _qt()
    if qt is None:
        return None
    _ensure_app(qt)

    dialog = qt["QDialog"]()
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(520)

    layout = qt["QVBoxLayout"](dialog)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(12)

    head = qt["QLabel"](heading)
    head.setWordWrap(True)
    head.setStyleSheet("font-size: 15px; font-weight: 600;")
    layout.addWidget(head)

    text = qt["QLabel"](body)
    text.setWordWrap(True)
    text.setOpenExternalLinks(True)
    layout.addWidget(text)

    field = qt["QLineEdit"]()
    field.setPlaceholderText("ELIV3-…  (paste your license key here)")
    field.setClearButtonEnabled(True)
    layout.addWidget(field)

    row = qt["QHBoxLayout"]()
    buy = qt["QPushButton"](buy_label)
    quit_btn = qt["QPushButton"]("Quit")
    activate = qt["QPushButton"]("Activate")
    activate.setDefault(True)
    row.addWidget(buy)
    row.addStretch(1)
    row.addWidget(quit_btn)
    row.addWidget(activate)
    layout.addLayout(row)

    # "Buy" opens the browser but keeps the dialog up, so the key can be pasted
    # straight back in once the purchase completes.
    buy.clicked.connect(_open_purchase_page)
    activate.clicked.connect(dialog.accept)
    quit_btn.clicked.connect(dialog.reject)
    field.returnPressed.connect(dialog.accept)

    accepted = _run(dialog) == int(qt["QDialog"].DialogCode.Accepted)
    if not accepted:
        return None
    return (field.text() or "").strip() or None


def prompt_for_license_key(*, reason: str = "") -> Optional[str]:
    """Ask for a key on a build that requires activation."""
    detail = f"<p>{reason}</p>" if reason else ""
    return _key_dialog(
        title="ELI v3 — License required",
        heading="ELI v3 requires a paid license key on this build.",
        body=(
            f"{detail}"
            "<p>Paste the key from your purchase email (it starts with "
            "<b>ELIV3-</b>).</p>"
            f'<p>Purchase: <a href="{PURCHASE_URL}">{PURCHASE_URL}</a><br>'
            f'Support: <a href="mailto:{PURCHASE_EMAIL}">{PURCHASE_EMAIL}</a></p>'
        ),
        buy_label="Buy a license",
    )


def prompt_purchase_or_key(*, reason: str = "") -> Optional[str]:
    """Trial-over dialog: purchase the full platform, or paste a bought key."""
    detail = f"<p>{reason}</p>" if reason else ""
    return _key_dialog(
        title="ELI v3 — Trial ended",
        heading="Your ELI v3 trial has ended.",
        body=(
            f"{detail}"
            "<p>The trial license for this machine has been revoked. Purchase the "
            "full platform to carry on — your conversations, memories and settings "
            "are kept and will be there when you activate.</p>"
            f'<p><b>Purchase:</b> <a href="{PURCHASE_URL}">{PURCHASE_URL}</a><br>'
            f'<b>Support:</b> <a href="mailto:{PURCHASE_EMAIL}">{PURCHASE_EMAIL}</a></p>'
            "<p>Already bought one? Paste the key below.</p>"
        ),
        buy_label="Buy now",
    )


def show_trial_started(*, days: int, reference: str = "") -> None:
    """One-time notice that this install's trial has begun."""
    qt = _qt()
    if qt is None:
        return
    try:
        _ensure_app(qt)
        ref = f"\n\nTrial reference: {reference}" if reference else ""
        qt["QMessageBox"].information(
            None,
            "ELI v3 — Trial started",
            f"Your {days}-day ELI v3 trial has started on this machine.\n\n"
            "Everything is unlocked for the trial period. When it ends you can "
            "purchase the full platform to keep going."
            f"{ref}\n\nPurchase: {PURCHASE_URL}",
        )
    except Exception:
        log.debug("trial-started dialog could not be shown", exc_info=True)


def prompt_terminal(reason: str = "") -> Optional[str]:
    """Terminal fallback when no display is available."""
    print("\nELI v3 — license activation required")
    if reason:
        print(f"  {reason}")
    print(f"  Purchase: {PURCHASE_URL} · {PURCHASE_EMAIL}")
    try:
        key = input("  Paste license key (ELIV3-…): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return key or None


# Back-compat for any caller of the pre-rewrite private helper.
_prompt_terminal = prompt_terminal
