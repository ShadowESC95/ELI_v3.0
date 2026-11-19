"""ELI Labs — Workspaces tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _WorkspacesTab(QWidget):
    @property
    def _DATA_FILE(self) -> Path:
        from eli.core.paths import adopt_legacy_file, data_dir
        return adopt_legacy_file(data_dir() / "labs" / "labs_workspaces.json", "~/.eli/labs_workspaces.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workspaces: Dict[str, Dict] = {}
        self._load()
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Workspaces"))
        self._ws_list = QListWidget()
        self._ws_list.currentTextChanged.connect(self._on_ws_selected)
        lv.addWidget(self._ws_list)
        btn_row = QHBoxLayout()
        new_btn = QPushButton("+ New")
        new_btn.clicked.connect(self._new_ws)
        btn_row.addWidget(new_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._del_ws)
        btn_row.addWidget(del_btn)
        lv.addLayout(btn_row)
        activate_btn = QPushButton("Activate Workspace")
        activate_btn.setStyleSheet("background:#2d7d46;color:white;font-weight:bold;padding:6px;")
        activate_btn.clicked.connect(self._activate)
        lv.addWidget(activate_btn)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self._ws_name_label = QLabel("Select a workspace")
        self._ws_name_label.setStyleSheet("font-weight:bold;font-size:14px;padding:4px;")
        rv.addWidget(self._ws_name_label)

        form = QFormLayout()
        self._ws_desc = QLineEdit()
        self._ws_desc.setPlaceholderText("Short description")
        form.addRow("Description:", self._ws_desc)
        rv.addLayout(form)

        rv.addWidget(QLabel("Files & folders (one path per line):"))
        self._ws_paths = QTextEdit()
        self._ws_paths.setMaximumHeight(90)
        self._ws_paths.setFont(QFont("Courier New", 9))
        self._ws_paths.setPlaceholderText(
            "e.g.\n~/Desktop/qmsh\nmodel06.py\n~/notes/qmsh.md\n"
        )
        rv.addWidget(self._ws_paths)

        rv.addWidget(QLabel("Launch on activation (one command per line):"))
        self._ws_cmds = QTextEdit()
        self._ws_cmds.setMaximumHeight(100)
        self._ws_cmds.setFont(QFont("Courier New", 9))
        self._ws_cmds.setPlaceholderText(
            "e.g.\nparaview\njupyter lab\ncode /path/to/project\n"
        )
        rv.addWidget(self._ws_cmds)

        tag_form = QFormLayout()
        self._ws_memtag = QLineEdit()
        self._ws_memtag.setPlaceholderText("project.<name> — scopes this project's memories")
        tag_form.addRow("Memory tag:", self._ws_memtag)
        rv.addLayout(tag_form)
        self._ws_tasks_label = QLabel("Background tasks: —")
        self._ws_tasks_label.setStyleSheet("color:#88c0d0;")
        rv.addWidget(self._ws_tasks_label)
        self._ws_mem_label = QLabel("Project memories: —")
        self._ws_mem_label.setStyleSheet("color:#a3be8c;")
        rv.addWidget(self._ws_mem_label)
        self._ws_active_label = QLabel("")
        self._ws_active_label.setStyleSheet("color:#ebcb8b;font-weight:bold;")
        rv.addWidget(self._ws_active_label)

        rv.addWidget(QLabel("Notes / context:"))
        self._ws_notes = QTextEdit()
        self._ws_notes.setPlaceholderText("Notes about this project…")
        rv.addWidget(self._ws_notes)

        save_row = QHBoxLayout()
        save_btn = QPushButton("Save Project")
        save_btn.clicked.connect(self._save_current)
        save_row.addWidget(save_btn)
        open_btn = QPushButton("📂 Open Project")
        open_btn.setToolTip("Open the project's files & folders in the OS.")
        open_btn.clicked.connect(self._open_project)
        save_row.addWidget(open_btn)
        rv.addLayout(save_row)

        active_row = QHBoxLayout()
        self._set_active_btn = QPushButton("🎯 Set Active")
        self._set_active_btn.setToolTip(
            "Make this the active project: new overnight tasks and new memories are "
            "owned/tagged by it until you clear it.")
        self._set_active_btn.clicked.connect(self._set_active)
        active_row.addWidget(self._set_active_btn)
        clear_active_btn = QPushButton("Clear active")
        clear_active_btn.clicked.connect(self._clear_active)
        active_row.addWidget(clear_active_btn)
        rv.addLayout(active_row)

        self._ws_state_label = QLabel("Last state: —")
        self._ws_state_label.setStyleSheet("color:#81a1c1;")
        rv.addWidget(self._ws_state_label)
        resume_row = QHBoxLayout()
        save_state_btn = QPushButton("📸 Save State")
        save_state_btn.setToolTip("Snapshot the current view (active tab) so Resume can restore it.")
        save_state_btn.clicked.connect(self._save_state)
        resume_row.addWidget(save_state_btn)
        resume_btn = QPushButton("▶ Resume Project")
        resume_btn.setStyleSheet("background:#5e81ac;color:white;font-weight:bold;padding:6px;")
        resume_btn.setToolTip("Re-open the project's files/folders, restore the saved tab, "
                              "set it active, and relaunch its apps.")
        resume_btn.clicked.connect(self._resume)
        resume_row.addWidget(resume_btn)
        rv.addLayout(resume_row)

        self._activate_log = QTextEdit()
        self._activate_log.setReadOnly(True)
        self._activate_log.setMaximumHeight(100)
        self._activate_log.setPlaceholderText("Activation log will appear here…")
        rv.addWidget(self._activate_log)

        splitter.addWidget(right)
        splitter.setSizes([200, 600])
        self._refresh_list()

    def _refresh_list(self):
        self._ws_list.clear()
        for name in sorted(self._workspaces):
            self._ws_list.addItem(name)

    def _on_ws_selected(self, name: str):
        if name not in self._workspaces:
            return
        ws = self._workspaces[name]
        self._ws_name_label.setText(name)
        self._ws_desc.setText(ws.get("description", ""))
        self._ws_paths.setPlainText("\n".join(ws.get("paths", [])))
        self._ws_cmds.setPlainText("\n".join(ws.get("commands", [])))
        self._ws_memtag.setText(ws.get("memory_tag", "") or f"project.{name.lower().replace(' ', '_')}")
        self._ws_notes.setPlainText(ws.get("notes", ""))
        owned = self._owned_tasks(name)
        self._ws_tasks_label.setText(
            f"Background tasks: {len(owned)}" + (
                "  (" + ", ".join(f"#{t['id']}:{t['status']}" for t in owned[:5]) + ")" if owned else " (none)"))
        # project memories (by tag) + active state
        tag = self._ws_memtag.text().strip() or f"project.{name.lower().replace(' ', '_')}"
        self._ws_mem_label.setText(f"Project memories: {len(self._project_memories(tag))}")
        self._refresh_active_label(name)
        self._show_state(ws)

    def _project_memories(self, tag: str):
        try:
            from eli.memory import get_memory
            return get_memory().get_memories_by_tag(tag, limit=200)
        except Exception:
            return []

    def _refresh_active_label(self, name: str):
        try:
            from eli.runtime.active_project import active_name
            cur = active_name()
        except Exception:
            cur = ""
        if cur and cur == name:
            self._ws_active_label.setText("● ACTIVE — new tasks & memories belong to this project")
            self._set_active_btn.setText("✓ Active")
        else:
            self._ws_active_label.setText(f"Active project: {cur}" if cur else "No active project")
            self._set_active_btn.setText("🎯 Set Active")

    def _set_active(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        tag = self._ws_memtag.text().strip() or f"project.{name.lower().replace(' ', '_')}"
        try:
            from eli.runtime.active_project import set_active
            set_active(name, tag)
        except Exception as ex:
            log.debug(f"[Projects] set_active failed: {ex}")
        self._refresh_active_label(name)

    def _clear_active(self):
        try:
            from eli.runtime.active_project import clear_active
            clear_active()
        except Exception:
            pass
        item = self._ws_list.currentItem()
        self._refresh_active_label(item.text() if item else "")

    # ── Phase 4: last-state save / resume ────────────────────────────────────
    def _main_tabs(self):
        """The main window's QTabWidget (for capturing/restoring the active tab)."""
        try:
            return getattr(self.window(), "tabs", None)
        except Exception:
            return None

    def _save_state(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        tabs = self._main_tabs()
        active_tab = ""
        try:
            if tabs is not None:
                active_tab = tabs.tabText(tabs.currentIndex())
        except Exception:
            active_tab = ""
        providers = {}
        try:
            from eli.runtime.state_providers import capture_all
            providers = capture_all()
        except Exception:
            providers = {}
        rec = self._workspaces.get(name, {})
        rec["last_state"] = {"active_tab": active_tab, "saved_at": datetime.now().isoformat(),
                             "providers": providers}
        self._workspaces[name] = rec
        self._save()
        self._show_state(rec)
        extra = f", {len(providers)} component state(s)" if providers else ""
        self._activate_log.append(f"Saved state for '{name}' (tab: {active_tab or '—'}{extra}).")

    def _show_state(self, rec: Dict):
        ls = (rec or {}).get("last_state") or {}
        if ls:
            when = str(ls.get("saved_at", ""))[:16].replace("T", " ")
            self._ws_state_label.setText(f"Last state: tab={ls.get('active_tab') or '—'}  ·  saved {when}")
        else:
            self._ws_state_label.setText("Last state: — (none saved)")

    def _resume(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        self._activate_log.clear()
        self._activate_log.append(f"Resuming project '{name}'…\n")
        # 1) re-open files/folders
        self._open_project()
        # 2) make it the active project (resuming = working in it)
        self._set_active()
        # 3) restore the saved GUI tab
        rec = self._workspaces.get(name, {})
        ls = rec.get("last_state") or {}
        tabname = (ls.get("active_tab") or "").strip()
        tabs = self._main_tabs()
        if tabs is not None and tabname:
            for i in range(tabs.count()):
                try:
                    if tabs.tabText(i) == tabname:
                        tabs.setCurrentIndex(i)
                        self._activate_log.append(f"  ✓ restored tab: {tabname}")
                        break
                except Exception:
                    pass
        # 4) restore component state (Sim-IDE code buffer, etc.)
        try:
            from eli.runtime.state_providers import restore_all
            n = restore_all(ls.get("providers") or {})
            if n:
                self._activate_log.append(f"  ✓ restored {n} component state(s)")
        except Exception:
            pass
        # 5) relaunch its apps (commands) — _activate has its own confirm gate
        self._activate()

    def _owned_tasks(self, name: str):
        try:
            from eli.runtime.background_tasks import get_background_tasks
            return [t for t in get_background_tasks().list(limit=100)
                    if (t.get("meta") or {}).get("project") == name]
        except Exception:
            return []

    def _open_project(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        paths = [p.strip() for p in self._ws_paths.toPlainText().splitlines() if p.strip()]
        self._activate_log.clear()
        self._activate_log.append(f"Opening project '{name}' — {len(paths)} path(s)\n")
        if not paths:
            self._activate_log.append("(no files/folders configured)")
            return
        from pathlib import Path as _P
        for raw in paths:
            p = _P(raw).expanduser()
            if not p.exists():
                self._activate_log.append(f"  ✗ not found: {p}")
                continue
            ok = False
            try:
                from eli.utils.platform_compat import open_file as _of
                ok = bool(_of(str(p)))
            except Exception:
                ok = False
            if not ok:
                try:
                    subprocess.Popen(["xdg-open", str(p)])
                    ok = True
                except Exception as ex:
                    self._activate_log.append(f"  ✗ {p}: {ex}")
                    continue
            self._activate_log.append(f"  ✓ opened {p}")

    def _new_ws(self):
        name, ok = QInputDialog.getText(self, "New Workspace", "Workspace name:")
        if ok and name.strip():
            name = name.strip()
            self._workspaces[name] = {
                "description": "", "paths": [], "commands": [], "notes": "",
                "memory_tag": f"project.{name.lower().replace(' ', '_')}",
            }
            self._refresh_list()
            items = self._ws_list.findItems(name, Qt.MatchFlag.MatchExactly)
            if items:
                self._ws_list.setCurrentItem(items[0])
            self._save()

    def _del_ws(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        reply = QMessageBox.question(self, "Delete", f"Delete workspace '{name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._workspaces.pop(name, None)
            self._refresh_list()
            self._save()

    def _save_current(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        prev = self._workspaces.get(name, {})
        self._workspaces[name] = {
            "description": self._ws_desc.text(),
            "paths": [p for p in self._ws_paths.toPlainText().splitlines() if p.strip()],
            "commands": [c for c in self._ws_cmds.toPlainText().splitlines() if c.strip()],
            "memory_tag": self._ws_memtag.text().strip() or f"project.{name.lower().replace(' ', '_')}",
            "notes": self._ws_notes.toPlainText(),
            "last_saved": datetime.now().isoformat(),
        }
        if prev.get("last_state"):  # Phase 4: don't lose a saved resume-state on edit
            self._workspaces[name]["last_state"] = prev["last_state"]
        self._save()

    def _activate(self):
        item = self._ws_list.currentItem()
        if not item:
            return
        name = item.text()
        ws = self._workspaces.get(name, {})
        cmds = [str(c).strip() for c in ws.get("commands", []) if str(c).strip()]
        self._activate_log.clear()
        self._activate_log.append(f"Activating workspace: {name}\n")

        if not cmds:
            self._activate_log.append("(no commands configured)")
            return

        # Explicit user gate. Workspace commands are local process launches.
        buttons = getattr(QMessageBox, "StandardButton", QMessageBox)
        preview = "\n".join(f"• {c}" for c in cmds[:12])
        if len(cmds) > 12:
            preview += f"\n… and {len(cmds) - 12} more"
        reply = QMessageBox.warning(
            self,
            "Run workspace commands?",
            (
                f"Workspace '{name}' contains {len(cmds)} command(s).\n\n"
                f"{preview}\n\n"
                "Commands will be launched directly without shell expansion. "
                "Only continue if you trust this workspace."
            ),
            buttons.Yes | buttons.No,
        )
        if reply != buttons.Yes:
            self._activate_log.append("Activation cancelled.")
            return

        for cmd in cmds:
            self._activate_log.append(f"  Launching: {cmd}")
            try:
                argv = shlex.split(cmd)
                if not argv:
                    self._activate_log.append("    Skipped empty command")
                    continue
                subprocess.Popen(argv)
                self._activate_log.append("    OK")
            except ValueError as ex:
                self._activate_log.append(f"    Parse error: {ex}")
            except FileNotFoundError:
                self._activate_log.append(f"    Command not found: {cmd}")
            except Exception as ex:
                self._activate_log.append(f"    Error: {ex}")

    def _save(self):
        try:
            self._DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._DATA_FILE.write_text(json.dumps(self._workspaces, indent=2), encoding="utf-8")
        except Exception as ex:
            log.debug(f"[Workspaces] save error: {ex}")

    def _load(self):
        from eli.utils.jsonio import read_json_dict
        self._workspaces = read_json_dict(self._DATA_FILE)


# ═══════════════════════════════════════════════════════════════════════════
# Sim / IDE sub-tab  (enhanced IDE with matplotlib output)
# ═══════════════════════════════════════════════════════════════════════════
