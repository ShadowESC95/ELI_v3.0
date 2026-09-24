"""Extensions — everything that adds to ELI from outside it.

Three different things live here because they answer one question — "what else can
ELI do, and who wrote it?" — and because they share one security posture:

  plugins       Python that runs INSIDE ELI's process, under the permission gate
  MCP servers   separate programs ELI talks to over stdio, which the permission
                gate cannot reach and the offline switch cannot stop
  marketplace   where both of those come from, and how far they can be trusted

The pages follow the decisions an operator actually makes:

  Overview    what is installed, what it can do to this machine, and where the
              documentation and config files are
  Browse      what the community registries offer, with verification state and
              permission risk shown BEFORE anything is downloaded
  Installed   what is on this machine, what it may do, and a switch to turn it off
  Permissions every grant given, and a way to take any of them back
  MCP servers the servers configured here, whether each actually answers, and the
              form that adds one
  Sources     which community registries this machine trusts at all
  Settings    the knobs that change how the above behave, plus the honest report of
              what this machine's sandbox can and cannot enforce

This was a sub-tab of the Advanced Settings dialog, which meant the entire extension
surface — including every MCP server — was three clicks inside a modal nobody opens.
It is a top-level tab because installing someone else's code is a first-class act,
not a preference.

The install path is deliberately slow. A listing is downloaded to memory, hashed,
signature-checked, statically analysed against its own manifest, and run through
every malware engine available — and only then is the operator shown a summary and
asked. Nothing is written to disk before that answer, and what is written arrives
switched OFF with no permissions granted.

Threading: scanning, downloading and MCP handshakes happen on a QThread and reach
the GUI through signals only. QTimer.singleShot from a worker never fires.
"""
from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from eli.gui.panels._qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QLineEdit, QComboBox, QTabWidget, QSplitter,
    QAbstractItemView, QHeaderView, QMessageBox, QDialog, QFormLayout,
    QProgressBar, QInputDialog, QCheckBox, QSpinBox, QGroupBox, QScrollArea,
    QPlainTextEdit, QDialogButtonBox, QUrl, QDesktopServices, QColor,
    Qt, QThread, QObject, pyqtSignal,
)
from eli.utils.log import get_logger

log = get_logger(__name__)

_OK = "#a3be8c"
_WARN = "#ebcb8b"
_BAD = "#bf616a"
_DIM = "#6c7086"
_RISK = {"low": _OK, "medium": _WARN, "high": "#d08770", "critical": _BAD}

# Documentation that ships with ELI. Local files, opened in whatever the OS uses for
# them — an offline install must not be told to go and read a website.
_DOC_LINKS = [
    ("Extending ELI — writing a plugin", "blueprints/EXTENDING_ELI.md"),
    ("MCP — both directions", "blueprints/mcp.md"),
    ("Marketplace — publishing and curation", "docs/MARKETPLACE.md"),
    ("Plugin permissions & sandboxing", "blueprints/security.md"),
]


def _repo_doc(rel: str) -> Optional[Path]:
    """Locate a shipped doc. Returns None rather than a path that is not there."""
    try:
        from eli.core.paths import get_paths
        roots = [Path(get_paths().project_root)]
    except Exception:
        roots = []
    roots.append(Path(__file__).resolve().parents[3])
    for root in roots:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


# The config files this tab reads and writes. Resolved through the owning module
# rather than hard-coded, so the list survives an ELI_CONFIG_DIR override, a portable
# install, or a frozen build — where the paths differ and a literal would be a
# confident lie.
def _mcp_config_path():
    from eli.plugins.mcp import config_path
    return config_path()


def _registries_config_path():
    from eli.plugins.marketplace import _registries_path
    return _registries_path()


def _grants_config_path():
    from eli.plugins.permissions import _grants_path
    return _grants_path()


def _audit_log_path():
    from eli.plugins.permissions import _audit_path
    return _audit_path()


def _installed_plugins_dir():
    from eli.plugins.manager import _plugins_dir
    return _plugins_dir()


def _yara_ruleset_path():
    from eli.plugins.security_scan import yara_rules_path
    return yara_rules_path()


_CONFIG_LOCATIONS = [
    ("MCP servers", _mcp_config_path),
    ("Plugin sources (registries)", _registries_config_path),
    ("Granted permissions", _grants_config_path),
    ("Permission audit log", _audit_log_path),
    ("Installed plugins", _installed_plugins_dir),
    ("YARA ruleset (optional)", _yara_ruleset_path),
]


def _left(widget) -> "QHBoxLayout":
    """A button at its natural width, left-aligned — not a full-width bar."""
    row = QHBoxLayout()
    row.addWidget(widget)
    row.addStretch(1)
    return row


def _count_line(label: str, count: int, noun: str, detail: str = "") -> str:
    """One status line, coloured by whether the number means anything is there."""
    colour = _OK if count else _DIM
    tail = f" <span style='color:{_DIM}'>· {detail}</span>" if detail else ""
    return f"{label}: <span style='color:{colour}'><b>{count}</b> {noun}</span>{tail}"


def _scanner_summary() -> str:
    """Which malware engines can actually run here.

    The nine built-in engines always run. ClamAV and YARA are the optional deep ones,
    and a scan without them is reported as partial rather than clean — so the tab
    says up front which of the two this machine has.
    """
    import shutil
    parts = [f"<span style='color:{_OK}'>9 built-in</span>"]
    clam = shutil.which("clamdscan") or shutil.which("clamscan")
    parts.append(f"<span style='color:{_OK}'>ClamAV</span>" if clam
                 else f"<span style='color:{_DIM}'>ClamAV not installed</span>")
    import importlib.util
    if importlib.util.find_spec("yara") is None:
        parts.append(f"<span style='color:{_DIM}'>YARA not installed</span>")
    else:
        try:
            from eli.plugins.security_scan import yara_rules_path
            has_rules = Path(yara_rules_path()).is_file()
        except Exception:
            has_rules = False
        # A ruleset-less YARA is reported as absent rather than present: it runs and
        # matches nothing, which is not the same as having looked.
        parts.append(f"<span style='color:{_OK}'>YARA</span>" if has_rules
                     else f"<span style='color:{_DIM}'>YARA (no ruleset)</span>")
    return " · ".join(parts)


