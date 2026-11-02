"""At start-up, when bundles are installed: open ELI v3, or one of them, in its place."""

from __future__ import annotations

import logging
from typing import Any

from eli.bundles import Bundle, Offer, discover, offers

log = logging.getLogger("eli.bundles")
SETTING = "startup_target"  # "ask" (default) | "eli" | "bundle:<id>"


def _settings() -> dict[str, Any]:
    try:
        from eli.core.runtime_settings import load_settings

        return dict(load_settings() or {})
    except Exception:  # noqa: BLE001 - unreadable settings mean: ask
        return {}


def _remember(value: str) -> None:
    try:
        from eli.core.runtime_settings import save_settings

        save_settings({SETTING: value})
    except Exception:
        log.warning("could not remember the start-up choice", exc_info=True)


def entries(listed: list[Offer]) -> list[tuple[str, bool, str]]:
    """What the chooser shows for each bundle: its text, whether it can be opened, and its id. A
    bundle that cannot be opened here is shown anyway, with why: ELI VR, ELI Legal and the ones to
    come are upgrades a person is told about, not left to guess at."""
    out = []
    for offer in listed:
        summary = offer.bundle.summary[:90] if offer.bundle and offer.bundle.summary else ""
        if offer.state == "ready":
            text = offer.label + (f" — {summary}" if summary else "")
        elif offer.state == "needs_key":
            text = f"{offer.label} — needs a licence key: enter it from ELI v3's Bundles menu"
        elif offer.state == "not_installed":
            text = f"{offer.label} — not installed" + (f": {offer.releases}" if offer.releases else "")
        else:
            text = f"{offer.label} — coming"
        out.append((text, offer.state == "ready", offer.id))
    return out


def choose(bundles: dict[str, Bundle]) -> str | None:
    """Ask which to open. Returns a bundle id, "eli", or None if the window was closed."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDialog,
        QLabel,
        QPushButton,
        QVBoxLayout,
    )

    app = QApplication.instance() or QApplication([])  # noqa: F841 - kept alive for the dialog
    dialog = QDialog()
    dialog.setWindowTitle("ELI")
    dialog.setMinimumWidth(460)
    layout = QVBoxLayout(dialog)
    heading = QLabel("<b>What would you like to open?</b>")
    heading.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(heading)
    chosen: dict[str, str] = {}

    def pick(value: str) -> None:
        chosen["value"] = value
        dialog.accept()

    main = QPushButton("ELI v3 — the full assistant")
    main.clicked.connect(lambda: pick("eli"))
    layout.addWidget(main)
    try:
        listed = offers()
    except Exception:  # noqa: BLE001 - the licence or edition files unreadable: the installed ones
        log.warning("bundle availability unavailable; listing the installed ones", exc_info=True)
        listed = [Offer(b.id, b.title, "ready", "", b) for b in bundles.values()]
    for text, can_open, bundle_id in entries(listed):
        button = QPushButton(text)
        bundle = bundles.get(bundle_id)
        if bundle is not None and bundle.icon:
            button.setIcon(QIcon(bundle.icon))
        button.setEnabled(can_open)
        if can_open:
            button.clicked.connect(lambda _c=False, b=bundle_id: pick(b))
        layout.addWidget(button)
    remember = QCheckBox(
        "Always open this (change it in Settings, or start with --choose)"
    )
    layout.addWidget(remember)
    if dialog.exec() != QDialog.DialogCode.Accepted or "value" not in chosen:
        return None
    if remember.isChecked():
        _remember("eli" if chosen["value"] == "eli" else f"bundle:{chosen['value']}")
    return chosen["value"]


def target_at_startup(args: list[str]) -> str | None:
    """The bundle to open in place of ELI v3's window, or None to open ELI v3."""
    if "--no-bundles" in args:
        return None
    bundles = discover()
    if not bundles:
        return None
    setting = str(_settings().get(SETTING) or "ask")
    if "--choose" not in args:
        if setting == "eli":
            return None
        if setting.startswith("bundle:") and setting[7:] in bundles:
            return setting[7:]
    choice = choose(bundles)
    if choice is None:
        raise SystemExit(0)  # closed without choosing: open nothing
    return None if choice == "eli" else choice