class _Worker(QObject):
    """Runs a blocking marketplace call off the GUI thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:
            log.debug("marketplace worker failed", exc_info=True)
            self.failed.emit(str(exc))


class ExtensionsTab(QWidget):
    """Plugins, MCP servers and the marketplace, as one top-level tab."""

    #: Page order, so `open_page("mcp")` survives someone inserting a tab.
    PAGES = ("overview", "browse", "installed", "permissions", "mcp",
             "sources", "settings")

    def __init__(self, parent_window=None):
        super().__init__()
        self._parent_window = parent_window
        self._listings: List[Dict[str, Any]] = []
        self._thread: Optional[QThread] = None
        self._worker: Optional[_Worker] = None
        self._build()
        self._refresh_installed()
        self._refresh_permissions()
        self._refresh_registries()
        self._refresh_mcp()
        self._refresh_overview()

    # ── shell ─────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)

        hdr = QLabel("🔌  Extensions — plugins, MCP servers & marketplace")
        hdr.setStyleSheet(
            "font-size:13px;font-weight:bold;padding:4px 8px;"
            "background:#1a1f2e;color:#e0e6f0;")
        v.addWidget(hdr)

        banner = QLabel(
            "<b>Everything here is code somebody else wrote.</b> "
            "<span style='color:#6c7086'>Nobody vets it — not ELI's author, not a store "
            "review team. ELI checks what it can (the file matches the listing, who signed "
            "it, a malware scan, and for an MCP server a real handshake), shows you the "
            "result, and lets you decide. Nothing installs itself and nothing arrives "
            "switched on.</span>")
        banner.setWordWrap(True)
        banner.setStyleSheet("padding:6px;background:#2e3440;border-radius:4px;")
        v.addWidget(banner)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._page_overview(), "📖 Overview")
        self._tabs.addTab(self._page_browse(), "🛒 Browse")
        self._tabs.addTab(self._page_installed(), "📦 Installed")
        self._tabs.addTab(self._page_permissions(), "🔐 Permissions")
        self._tabs.addTab(self._page_mcp(), "🔌 MCP servers")
        self._tabs.addTab(self._page_registries(), "🌐 Sources")
        self._tabs.addTab(self._page_settings(), "⚙️ Settings")
        self._tabs.currentChanged.connect(self._on_page_changed)
        v.addWidget(self._tabs, 1)

    def open_page(self, name: str) -> None:
        """Jump to a page by name — how other parts of the GUI deep-link in here."""
        try:
            self._tabs.setCurrentIndex(self.PAGES.index(name))
        except ValueError:
            log.debug("[EXT] no such page: %s", name)

    def _on_page_changed(self, index: int) -> None:
        # The overview counts what the other pages change, so it is recomputed on
        # arrival rather than left showing whatever was true when the tab was built.
        if index == self.PAGES.index("overview"):
            self._refresh_overview()

    # ── overview ──────────────────────────────────────────────────────────────
    def _page_overview(self) -> QWidget:
        """What this tab is, what is actually installed, and where to read more.

        Deliberately the first page: someone arriving here has usually not decided to
        install anything yet — they are working out what the three words in the tab
        title mean and whether any of it is safe. Answering that badly is how people
        end up either installing nothing or installing everything.
        """
        outer = QWidget()
        outer_v = QVBoxLayout(outer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)

        # ── the three things, and where each one runs ──────────────────────────
        what = QGroupBox("What lives in this tab")
        wv = QVBoxLayout(what)
        for title, text, page, button in (
            ("🧩 Plugins",
             "Python that runs <b>inside ELI's own process</b>. It gains ELI's actions "
             "and its memory, so every capability it wants — the filesystem, the "
             "network, your clipboard — is asked for individually and can be refused "
             "or revoked at any time.",
             "installed", "Show installed plugins"),
            ("🔌 MCP servers",
             "Separate programs ELI launches and speaks to over stdio. They give ELI "
             "tools it does not have. Because they are their own processes, ELI's "
             "permission gate and its offline switch <b>do not apply to them</b> — "
             "containment, where this machine has any, comes from the kernel instead.",
             "mcp", "Show MCP servers"),
            ("🛒 Marketplace",
             "Where both of the above come from. ELI ships two sources: its own bundled "
             "plugins, and a <b>curated</b> registry whose listings must carry a valid "
             "official signature. Anyone can host another, and adding one is your trust "
             "decision — taken once, revocable, and never made for you.",
             "sources", "Show sources"),
        ):
            box = QVBoxLayout()
            head = QLabel(f"<b>{title}</b>")
            box.addWidget(head)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{_DIM};")
            box.addWidget(lbl)
            row = QHBoxLayout()
            b = QPushButton(button)
            b.clicked.connect(lambda _=False, pg=page: self.open_page(pg))
            row.addWidget(b)
            row.addStretch(1)
            box.addLayout(row)
            wv.addLayout(box)
        v.addWidget(what)

        # ── live state ─────────────────────────────────────────────────────────
        state = QGroupBox("On this machine")
        sv = QVBoxLayout(state)
        self._ov_status = QLabel("…")
        self._ov_status.setWordWrap(True)
        self._ov_status.setTextFormat(Qt.TextFormat.RichText)
        sv.addWidget(self._ov_status)
        row = QHBoxLayout()
        rb = QPushButton("↻ Recheck")
        rb.clicked.connect(self._refresh_overview)
        row.addWidget(rb)
        row.addStretch(1)
        sv.addLayout(row)
        v.addWidget(state)

        # ── containment, stated honestly ───────────────────────────────────────
        contain = QGroupBox("What ELI can enforce here")
        cv = QVBoxLayout(contain)
        self._ov_sandbox = QLabel("…")
        self._ov_sandbox.setWordWrap(True)
        self._ov_sandbox.setTextFormat(Qt.TextFormat.RichText)
        cv.addWidget(self._ov_sandbox)
        v.addWidget(contain)

        # ── documentation ──────────────────────────────────────────────────────
        docs = QGroupBox("Documentation")
        dv = QVBoxLayout(docs)
        note = QLabel("Shipped with ELI — these open from disk, not the internet.")
        note.setStyleSheet(f"color:{_DIM};")
        dv.addWidget(note)
        any_doc = False
        for label, rel in _DOC_LINKS:
            path = _repo_doc(rel)
            b = QPushButton(f"📄 {label}")
            if path is None:
                b.setEnabled(False)
                b.setToolTip(f"{rel} is not present in this install.")
            else:
                any_doc = True
                b.setToolTip(str(path))
                b.clicked.connect(lambda _=False, pth=path: self._open_path(pth))
            brow = QHBoxLayout()
            brow.addWidget(b)
            brow.addStretch(1)
            dv.addLayout(brow)
        if not any_doc:
            miss = QLabel("No shipped documentation found in this install.")
            miss.setStyleSheet(f"color:{_WARN};")
            dv.addWidget(miss)
        v.addWidget(docs)

        # ── links out ──────────────────────────────────────────────────────────
        links = QGroupBox("Links")
        lv = QVBoxLayout(links)
        self._ov_home_btn = QPushButton("🌐 Marketplace website")
        self._ov_home_btn.setToolTip(
            "Discovery only — a website can never install anything. It gives you a "
            "source URL to add under Sources.")
        self._ov_home_btn.clicked.connect(self._open_marketplace)
        lv.addLayout(_left(self._ov_home_btn))

        self._ov_web_btn = QPushButton("🖥 MCP page in ELI's web UI")
        self._ov_web_btn.setToolTip(
            "Live connection state and discovered tools, served by ELI's own web "
            "server. Needs that server running — start it from the Settings tab.")
        self._ov_web_btn.clicked.connect(self._open_mcp_web_page)
        lv.addLayout(_left(self._ov_web_btn))

        proto = QPushButton("📘 modelcontextprotocol.io — the MCP spec")
        proto.setToolTip("Opens the public specification in your browser.")
        proto.clicked.connect(
            lambda: self._open_url("https://modelcontextprotocol.io"))
        lv.addLayout(_left(proto))
        v.addWidget(links)

        v.addStretch(1)
        scroll.setWidget(body)
        outer_v.addWidget(scroll)
        return outer

    def _refresh_overview(self) -> None:
        """Count what is really there. Every number is read, never remembered."""
        if not hasattr(self, "_ov_status"):
            return
        lines: List[str] = []

        try:
            from eli.plugins.manager import get_manager
            installed = get_manager().list_installed()
            enabled = sum(1 for p in installed if p.get("enabled"))
            lines.append(_count_line("🧩 Plugins", len(installed), "installed",
                                     f"{enabled} enabled"))
        except Exception as exc:
            lines.append(f"🧩 Plugins: <span style='color:{_WARN}'>unavailable ({exc})</span>")

        try:
            from eli.plugins.permissions import store
            grants = store().all_grants()
            allowed = sum(1 for caps in grants.values() for g in caps.values()
                          if g.get("decision") == "allow_always")
            lines.append(_count_line("🔐 Permissions", allowed, "standing grant(s)",
                                     f"across {len(grants)} plugin(s)"))
        except Exception as exc:
            lines.append(f"🔐 Permissions: <span style='color:{_WARN}'>unavailable ({exc})</span>")

        try:
            from eli.plugins.mcp import list_servers
            servers = list_servers()
            on = sum(1 for srv in servers if srv.get("enabled"))
            verified = sum(1 for srv in servers if srv.get("verified"))
            tools = sum(len(srv.get("tools") or []) for srv in servers if srv.get("enabled"))
            lines.append(_count_line(
                "🔌 MCP servers", len(servers), "configured",
                f"{on} enabled · {verified} verified · {tools} tool(s) available"))
        except Exception as exc:
            lines.append(f"🔌 MCP servers: <span style='color:{_WARN}'>unavailable ({exc})</span>")

        try:
            from eli.plugins.marketplace import list_registries
            regs = list_registries()
            on = sum(1 for r in regs if r.get("enabled", True))
            lines.append(_count_line("🌐 Sources", on, "enabled",
                                     f"of {len(regs)} configured"))
        except Exception as exc:
            lines.append(f"🌐 Sources: <span style='color:{_WARN}'>unavailable ({exc})</span>")

        lines.append(f"🔎 Malware scanners: {_scanner_summary()}")
        self._ov_status.setText("<br>".join(lines))

        # Containment. Never optimistic — if this machine cannot contain a child
        # process, the overview says so in the same place it says everything else.
        try:
            from eli.plugins import subprocess_sandbox
            caps = subprocess_sandbox.capabilities()
            good = caps["network_isolation"] or caps["filesystem_isolation"]
            colour = _OK if (caps["network_isolation"] and caps["filesystem_isolation"]) \
                else (_WARN if good else _BAD)
            parts = [f"<span style='color:{colour}'>{subprocess_sandbox.describe(False)}</span>"]
            parts.append(
                "<span style='color:%s'>Network isolation: %s · Filesystem isolation: %s "
                "(%s)</span>" % (
                    _DIM,
                    "yes" if caps["network_isolation"] else "no",
                    "yes" if caps["filesystem_isolation"] else "no",
                    caps.get("platform", "?")))
            for note in caps.get("notes") or []:
                parts.append(f"<span style='color:{_WARN}'>{note}</span>")
            parts.append(
                f"<span style='color:{_DIM}'>This applies to MCP servers and to any "
                f"subprocess a plugin is allowed to start. Python plugins run inside "
                f"ELI and are held by the permission gate instead.</span>")
            self._ov_sandbox.setText("<br>".join(parts))
        except Exception as exc:
            self._ov_sandbox.setText(
                f"<span style='color:{_WARN}'>Sandbox status unavailable: {exc}</span>")

        # A link to a website nobody has configured is a dead end; say which it is.
        try:
            from eli.plugins.marketplace import marketplace_home
            home = marketplace_home()
        except Exception:
            home = ""
        self._ov_home_btn.setText(
            f"🌐 Marketplace website — {home}" if home
            else "🌐 Marketplace website — none set yet")

    # ── opening things ────────────────────────────────────────────────────────
    def _open_path(self, path) -> None:
        """Open a local file with whatever the OS uses for it."""
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:
            QMessageBox.warning(self, "Extensions", f"Could not open {path}:\n{exc}")

    def _open_url(self, url: str) -> None:
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            webbrowser.open(url)

    def _open_mcp_web_page(self) -> None:
        """ELI's own /mcp page — but only if the server it lives in is running.

        Opening a browser at a dead port and letting the user conclude MCP is broken
        is worse than saying plainly that the web server is off.
        """
        try:
            # server_util, not api.server: this only needs the port number, and
            # importing api.server would drag FastAPI in on an install that has none.
            from eli.runtime.server_util import effective_api_port
            port = int(effective_api_port())
        except Exception as exc:
            QMessageBox.information(
                self, "MCP web page",
                f"ELI's web server is not available in this install.\n\n{exc}")
            return
        running = False
        try:
            from eli.runtime.server_util import probe_eli_server
            running = bool(probe_eli_server(port).get("in_use"))
        except Exception:
            log.debug("[EXT] could not probe the web server", exc_info=True)
        if not running:
            QMessageBox.information(
                self, "MCP web page",
                f"ELI's web server is not running, so http://127.0.0.1:{port}/mcp has "
                f"nothing to answer with.\n\nStart it from Settings ▸ Server "
                f"(\"Start server\"), then come back.")
            return
        self._open_url(f"http://127.0.0.1:{port}/mcp")

    # ── browse ────────────────────────────────────────────────────────────────
    def _page_browse(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search plugins…")
        self._search.returnPressed.connect(self._do_browse)
        top.addWidget(self._search, 1)
        b = QPushButton("Refresh")
        b.clicked.connect(self._do_browse)
        top.addWidget(b)
        v.addLayout(top)

        self._browse_status = QLabel("Press Refresh to load community registries.")
        self._browse_status.setStyleSheet(f"color:{_DIM};")
        self._browse_status.setWordWrap(True)
        v.addWidget(self._browse_status)

        self._busy = QProgressBar()
        self._busy.setRange(0, 0)
        self._busy.setVisible(False)
        v.addWidget(self._busy)

        split = QSplitter(Qt.Orientation.Vertical)
        self._browse_table = QTableWidget(0, 7)
        self._browse_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Publisher", "Version", "Price", "Permissions", "Verified"])
        self._browse_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._browse_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._browse_table.itemSelectionChanged.connect(self._show_listing)
        self._browse_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        split.addWidget(self._browse_table)

        self._browse_detail = QTextEdit()
        self._browse_detail.setReadOnly(True)
        split.addWidget(self._browse_detail)
        split.setSizes([260, 240])
        v.addWidget(split, 1)

        row = QHBoxLayout()
        self._install_btn = QPushButton("⬇ Install")
        self._install_btn.setToolTip(
            "Download, verify, scan and install. Stops to ask only if there is "
            "something to decide — permissions, a scan finding, or a paid listing.")
        self._install_btn.setStyleSheet("font-weight:bold;")
        self._install_btn.clicked.connect(self._quick_install_selected)
        row.addWidget(self._install_btn)
        self._inspect_btn = QPushButton("🔍 Check this plugin")
        self._inspect_btn.setToolTip("Download to memory, verify, and run every malware "
                                     "scanner. Installs nothing.")
        self._inspect_btn.clicked.connect(self._inspect_selected)
        row.addWidget(self._inspect_btn)
        self._licence_btn = QPushButton("Enter licence key…")
        self._licence_btn.clicked.connect(self._enter_licence)
        row.addWidget(self._licence_btn)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _set_busy(self, busy: bool) -> None:
        self._busy.setVisible(busy)
        self._inspect_btn.setEnabled(not busy)
        if hasattr(self, "_install_btn"):
            self._install_btn.setEnabled(not busy)

    def _run_async(self, fn, on_done, on_fail=None) -> None:
        """One background call at a time, with the failure reported where it started.

        `on_fail` matters because the pages share this worker: a failure used to be
        written to the Browse status line no matter which page asked, so an MCP
        handshake that blew up left the operator on a page that said nothing at all.
        """
        if self._thread is not None:
            self._note_busy()
            return
        self._thread = QThread(self)
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        report = on_fail or (lambda msg: self._browse_status.setText(
            f"<span style='color:{_BAD}'>{msg}</span>"))

        def _finish(result):
            self._teardown()
            on_done(result)

        def _fail(msg):
            self._teardown()
            report(msg)

        self._worker.done.connect(_finish)
        self._worker.failed.connect(_fail)
        self._set_busy(True)
        self._thread.start()

    def _note_busy(self) -> None:
        """Refuse a second background call out loud rather than by doing nothing."""
        QMessageBox.information(
            self, "Extensions",
            "Something is already running — a download, a scan, or an MCP handshake. "
            "Wait for it to finish and try again.")

    def _teardown(self) -> None:
        self._set_busy(False)
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None

    def _do_browse(self) -> None:
        from eli.plugins.marketplace import browse
        q = self._search.text().strip()
        self._browse_status.setText("Loading…")
        self._run_async(lambda: browse(), lambda res: self._fill_browse(res, q))

    def _fill_browse(self, res: Dict[str, Any], query: str) -> None:
        items = res["listings"]
        if query:
            ql = query.lower()
            items = [m for m in items if ql in str(m.get("name", "")).lower()
                     or ql in str(m.get("description", "")).lower()]
        self._listings = items

        self._browse_table.setRowCount(len(items))
        for r, m in enumerate(items):
            perms = m.get("permissions") or []
            risk = m.get("risk", "low")
            price = float(m.get("price") or 0)
            if m.get("signed"):
                verified, colour = "signed", _OK
            elif m.get("pinned"):
                verified, colour = "checksum only", _WARN
            else:
                verified, colour = "unverified", _BAD
            cells = [
                str(m.get("name", m.get("id", "?"))),
                "MCP" if m.get("kind") == "mcp" else "plugin",
                str(m.get("author", "unknown")),
                str(m.get("version", "?")),
                ("free" if price <= 0 else f"{price:g} {m.get('currency', 'EUR')}"),
                (f"{len(perms)} ({risk})" if perms else "none"),
                verified,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 5 and perms:
                    item.setToolTip("\n".join(perms))
                    item.setForeground(QColor(_RISK.get(risk, _DIM)))
                if c == 6:
                    # The verification state was being computed and then thrown away,
                    # so "signed" and "unverified" read identically in a long list —
                    # exactly the distinction the operator is scanning the column for.
                    item.setForeground(QColor(colour))
                self._browse_table.setItem(r, c, item)

        errs = res.get("errors") or []
        msg = f"{len(items)} plugin(s) from {len(set(m.get('registry') for m in items))} source(s)."
        if errs:
            offline = any(e.get("offline") for e in errs)
            msg += ("  " + ("ELI is offline — only built-in plugins are listed."
                            if offline else
                            "Some sources failed: "
                            + "; ".join(f"{e['registry']}: {e['error']}" for e in errs[:2])))
        self._browse_status.setText(msg)

    def _selected_listing(self) -> Optional[Dict[str, Any]]:
        r = self._browse_table.currentRow()
        if r < 0 or r >= len(self._listings):
            return None
        return self._listings[r]

    def _show_listing(self) -> None:
        m = self._selected_listing()
        if not m:
            return
        from eli.plugins.permissions import describe
        perms = m.get("permissions") or []
        lines = [
            f"<h3>{m.get('name')}</h3>",
            f"<p>{m.get('description', '')}</p>",
            f"<p><b>Publisher:</b> {m.get('author', 'unknown')} &nbsp; "
            f"<b>Licence:</b> {m.get('license', '?')} &nbsp; "
            f"<b>Source:</b> {m.get('registry_label', m.get('registry'))}</p>",
        ]
        if perms:
            lines.append("<p><b>Requests permission to:</b></p><ul>")
            for p in perms:
                d = describe(p)
                lines.append(f"<li><span style='color:{_RISK.get(d['risk'], _BAD)}'>"
                             f"{d['title']}</span> — {d['detail']}</li>")
            lines.append("</ul>")
        else:
            lines.append("<p>Requests no special permissions.</p>")
        if m.get("listing_problems"):
            lines.append(f"<p style='color:{_BAD}'><b>Listing problems:</b> "
                         + "; ".join(m["listing_problems"]) + "</p>")
        if m.get("listing_warnings"):
            lines.append(f"<p style='color:{_WARN}'>"
                         + "; ".join(m["listing_warnings"]) + "</p>")
        lines.append(f"<p style='color:{_DIM}'>Nothing has been downloaded yet. "
                     f"Use “Check this plugin” to verify and scan it.</p>")
        self._browse_detail.setHtml("".join(lines))

    def _enter_licence(self) -> None:
        m = self._selected_listing()
        if not m:
            return
        from eli.plugins.marketplace import set_licence_key
        key, ok = QInputDialog.getText(
            self, "Licence key",
            f"Licence key for '{m.get('name')}'.\n\n"
            f"ELI does not process payments and cannot confirm a purchase — the key is "
            f"passed to the publisher's own server when downloading.")
        if ok and key.strip():
            set_licence_key(m["id"], key.strip())
            QMessageBox.information(self, "Marketplace", "Licence key saved.")

    def _quick_install_selected(self) -> None:
        """One click. Verification and scanning still happen — they just do not
        require the operator to drive them when there is nothing to decide."""
        m = self._selected_listing()
        if not m:
            return
        if m.get("kind") == "mcp":
            self._install_mcp(m)
            return
        from eli.plugins.marketplace import quick_install
        self._browse_status.setText(
            f"Verifying and scanning {m.get('name')} — nothing is written to disk "
            f"until that finishes…")
        self._run_async(lambda: quick_install(m), lambda r: self._after_quick(m, r))

    def _after_quick(self, listing: Dict[str, Any], result: Dict[str, Any]) -> None:
        if result.get("review_needed"):
            # Not a failure — just something the operator has to see.
            self._browse_status.setText(
                "Needs your decision: " + "; ".join(result["reasons"]))
            dlg = InstallReviewDialog(listing, result["preview"], parent=self)
            if dlg.exec() and dlg.approved:
                self._do_install(listing, allow_pip=dlg.allow_pip)
            return
        if not result.get("ok"):
            problems = (result.get("result") or {}).get("problems") or ["Install failed."]
            QMessageBox.warning(self, "Marketplace", "\n\n".join(problems))
            return

        self._browse_status.setText("Installed.")
        self._refresh_installed()
        pid = (result.get("result") or {}).get("plugin_id") or listing.get("id")
        notes = result.get("notes") or []
        note_text = ("\n\nWorth knowing:\n  · " + "\n  · ".join(notes)) if notes else ""
        enable = QMessageBox.question(
            self, "Installed",
            f"'{listing.get('name')}' matches the checksum in its listing, scanned "
            f"clean, and asks for no permissions.{note_text}\n\n"
            f"It is installed but switched OFF. Enable it now?")
        if enable == QMessageBox.StandardButton.Yes:
            try:
                from eli.plugins.manager import get_manager
                get_manager().enable(pid)
                self._refresh_installed()
            except Exception as exc:
                QMessageBox.warning(self, "Marketplace", f"Could not enable: {exc}")

    def _install_mcp(self, listing: Dict[str, Any]) -> None:
        """MCP servers install differently: there is no source for ELI to scan, so
        what gets checked is that the runtime exists and the server really answers."""
        from eli.plugins.mcp import network_caveat
        spec = listing.get("mcp") or {}
        proceed = QMessageBox.question(
            self, "Add MCP server",
            f"Add '{listing.get('name')}'?\n\n"
            f"Command: {spec.get('command', '?')} {' '.join(spec.get('args') or [])}\n\n"
            f"{network_caveat()}\n\n"
            f"ELI will check the required runtime is installed and that the server "
            f"answers before saving anything.")
        if proceed != QMessageBox.StandardButton.Yes:
            return
        from eli.plugins.marketplace import install_mcp
        self._browse_status.setText(f"Starting {listing.get('name')} to verify it…")
        self._run_async(lambda: install_mcp(listing), self._after_mcp)

    def _after_mcp(self, result: Dict[str, Any]) -> None:
        if not result.get("ok"):
            QMessageBox.warning(
                self, "MCP server",
                "\n\n".join(result.get("problems") or ["Could not add the server."]))
            self._browse_status.setText("MCP server not added.")
            return
        tools = ", ".join(t["name"] for t in (result.get("tools") or [])[:8]) or "none"
        QMessageBox.information(
            self, "MCP server",
            f"{result.get('response')}\n\nTools offered: {tools}")
        self._browse_status.setText("MCP server added.")
        self._refresh_mcp()

    def _inspect_selected(self) -> None:
        m = self._selected_listing()
        if not m:
            return
        from eli.plugins.marketplace import preview_install
        self._browse_status.setText(f"Downloading and scanning {m.get('name')}…")
        self._run_async(lambda: preview_install(m), lambda p: self._show_preview(m, p))

    def _show_preview(self, listing: Dict[str, Any], preview: Dict[str, Any]) -> None:
        self._browse_status.setText("Scan complete.")
        dlg = InstallReviewDialog(listing, preview, parent=self)
        if dlg.exec() and dlg.approved:
            self._do_install(listing, allow_pip=dlg.allow_pip)

    def _do_install(self, listing: Dict[str, Any], *, allow_pip: bool) -> None:
        from eli.plugins.marketplace import install
        res = install(listing, confirm=lambda _p: True, allow_pip=allow_pip)
        if res.get("ok"):
            QMessageBox.information(self, "Marketplace", res["response"])
            self._refresh_installed()
        else:
            QMessageBox.warning(self, "Marketplace",
                                "\n\n".join(res.get("problems") or ["Install failed."]))

    # ── installed ─────────────────────────────────────────────────────────────
    def _page_installed(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self._inst_table = QTableWidget(0, 5)
        self._inst_table.setHorizontalHeaderLabels(
            ["Plugin", "Version", "Enabled", "Verification", "Granted permissions"])
        self._inst_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._inst_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._inst_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._inst_table, 1)

        row = QHBoxLayout()
        for label, slot in (("Enable", self._enable), ("Disable", self._disable),
                            ("Rescan", self._rescan), ("Uninstall", self._uninstall)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        r = QPushButton("Refresh")
        r.clicked.connect(self._refresh_installed)
        row.addWidget(r)
        v.addLayout(row)
        return w

    def _refresh_installed(self) -> None:
        try:
            from eli.plugins.manager import get_manager, _plugins_dir
            from eli.plugins.permissions import store
            rows = get_manager().list_installed()
        except Exception as exc:
            log.debug(f"[MARKET] could not list installed plugins: {exc}", exc_info=True)
            return
        from pathlib import Path
        grants = store().all_grants()
        self._inst_table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            pid = p["id"]
            integrity = "built-in"
            try:
                f = Path(_plugins_dir()) / pid / ".integrity.json"
                if f.is_file():
                    d = json.loads(f.read_text(encoding="utf-8"))
                    integrity = f"{d.get('status', '?')} · scan {d.get('scan_verdict', '?')}"
            except Exception:
                log.debug("[MARKET] unreadable integrity sidecar", exc_info=True)
            allowed = [c for c, g in (grants.get(pid) or {}).items()
                       if g.get("decision") == "allow_always"]
            for c, text in enumerate([pid, p.get("version", "?"),
                                      "yes" if p.get("enabled") else "no",
                                      integrity,
                                      ", ".join(allowed) or "none"]):
                self._inst_table.setItem(r, c, QTableWidgetItem(str(text)))
        self._inst_table.resizeColumnsToContents()
        self._inst_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)

    def _selected_installed(self) -> Optional[str]:
        r = self._inst_table.currentRow()
        item = self._inst_table.item(r, 0) if r >= 0 else None
        return item.text() if item else None

    def _enable(self) -> None:
        pid = self._selected_installed()
        if not pid:
            return
        if QMessageBox.question(
                self, "Marketplace",
                f"Enable '{pid}'?\n\nIt will run inside ELI. It still has to ask before "
                f"using any permission.") != QMessageBox.StandardButton.Yes:
            return
        from eli.plugins.manager import get_manager
        get_manager().enable(pid)
        self._refresh_installed()

    def _disable(self) -> None:
        pid = self._selected_installed()
        if not pid:
            return
        from eli.plugins.manager import get_manager
        get_manager().disable(pid)
        self._refresh_installed()

    def _rescan(self) -> None:
        pid = self._selected_installed()
        if not pid:
            return
        from pathlib import Path
        from eli.plugins.manager import _plugins_dir
        from eli.plugins.security_scan import scan_file
        path = Path(_plugins_dir()) / pid / "plugin.py"
        if not path.is_file():
            QMessageBox.warning(self, "Marketplace", f"No source found for '{pid}'.")
            return
        res = scan_file(path, deep=True)
        ScanReportDialog(pid, res, parent=self).exec()

    def _uninstall(self) -> None:
        pid = self._selected_installed()
        if not pid:
            return
        if QMessageBox.question(
                self, "Marketplace",
                f"Uninstall '{pid}' and forget its permissions?") \
                != QMessageBox.StandardButton.Yes:
            return
        from eli.plugins.manager import get_manager
        from eli.plugins.permissions import store
        get_manager().uninstall(pid)
        store().revoke(pid)
        self._refresh_installed()
        self._refresh_permissions()

    # ── permissions ───────────────────────────────────────────────────────────
    def _page_permissions(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Every permission you have granted or refused. Revoking one means the plugin "
            "will be asked about again the next time it needs it."))
        self._perm_table = QTableWidget(0, 4)
        self._perm_table.setHorizontalHeaderLabels(["Plugin", "Permission", "Decision", "When"])
        self._perm_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._perm_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._perm_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._perm_table, 1)

        row = QHBoxLayout()
        rb = QPushButton("Revoke selected")
        rb.clicked.connect(self._revoke)
        row.addWidget(rb)
        ab = QPushButton("View activity log")
        ab.clicked.connect(self._show_audit)
        row.addWidget(ab)
        row.addStretch(1)
        r = QPushButton("Refresh")
        r.clicked.connect(self._refresh_permissions)
        row.addWidget(r)
        v.addLayout(row)
        return w

    def _refresh_permissions(self) -> None:
        from eli.plugins.permissions import store, describe
        grants = store().all_grants()
        rows = [(pid, cap, g.get("decision", "?"), g.get("at", ""))
                for pid, caps in grants.items() for cap, g in caps.items()]
        self._perm_table.setRowCount(len(rows))
        for r, (pid, cap, decision, at) in enumerate(rows):
            cells = [pid, describe(cap)["title"], decision.replace("_", " "), at]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c == 2:
                    item.setForeground(Qt.GlobalColor.green if "allow" in decision
                                       else Qt.GlobalColor.red)
                if c == 1:
                    item.setData(Qt.ItemDataRole.UserRole, cap)
                self._perm_table.setItem(r, c, item)
        self._perm_table.resizeColumnsToContents()
        self._perm_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)

    def _revoke(self) -> None:
        r = self._perm_table.currentRow()
        if r < 0:
            return
        pid = self._perm_table.item(r, 0).text()
        cap = self._perm_table.item(r, 1).data(Qt.ItemDataRole.UserRole)
        from eli.plugins.permissions import store
        store().revoke(pid, cap)
        self._refresh_permissions()

    def _show_audit(self) -> None:
        from eli.plugins.permissions import store
        entries = store().audit_tail(400)
        dlg = QDialog(self)
        dlg.setWindowTitle("Plugin permission activity")
        dlg.setMinimumSize(640, 420)
        lay = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setStyleSheet("font-family:monospace;font-size:11px;")
        te.setPlainText("\n".join(
            f"{e.get('at')}  {e.get('plugin'):20s} {e.get('capability'):18s} "
            f"{e.get('decision')}" for e in entries) or "No activity recorded yet.")
        lay.addWidget(te)
        dlg.exec()

    # ── MCP servers ───────────────────────────────────────────────────────────
    def _page_mcp(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        # The caveat is derived from what this machine can actually enforce rather
        # than asserted. It used to say flatly that ELI could not contain a server;
        # that is still true without a sandbox and plainly false on a box with
        # bubblewrap, and a warning that is wrong half the time gets ignored.
        try:
            from eli.plugins.mcp import network_caveat
            caveat = network_caveat(False)
        except Exception:
            caveat = ("An MCP server runs as its own program, outside ELI's own process, "
                      "and ELI cannot see what it sends.")
        intro = QLabel(
            "<b>MCP servers run as their own programs alongside ELI.</b><br>"
            f"<span style='color:{_WARN}'>{caveat}</span> "
            f"<span style='color:{_DIM}'>What ELI does guarantee is that a server here "
            f"actually starts and answers — it is verified with a real handshake before "
            f"being saved.</span>")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self._mcp_table = QTableWidget(0, 5)
        self._mcp_table.setHorizontalHeaderLabels(
            ["Server", "Command", "Enabled", "Verified", "Tools"])
        self._mcp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._mcp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._mcp_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._mcp_table, 1)

        row = QHBoxLayout()
        add = QPushButton("➕ Add server…")
        add.setStyleSheet("font-weight:bold;")
        add.setToolTip("Paste a config or fill in the command. ELI runs the handshake "
                       "before saving, so nothing lands in the config that does not work.")
        add.clicked.connect(self._mcp_add)
        row.addWidget(add)
        for label, slot in (("Enable", lambda: self._mcp_enable(True)),
                            ("Disable", lambda: self._mcp_enable(False)),
                            ("Re-check", self._mcp_doctor),
                            ("Remove", self._mcp_remove)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        r = QPushButton("Refresh")
        r.clicked.connect(self._refresh_mcp)
        row.addWidget(r)
        v.addLayout(row)

        self._mcp_status = QLabel("")
        self._mcp_status.setWordWrap(True)
        self._mcp_status.setStyleSheet(f"color:{_DIM};")
        v.addWidget(self._mcp_status)

        v.addWidget(QLabel(
            "<b>Use ELI from other apps.</b> ELI can act as an MCP server, so a client "
            "such as an editor or desktop assistant can call ELI's actions. Paste this "
            "into that app's MCP servers config."))
        self._mcp_scope = QComboBox()
        self._mcp_scope.addItem("Safe actions only (recommended)", False)
        self._mcp_scope.addItem("Include control actions (mouse, keyboard, shell, self-change)", True)
        self._mcp_scope.currentIndexChanged.connect(self._refresh_mcp_config)
        v.addWidget(self._mcp_scope)
        self._mcp_cfg = QTextEdit()
        self._mcp_cfg.setReadOnly(True)
        self._mcp_cfg.setMaximumHeight(140)
        v.addWidget(self._mcp_cfg)
        copy_row = QHBoxLayout()
        copy_btn = QPushButton("Copy config")
        copy_btn.clicked.connect(self._copy_mcp_config)
        copy_row.addWidget(copy_btn)
        copy_row.addStretch(1)
        v.addLayout(copy_row)
        self._refresh_mcp_config()
        return w

    def _refresh_mcp_config(self) -> None:
        try:
            from eli.integrations.mcp.server import connection_config
            allow = bool(self._mcp_scope.currentData())
            self._mcp_cfg.setPlainText(json.dumps(connection_config(allow), indent=2))
        except Exception as exc:
            log.debug(f"[MARKET] MCP server config unavailable: {exc}", exc_info=True)
            self._mcp_cfg.setPlainText("")

    def _copy_mcp_config(self) -> None:
        self._mcp_cfg.selectAll()
        self._mcp_cfg.copy()
        self._mcp_status.setText("Config copied.")

    def _mcp_add(self) -> None:
        """Add a server: collect it, verify it, then write it — in that order.

        The verification runs off the GUI thread because a cold `npx` fetch can take
        the better part of a minute, and a frozen window during it reads as a crash.
        """
        if self._thread is not None:
            # Checked before the dialog rather than after it, so nobody fills in a
            # form only to be told the worker was busy all along.
            self._note_busy()
            return
        dlg = AddMcpServerDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry = dlg.entry()
        if not entry:
            return
        from eli.plugins.mcp import install_server, network_caveat
        allow_network = "network" in (entry.get("permissions") or [])
        if QMessageBox.question(
                self, "Add MCP server",
                f"Add '{entry.get('id')}'?\n\n{network_caveat(allow_network)}\n\n"
                f"ELI will start it once to check it answers. It is saved switched OFF "
                f"either way.") != QMessageBox.StandardButton.Yes:
            return
        self._mcp_status.setText(
            f"Starting '{entry.get('id')}' and waiting for the handshake…")
        self._run_async(lambda: install_server(entry, verify=True, enable=False),
                        self._after_mcp_add, on_fail=self._mcp_failed)

    def _mcp_failed(self, message: str) -> None:
        self._mcp_status.setText(f"<span style='color:{_BAD}'>{message}</span>")
        self._mcp_status.setTextFormat(Qt.TextFormat.RichText)

    def _after_mcp_add(self, result: Dict[str, Any]) -> None:
        if not result.get("ok"):
            stage = result.get("stage", "install")
            problems = "\n".join(result.get("problems") or ["Unknown failure."])
            QMessageBox.warning(
                self, "MCP server not added",
                f"Nothing was written to the config.\n\nFailed at: {stage}\n\n{problems}")
            self._mcp_status.setText(
                f"<span style='color:{_BAD}'>{stage}: {problems}</span>")
            self._mcp_status.setTextFormat(Qt.TextFormat.RichText)
            return
        QMessageBox.information(
            self, "MCP server added",
            result.get("response")
            or f"'{result.get('id')}' was added and verified. It is switched OFF.")
        self._refresh_mcp()
        self._refresh_overview()

    def _refresh_mcp(self) -> None:
        try:
            from eli.plugins.mcp import config_path, list_servers
            servers = list_servers()
        except Exception as exc:
            log.debug(f"[MARKET] MCP unavailable: {exc}", exc_info=True)
            return
        self._mcp_table.setRowCount(len(servers))
        for r, srv in enumerate(servers):
            cmd = srv.get("url") or " ".join(
                [str(srv.get("command", ""))] + [str(a) for a in (srv.get("args") or [])])
            cells = [srv["id"], cmd, "yes" if srv.get("enabled") else "no",
                     "yes" if srv.get("verified") else "no",
                     str(len(srv.get("tools") or []))]
            for c, text in enumerate(cells):
                self._mcp_table.setItem(r, c, QTableWidgetItem(text))
        if servers:
            self._mcp_status.setText(f"Config: {config_path()}")
        else:
            # An empty grid tells a first-time operator nothing. Say what is missing
            # and which button fixes it.
            self._mcp_status.setText(
                f"<span style='color:{_DIM}'>No MCP servers configured. "
                f"<b>➕ Add server…</b> takes a config pasted from a server's README, or "
                f"a command you type in. Config lives at {config_path()}.</span>")
        self._mcp_status.setTextFormat(Qt.TextFormat.RichText)

    def _selected_mcp(self) -> Optional[str]:
        r = self._mcp_table.currentRow()
        item = self._mcp_table.item(r, 0) if r >= 0 else None
        return item.text() if item else None

    def _mcp_enable(self, enabled: bool) -> None:
        sid = self._selected_mcp()
        if not sid:
            return
        from eli.plugins.mcp import network_caveat, set_enabled
        if enabled and QMessageBox.question(
                self, "Enable MCP server",
                f"Enable '{sid}'?\n\n{network_caveat()}") \
                != QMessageBox.StandardButton.Yes:
            return
        set_enabled(sid, enabled)
        self._refresh_mcp()

    def _mcp_remove(self) -> None:
        sid = self._selected_mcp()
        if not sid:
            return
        from eli.plugins.mcp import remove_server
        remove_server(sid)
        self._refresh_mcp()

    def _mcp_doctor(self) -> None:
        from eli.plugins.mcp import doctor
        self._mcp_status.setText("Checking every configured server…")
        self._run_async(lambda: doctor(), self._show_doctor, on_fail=self._mcp_failed)

    def _show_doctor(self, report: Dict[str, Any]) -> None:
        lines = [report.get("summary", "")]
        for srv in report.get("servers", []):
            state = (f"<span style='color:{_OK}'>ok, {srv['tools']} tool(s)</span>"
                     if srv["ok"] else
                     f"<span style='color:{_BAD}'>{srv['problem']}</span>")
            lines.append(f"<b>{srv['id']}</b>: {state}")
        self._mcp_status.setText("<br>".join(lines))
        self._mcp_status.setTextFormat(Qt.TextFormat.RichText)
        self._refresh_mcp()

    # ── registries ────────────────────────────────────────────────────────────
    def _page_registries(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Community registries this machine will install from. Adding one is a trust "
            "decision: its listings become installable. ELI ships with none — the built-in "
            "entry is just ELI's own bundled plugins."))

        home_row = QHBoxLayout()
        home_row.addWidget(QLabel("Marketplace website:"))
        self._home_edit = QLineEdit()
        self._home_edit.setPlaceholderText(
            "https://…  (ELI ships no default — the marketplace is the community's)")
        home_row.addWidget(self._home_edit, 1)
        save_home = QPushButton("Save")
        save_home.clicked.connect(self._save_home)
        home_row.addWidget(save_home)
        self._open_home = QPushButton("Open in browser")
        self._open_home.setToolTip("Discovery only — a website can never install "
                                   "anything. It gives you a source URL to add here.")
        self._open_home.clicked.connect(self._open_marketplace)
        home_row.addWidget(self._open_home)
        v.addLayout(home_row)
        self._reg_table = QTableWidget(0, 4)
        self._reg_table.setHorizontalHeaderLabels(["Id", "Label", "URL", "Enabled"])
        self._reg_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._reg_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._reg_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._reg_table, 1)

        form = QHBoxLayout()
        self._reg_id = QLineEdit()
        self._reg_id.setPlaceholderText("id")
        self._reg_url = QLineEdit()
        self._reg_url.setPlaceholderText("https://…/index.json")
        form.addWidget(self._reg_id)
        form.addWidget(self._reg_url, 1)
        add = QPushButton("Add source")
        add.clicked.connect(self._add_registry)
        form.addWidget(add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove_registry)
        form.addWidget(rm)
        v.addLayout(form)
        return w

    def _save_home(self) -> None:
        from eli.plugins.marketplace import set_marketplace_home
        res = set_marketplace_home(self._home_edit.text())
        if not res["ok"]:
            QMessageBox.warning(self, "Marketplace", "\n".join(res["problems"]))
            return
        if res.get("warnings"):
            QMessageBox.warning(self, "Marketplace", "\n".join(res["warnings"]))
        self._refresh_registries()

    def _open_marketplace(self) -> None:
        from eli.plugins.marketplace import marketplace_home
        url = marketplace_home()
        if not url:
            # Reached from Overview as well as Sources, so it names where to set it
            # rather than saying "above" and being wrong half the time.
            QMessageBox.information(
                self, "Marketplace",
                "No marketplace website is set.\n\nELI ships without one on purpose — "
                "the marketplace belongs to the community, not to ELI's author. Set the "
                "address of one you trust on the Sources page, or add a registry URL "
                "there directly.")
            self.open_page("sources")
            return
        self._open_url(url)

    def _refresh_registries(self) -> None:
        from eli.plugins.marketplace import list_registries, marketplace_home
        self._home_edit.setText(marketplace_home())
        regs = list_registries()
        self._reg_table.setRowCount(len(regs))
        for r, reg in enumerate(regs):
            for c, text in enumerate([reg["id"], reg.get("label", ""), reg.get("url", "—"),
                                      "yes" if reg.get("enabled", True) else "no"]):
                self._reg_table.setItem(r, c, QTableWidgetItem(str(text)))

    def _add_registry(self) -> None:
        from eli.plugins.marketplace import add_registry
        rid = self._reg_id.text().strip()
        url = self._reg_url.text().strip()
        if QMessageBox.question(
                self, "Add source",
                f"Add '{rid}' as a plugin source?\n\n{url}\n\n"
                f"Anything listed there becomes installable on this machine. Only add "
                f"sources you trust.") != QMessageBox.StandardButton.Yes:
            return
        res = add_registry(rid, url)
        if not res["ok"]:
            QMessageBox.warning(self, "Add source", "\n".join(res["problems"]))
            return
        if res.get("warnings"):
            QMessageBox.warning(self, "Add source", "\n".join(res["warnings"]))
        self._reg_id.clear()
        self._reg_url.clear()
        self._refresh_registries()

    def _remove_registry(self) -> None:
        r = self._reg_table.currentRow()
        if r < 0:
            return
        from eli.plugins.marketplace import remove_registry
        res = remove_registry(self._reg_table.item(r, 0).text())
        if not res["ok"]:
            QMessageBox.warning(self, "Sources", "\n".join(res["problems"]))
        self._refresh_registries()


    # ── settings ──────────────────────────────────────────────────────────────
    def _page_settings(self) -> QWidget:
        """The knobs that change how everything else on this tab behaves.

        Every control here is wired to something that reads it. There is deliberately
        no "run fewer security checks" switch: the scan on install is the only thing
        standing between a listing and this machine, and a toggle that turns it off
        would exist only to be turned off.
        """
        outer = QWidget()
        outer_v = QVBoxLayout(outer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)

        # ── MCP behaviour ──────────────────────────────────────────────────────
        mcp_box = QGroupBox("MCP")
        form = QFormLayout(mcp_box)

        self._set_timeout = QSpinBox()
        self._set_timeout.setRange(5, 300)
        self._set_timeout.setSuffix(" s")
        self._set_timeout.setMaximumWidth(140)
        self._set_timeout.setToolTip(
            "How long a server gets to start and answer the handshake, both when you "
            "add it and when ELI first uses it. A resident binary answers instantly; "
            "'npx some-server' on a cold npm cache can take a minute. Raise this if a "
            "server that works from a terminal times out here.")
        tl = QLabel("Server start timeout")
        tl.setToolTip(self._set_timeout.toolTip())
        form.addRow(tl, self._set_timeout)

        self._set_expose = QCheckBox("Expose ELI's control actions over MCP")
        self._set_expose.setToolTip(
            "ELI can act as an MCP server itself (python -m eli.integrations.mcp.server), "
            "offering its own actions to any client. Actions that drive the machine, run "
            "code, or change ELI are withheld unless you turn this on — that is handing "
            "an external client the mouse, the shell and the self-upgrade path.")
        form.addRow(self._set_expose)
        warn = QLabel(
            "<span style='color:%s'>Leaving that off is the safe default. It affects "
            "ELI-as-a-server only, not the servers you add on this tab.</span>" % _DIM)
        warn.setWordWrap(True)
        form.addRow(warn)

        save = QPushButton("💾 Save")
        save.clicked.connect(self._save_settings)
        self._settings_status = QLabel("")
        self._settings_status.setStyleSheet(f"color:{_DIM};")
        row = QHBoxLayout()
        row.addWidget(save)
        row.addWidget(self._settings_status, 1)
        form.addRow(row)
        v.addWidget(mcp_box)

        # ── where everything lives ─────────────────────────────────────────────
        files = QGroupBox("Files on this machine")
        fv = QVBoxLayout(files)
        note = QLabel(
            "The config ELI actually reads — not a copy, not a template. Editing one by "
            "hand is supported; press Refresh on the relevant page afterwards.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_DIM};")
        fv.addWidget(note)
        for label, getter in _CONFIG_LOCATIONS:
            fv.addLayout(self._path_row(label, getter))
        v.addWidget(files)

        # ── containment, again, where someone changing settings will read it ───
        sandbox = QGroupBox("Sandbox")
        sv = QVBoxLayout(sandbox)
        self._set_sandbox = QLabel("…")
        self._set_sandbox.setWordWrap(True)
        self._set_sandbox.setTextFormat(Qt.TextFormat.RichText)
        sv.addWidget(self._set_sandbox)
        v.addWidget(sandbox)

        # ── taking it all back ─────────────────────────────────────────────────
        danger = QGroupBox("Reset")
        dv = QVBoxLayout(danger)
        for label, tip, slot in (
            ("Forget session-only grants",
             "Drops every 'allow once' grant made since ELI started. Standing grants "
             "are untouched.", self._clear_session_grants),
            ("Revoke every plugin permission",
             "Every plugin is asked again the next time it needs anything. Nothing is "
             "uninstalled and nothing is disabled.", self._revoke_all_permissions),
            ("Disable every MCP server",
             "Leaves them configured and verified, but none of them reaches ELI's "
             "action surface until you switch it back on.", self._disable_all_mcp),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            brow = QHBoxLayout()
            brow.addWidget(b)
            brow.addStretch(1)
            dv.addLayout(brow)
        v.addWidget(danger)

        v.addStretch(1)
        scroll.setWidget(body)
        outer_v.addWidget(scroll)
        self._load_settings()
        return outer

    def _path_row(self, label: str, getter) -> QHBoxLayout:
        """One config location: what it is, where it is, and whether it exists yet."""
        row = QHBoxLayout()
        try:
            path = Path(getter())
        except Exception as exc:
            lbl = QLabel(f"<b>{label}</b>: <span style='color:{_WARN}'>unavailable ({exc})</span>")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            return row
        exists = path.exists()
        state = "" if exists else f" <span style='color:{_DIM}'>(not created yet)</span>"
        lbl = QLabel(f"<b>{label}</b><br><span style='color:{_DIM}'>{path}</span>{state}")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(lbl, 1)
        b = QPushButton("Open")
        b.setEnabled(exists)
        b.setToolTip(str(path) if exists else "Nothing has written this file yet.")
        b.clicked.connect(lambda _=False, pth=path: self._open_path(pth))
        row.addWidget(b)
        f = QPushButton("Show folder")
        f.clicked.connect(lambda _=False, pth=path: self._open_path(
            pth if pth.is_dir() else pth.parent))
        row.addWidget(f)
        return row

    def _load_settings(self) -> None:
        try:
            from eli.core.runtime_settings import load_settings
            live = load_settings() or {}
        except Exception as exc:
            log.debug(f"[EXT] could not read settings: {exc}", exc_info=True)
            live = {}
        self._set_timeout.setValue(int(live.get("mcp_start_timeout") or 30))
        self._set_expose.setChecked(bool(live.get("mcp_expose_control", False)))
        # The env var overrides the saved value wherever the server runs, so a machine
        # that sets it must not be shown a checkbox that appears to be in charge.
        env = os.environ.get("ELI_MCP_ALLOW_CONTROL")
        if env not in (None, ""):
            # Show what is actually in force, not what is saved underneath it — a
            # greyed-out box displaying the losing value is worse than no box.
            self._set_expose.setChecked(env.strip().lower() in {"1", "true", "yes", "on"})
            self._set_expose.setEnabled(False)
            self._set_expose.setToolTip(
                self._set_expose.toolTip()
                + f"\n\nOverridden right now by ELI_MCP_ALLOW_CONTROL={env} in the "
                  f"environment, which wins over this setting. This box shows what that "
                  f"means in practice; the saved setting is "
                  f"{bool(live.get('mcp_expose_control', False))}.")
        self._refresh_sandbox_label()

    def _refresh_sandbox_label(self) -> None:
        if not hasattr(self, "_set_sandbox"):
            return
        try:
            from eli.plugins import subprocess_sandbox
            caps = subprocess_sandbox.capabilities()
            lines = [subprocess_sandbox.describe(False)]
            for note in caps.get("notes") or []:
                lines.append(f"<span style='color:{_WARN}'>{note}</span>")
            if not caps["network_isolation"] and not caps["filesystem_isolation"]:
                lines.append(
                    f"<span style='color:{_DIM}'>There is nothing to configure here — a "
                    f"sandbox is either available on the platform or it is not. What can "
                    f"be changed is the machine: install bubblewrap and ELI will use it "
                    f"on the next launch, with no setting to turn on.</span>")
            self._set_sandbox.setText("<br>".join(lines))
        except Exception as exc:
            self._set_sandbox.setText(f"Sandbox status unavailable: {exc}")

    def _save_settings(self) -> None:
        try:
            from eli.core.runtime_settings import update_settings
            update_settings(mcp_start_timeout=int(self._set_timeout.value()),
                            mcp_expose_control=bool(self._set_expose.isChecked()))
        except Exception as exc:
            QMessageBox.warning(self, "Extensions", f"Could not save settings:\n{exc}")
            return
        # No restart line here because there is no restart: the timeout is read per
        # server start, and ELI-as-a-server reads the flag per request.
        self._settings_status.setText("Saved. Takes effect on the next server start.")

    def _clear_session_grants(self) -> None:
        try:
            from eli.plugins.permissions import store
            store().clear_session()
        except Exception as exc:
            QMessageBox.warning(self, "Extensions", str(exc))
            return
        self._refresh_permissions()
        self._settings_status.setText("Session-only grants forgotten.")

    def _revoke_all_permissions(self) -> None:
        try:
            from eli.plugins.permissions import store
            st = store()
            plugin_ids = list(st.all_grants())
        except Exception as exc:
            QMessageBox.warning(self, "Extensions", str(exc))
            return
        if not plugin_ids:
            QMessageBox.information(self, "Extensions", "There are no grants to revoke.")
            return
        if QMessageBox.question(
                self, "Revoke everything",
                f"Revoke every permission granted to {len(plugin_ids)} plugin(s)?\n\n"
                f"Nothing is uninstalled or disabled — each one is simply asked again "
                f"the next time it needs something.") != QMessageBox.StandardButton.Yes:
            return
        for pid in plugin_ids:
            st.revoke(pid)
        self._refresh_permissions()
        self._refresh_installed()
        self._settings_status.setText(f"Revoked every grant for {len(plugin_ids)} plugin(s).")

    def _disable_all_mcp(self) -> None:
        try:
            from eli.plugins.mcp import list_servers, set_enabled
            on = [srv["id"] for srv in list_servers() if srv.get("enabled")]
        except Exception as exc:
            QMessageBox.warning(self, "Extensions", str(exc))
            return
        if not on:
            QMessageBox.information(self, "Extensions", "No MCP server is enabled.")
            return
        if QMessageBox.question(
                self, "Disable MCP servers",
                f"Disable {len(on)} enabled MCP server(s)?\n\nThey stay configured and "
                f"verified; their tools simply stop reaching ELI.") \
                != QMessageBox.StandardButton.Yes:
            return
        for sid in on:
            set_enabled(sid, False)
        self._refresh_mcp()
        self._settings_status.setText(f"Disabled {len(on)} MCP server(s).")


class ScanReportDialog(QDialog):
    """Full malware-scan output for one plugin."""

    def __init__(self, plugin_id: str, scan: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Security scan — {plugin_id}")
        self.setMinimumSize(680, 480)
        v = QVBoxLayout(self)
        colour = {"clean": _OK, "suspicious": _WARN, "malicious": _BAD}.get(
            scan.get("verdict"), _DIM)
        v.addWidget(QLabel(
            f"<div style='font-size:15px;color:{colour}'><b>"
            f"{str(scan.get('verdict', '?')).upper()}</b> — risk score "
            f"{scan.get('score', 0)}/100</div>"))
        summary = QLabel(scan.get("summary", ""))
        summary.setWordWrap(True)
        v.addWidget(summary)

        te = QTextEdit()
        te.setReadOnly(True)
        html = ["<h4>Findings</h4>"]
        if not scan.get("findings"):
            html.append("<p>No findings.</p>")
        for f in scan.get("findings", []):
            c = _RISK.get(f["severity"], _DIM)
            line = f" (line {f['line']})" if f.get("line") else ""
            html.append(f"<p><b style='color:{c}'>[{f['severity']}]</b> "
                        f"{f['title']}{line}<br>"
                        f"<span style='color:{_DIM}'>{f['detail']} — {f['engine']}</span></p>")
        html.append("<h4>Engines</h4><ul>")
        for name, info in (scan.get("engines") or {}).items():
            state = (f"ran, {info['findings']} finding(s)" if info.get("ran")
                     else f"<span style='color:{_WARN}'>did not run — "
                          f"{info.get('error', 'unavailable')}</span>")
            html.append(f"<li>{name}: {state}</li>")
        html.append("</ul>")
        te.setHtml("".join(html))
        v.addWidget(te, 1)

        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        v.addWidget(b)


class InstallReviewDialog(QDialog):
    """The decision point: everything ELI checked, then install or don't."""

    def __init__(self, listing: Dict[str, Any], preview: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.approved = False
        self.allow_pip = False
        self._preview = preview
        self.setWindowTitle(f"Install {listing.get('name', '?')}?")
        self.setMinimumSize(620, 520)
        self._build(listing, preview)

    def _build(self, listing: Dict[str, Any], preview: Dict[str, Any]) -> None:
        v = QVBoxLayout(self)
        scan = preview.get("scan") or {}
        blocked = not preview.get("ok")

        verdict = scan.get("verdict", "unknown")
        colour = {"clean": _OK, "suspicious": _WARN, "malicious": _BAD}.get(verdict, _BAD)
        head = QLabel(f"<div style='font-size:16px'><b>{listing.get('name')}</b> "
                      f"<span style='color:{_DIM}'>{listing.get('version', '')} by "
                      f"{listing.get('author', 'unknown')}</span></div>")
        v.addWidget(head)

        te = QTextEdit()
        te.setReadOnly(True)
        html: List[str] = []

        if blocked:
            html.append(f"<div style='color:{_BAD};font-size:14px'><b>ELI refused this "
                        f"plugin at the '{preview.get('stage')}' stage.</b></div>")
            for p in preview.get("problems", []):
                html.append(f"<p style='color:{_BAD}'>• {p}</p>")
        else:
            html.append(f"<p><b>Malware scan:</b> <span style='color:{colour}'>"
                        f"{verdict.upper()}</span> ({scan.get('score', 0)}/100) — "
                        f"{scan.get('summary', '')}</p>")
            html.append(f"<p><b>Integrity:</b> {preview.get('integrity_summary', '')}</p>")

            perms = preview.get("permissions") or []
            if perms:
                html.append("<p><b>It will be able to ask for:</b></p><ul>")
                for d in perms:
                    html.append(f"<li><span style='color:{_RISK.get(d['risk'], _BAD)}'>"
                                f"{d['title']}</span> — {d['detail']}<br>"
                                f"<span style='color:{_DIM}'>{d['why_risky']}</span></li>")
                html.append("</ul>")
                html.append(f"<p style='color:{_DIM}'>Installing does not grant any of "
                            f"these. It will ask you, one at a time, the first time it "
                            f"needs each one.</p>")
            else:
                html.append("<p>Requests no special permissions.</p>")

            if preview.get("pip"):
                html.append(f"<p style='color:{_WARN}'><b>Wants to install "
                            f"{len(preview['pip'])} package(s) from PyPI:</b> "
                            f"{', '.join(preview['pip'])}. Package installers run their own "
                            f"code outside anything ELI can gate.</p>")

            for w in preview.get("warnings", []):
                html.append(f"<p style='color:{_WARN}'>⚠ {w}</p>")

            if scan.get("findings"):
                html.append("<h4>Scan findings</h4>")
                for f in scan["findings"][:15]:
                    c = _RISK.get(f["severity"], _DIM)
                    line = f" (line {f['line']})" if f.get("line") else ""
                    html.append(f"<p><b style='color:{c}'>[{f['severity']}]</b> "
                                f"{f['title']}{line}<br><span style='color:{_DIM}'>"
                                f"{f['detail']}</span></p>")

        te.setHtml("".join(html))
        v.addWidget(te, 1)

        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)
        row.addWidget(cancel)
        row.addStretch(1)

        if not blocked:
            if preview.get("pip"):
                pip_btn = QPushButton("Install, including PyPI packages")
                pip_btn.clicked.connect(lambda: self._approve(True))
                row.addWidget(pip_btn)
            ok = QPushButton("Install")
            ok.clicked.connect(lambda: self._approve(False))
            row.addWidget(ok)
        v.addLayout(row)

    def _approve(self, allow_pip: bool) -> None:
        self.approved = True
        self.allow_pip = allow_pip
        self.accept()


class AddMcpServerDialog(QDialog):
    """Collect one MCP server entry — by pasting a config, or by filling in a form.

    Pasting is first because that is how servers are actually distributed: every
    project's README ends with a `mcpServers` block to copy. Retyping it into four
    fields is where the transcription errors come from, and a mistyped arg is
    indistinguishable from a broken server once it fails to start.

    Nothing here writes anything. It produces an entry; the caller verifies it.
    """

    _SAMPLE = (
        '{\n'
        '  "mcpServers": {\n'
        '    "filesystem": {\n'
        '      "command": "npx",\n'
        '      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]\n'
        '    }\n'
        '  }\n'
        '}'
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add an MCP server")
        self.setMinimumSize(620, 560)
        self._entry: Optional[Dict[str, Any]] = None
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)

        intro = QLabel(
            "An MCP server is a program on this machine that ELI launches and talks to. "
            "Paste the config block from the server's README, or fill the fields in "
            "yourself — either way ELI starts it once and waits for a real handshake "
            "before saving anything.")
        intro.setWordWrap(True)
        v.addWidget(intro)

        tabs = QTabWidget()

        # ── paste ──────────────────────────────────────────────────────────────
        paste = QWidget()
        pv = QVBoxLayout(paste)
        hint = QLabel(
            "Accepts a whole <code>mcpServers</code> block or a single server object. "
            "If the block has several servers, pick one after pasting.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{_DIM};")
        pv.addWidget(hint)
        self._paste = QPlainTextEdit()
        self._paste.setPlaceholderText(self._SAMPLE)
        self._paste.textChanged.connect(self._parse_paste)
        pv.addWidget(self._paste, 1)
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Server:"))
        self._paste_pick = QComboBox()
        self._paste_pick.setEnabled(False)
        pick.addWidget(self._paste_pick, 1)
        pv.addLayout(pick)
        self._paste_status = QLabel("")
        self._paste_status.setWordWrap(True)
        self._paste_status.setTextFormat(Qt.TextFormat.RichText)
        pv.addWidget(self._paste_status)
        tabs.addTab(paste, "📋 Paste a config")

        # ── form ───────────────────────────────────────────────────────────────
        form_w = QWidget()
        fv = QVBoxLayout(form_w)
        form = QFormLayout()
        self._f_id = QLineEdit()
        self._f_id.setPlaceholderText("filesystem")
        self._f_id.setToolTip("The name ELI will use to qualify this server's tools.")
        form.addRow("Name", self._f_id)
        self._f_cmd = QLineEdit()
        self._f_cmd.setPlaceholderText("npx")
        self._f_cmd.setToolTip(
            "The program to run. It must be on PATH — ELI checks before saving and "
            "tells you what is missing rather than writing an entry that never starts.")
        form.addRow("Command", self._f_cmd)
        self._f_args = QLineEdit()
        self._f_args.setPlaceholderText("-y @modelcontextprotocol/server-filesystem /data")
        self._f_args.setToolTip("Space-separated. Quote any argument containing a space.")
        form.addRow("Arguments", self._f_args)
        self._f_env = QLineEdit()
        self._f_env.setPlaceholderText("API_KEY=… OTHER=…")
        self._f_env.setToolTip("Space-separated KEY=VALUE pairs passed to the process.")
        form.addRow("Environment", self._f_env)
        self._f_net = QCheckBox("This server needs network access")
        self._f_net.setToolTip(
            "Where this machine can enforce it, a server that does not ask for the "
            "network is launched into an empty network namespace and genuinely cannot "
            "reach anything. Ticking this gives it up.")
        form.addRow(self._f_net)
        fv.addLayout(form)
        fv.addStretch(1)
        tabs.addTab(form_w, "✍️ Fill in a form")

        self._tabs = tabs
        v.addWidget(tabs, 1)

        caveat = QLabel("")
        caveat.setWordWrap(True)
        caveat.setStyleSheet(f"color:{_WARN};")
        try:
            from eli.plugins.mcp import network_caveat
            caveat.setText(network_caveat(False))
        except Exception:
            caveat.setText("An MCP server runs as its own program, outside ELI's process.")
        v.addWidget(caveat)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Check and add")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    # ── paste parsing ─────────────────────────────────────────────────────────
    def _parse_paste(self) -> None:
        """Report what was pasted as it is typed, including why it will not do."""
        text = self._paste.toPlainText().strip()
        self._paste_pick.clear()
        self._paste_pick.setEnabled(False)
        if not text:
            self._paste_status.setText("")
            return
        try:
            data = json.loads(text)
        except Exception as exc:
            self._paste_status.setText(
                f"<span style='color:{_WARN}'>Not valid JSON yet: {exc}</span>")
            return
        servers = _servers_from_config(data)
        if not servers:
            self._paste_status.setText(
                f"<span style='color:{_WARN}'>No server found. Expected a "
                f"<code>mcpServers</code> object, or a single server with a "
                f"<code>command</code>.</span>")
            return
        for sid in servers:
            self._paste_pick.addItem(sid)
        self._paste_pick.setEnabled(len(servers) > 1)
        self._paste_status.setText(
            f"<span style='color:{_OK}'>Found {len(servers)} server(s): "
            f"{', '.join(servers)}</span>")

    # ── result ────────────────────────────────────────────────────────────────
    def entry(self) -> Optional[Dict[str, Any]]:
        return self._entry

    def _accept(self) -> None:
        entry = (self._entry_from_paste() if self._tabs.currentIndex() == 0
                 else self._entry_from_form())
        if entry is None:
            return
        from eli.plugins.mcp import validate_entry
        check = validate_entry(entry)
        if not check["ok"]:
            QMessageBox.warning(self, "Add an MCP server", "\n".join(check["problems"]))
            return
        self._entry = {**check["entry"], "id": entry["id"]}
        self.accept()

    def _entry_from_paste(self) -> Optional[Dict[str, Any]]:
        text = self._paste.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Add an MCP server", "Nothing pasted yet.")
            return None
        try:
            servers = _servers_from_config(json.loads(text))
        except Exception as exc:
            QMessageBox.warning(self, "Add an MCP server", f"That is not valid JSON:\n{exc}")
            return None
        if not servers:
            QMessageBox.warning(
                self, "Add an MCP server",
                "No server found in that config. It needs a 'mcpServers' object, or to "
                "be a single server object with a 'command'.")
            return None
        sid = self._paste_pick.currentText() or next(iter(servers))
        spec = dict(servers[sid])
        spec.pop("_comment", None)
        return {"id": sid, "transport": "stdio", **spec}

    def _entry_from_form(self) -> Optional[Dict[str, Any]]:
        import shlex
        sid = self._f_id.text().strip()
        cmd = self._f_cmd.text().strip()
        if not sid or not cmd:
            QMessageBox.warning(self, "Add an MCP server",
                                "A name and a command are both required.")
            return None
        try:
            args = shlex.split(self._f_args.text().strip())
        except ValueError as exc:
            QMessageBox.warning(self, "Add an MCP server",
                                f"The arguments could not be parsed: {exc}")
            return None
        env: Dict[str, str] = {}
        for pair in shlex.split(self._f_env.text().strip()):
            key, _, value = pair.partition("=")
            if key:
                env[key] = value
        entry = {"id": sid, "transport": "stdio", "command": cmd, "args": args, "env": env}
        if self._f_net.isChecked():
            entry["permissions"] = ["network"]
        return entry


def _servers_from_config(data: Any) -> Dict[str, Dict[str, Any]]:
    """Pull server specs out of whatever shape was pasted.

    Three shapes are in circulation and all three appear in READMEs: the full file
    with `mcpServers`, the bare mapping of name to spec, and a single spec on its
    own. Accepting only the first would reject most of what people actually copy.
    """
    if not isinstance(data, dict):
        return {}
    block = data.get("mcpServers")
    if isinstance(block, dict):
        return {str(k): v for k, v in block.items()
                if isinstance(v, dict) and not str(k).startswith("_")}
    if "command" in data or "url" in data:
        sid = str(data.get("id") or data.get("name") or "server")
        return {sid: {k: v for k, v in data.items() if k not in ("id", "name")}}
    found = {str(k): v for k, v in data.items()
             if isinstance(v, dict) and ("command" in v or "url" in v)
             and not str(k).startswith("_")}
    return found


# `MarketplaceTab` was this class's name while it was a Settings sub-tab. Kept as an
# alias so an out-of-tree import does not break on the rename.
MarketplaceTab = ExtensionsTab

__all__ = ["ExtensionsTab", "MarketplaceTab", "AddMcpServerDialog",
           "InstallReviewDialog", "ScanReportDialog"]
