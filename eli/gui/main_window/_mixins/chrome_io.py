"""ChromeIoMixin — 63 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class ChromeIoMixin:
    def _daemon_queue_consumer(self):
        """
        Reads (kind, data) tuples from the proactive daemon's suggestion_queue
        and emits them as GUI signals so the proactive tab stays live.
        Runs in its own daemon thread.
        """
        import queue as _q
        daemon = self._proactive_daemon
        if daemon is None:
            return
        while True:
            try:
                kind, data = daemon.suggestion_queue.get(timeout=5)
            except _q.Empty:
                continue
            except Exception:
                import time as _t; _t.sleep(5); continue
            try:
                if kind == "pattern":
                    stype = data.get("type", "pattern")
                    sugg  = data.get("suggestion", "")
                    line  = f"<b>[{stype}]</b> {sugg}"
                    log.debug(f"[PROACTIVE] {stype}: {sugg}")
                    self.proactive_suggestions_signal.emit(line)
                elif kind == "improvement":
                    cat  = data.get("category", "code")
                    det  = data.get("detail", "") or data.get("description", "") or data.get("suggestion", "")
                    log.debug(f"[PROACTIVE] code insight [{cat}]: {det[:120]}")
                    self.proactive_insights_signal.emit(
                        f"<b>[{cat}]</b> {det[:200]}")
                elif kind == "habit_suggestion":
                    # ELI proactively offers a detected habit and asks first.
                    # Pending state is already set by the daemon; a "yes" reply is
                    # intercepted by the router's pending_habit_confirm stage.
                    offer = data.get("suggestion", "")
                    log.debug(f"[PROACTIVE] habit suggestion: {offer}")
                    self.chat_response_signal.emit(f"💡 {offer}")
                    self.proactive_suggestions_signal.emit(f"<b>[habit?]</b> {offer}")
                elif kind in ("habit", "habit_result"):
                    name = data.get("name", "")
                    sugg = data.get("suggestion", "")
                    ok   = data.get("ok", None)
                    icon = ("✓" if ok else "✗") if ok is not None else "⏰"
                    log.debug(f"[PROACTIVE] habit {icon} '{name}': {sugg}")
                    self.proactive_suggestions_signal.emit(
                        f"<b>[habit {icon}]</b> {name}: {sugg}")
                elif kind == "emotion_checkin":
                    # ELI opens the conversation about how the user has been reading.
                    # Goes to CHAT (not just the suggestions pane) — the whole point is
                    # that ELI speaks first. The daemon already set the pending proposal
                    # so a "yes" routes instead of being swallowed.
                    offer = data.get("suggestion", "")
                    log.debug(f"[PROACTIVE] emotional check-in "
                              f"({data.get('detected','')}×{data.get('run_length',0)})")
                    self.chat_response_signal.emit(offer)
                    self.proactive_suggestions_signal.emit(
                        f"<b>[check-in: {data.get('detected','')}]</b> {offer}")
                elif kind == "reminder":
                    # A reminder or calendar event coming due (eli.runtime.agenda): said in the
                    # chat, where the user is looking, as well as the desktop notification.
                    note = data.get("suggestion", "")
                    log.debug(f"[PROACTIVE] {note}")
                    self.chat_response_signal.emit(f"⏰ {note}")
                    self.proactive_suggestions_signal.emit(f"<b>[reminder]</b> {note}")
                elif kind == "morning_report":
                    report_text = data.get("suggestion", "")
                    log.debug(f"[PROACTIVE] morning report ready")
                    # Route to summaries tab
                    self.proactive_summary_signal.emit(
                        f"<pre style='white-space:pre-wrap'>{report_text}</pre>")
                else:
                    sugg = data.get("suggestion", str(data)[:200]) if isinstance(data, dict) else str(data)[:200]
                    log.debug(f"[PROACTIVE] [{kind}]: {sugg[:120]}")
                    self.proactive_suggestions_signal.emit(
                        f"<b>[{kind}]</b> {sugg[:200]}")
                # NOTE: do NOT touch _proactive_dock widgets from this thread —
                # dock forwarding is handled by _update_suggestions_display on the GUI thread.
            except Exception as _sig_err:
                log.debug(f"[GUI] daemon queue signal failed: {_sig_err}")

    def _retrieve_relevant_memories(self, query: str, limit: int = 20) -> str:
        if not self._central_memory:
            return ""

        context_parts = []

        # 1. Always include identity/preference memories
        try:
            identity_mems = self._central_memory.recall_memory("identity preference name", limit=10)
            if identity_mems:
                lines = []
                for m in identity_mems:
                    txt_ = (m.get("text") or m.get("content") or "").strip()
                    if txt_:
                        lines.append(f"- {txt_}")
                if lines:
                    context_parts.append("Known facts about the user:\n" + "\n".join(lines))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # 2. Semantic search for the query
        lowered = query.lower()
        commandish = bool(re.match(r"^(open|access|initiate|fabricate|check|run|execute|type|press|pause|resume|play|next|previous|stop|mute|unmute|read|list|show|write|add)\b", lowered))
        if query and not commandish:
            try:
                results = self._central_memory.recall_memory(query=query, limit=limit)
                if results:
                    lines = []
                    for mem in results[:5]:
                        text = (mem.get("text") or "").replace("\n", " ")[:200]
                        tags = mem.get("tags", [])
                        if isinstance(tags, list):
                            tags = ", ".join(tags)
                        lines.append(f"- {text} [{tags}]")
                    if lines:
                        context_parts.append("Relevant stored knowledge:\n" + "\n".join(lines))
            except Exception as e:
                print(f"Memory recall failed: {e}")

        # 3. Pull recent conversation turns from user.sqlite3
        try:
            import sqlite3 as _sq3
            from eli.core.paths import user_db_path as _udp3
            con = _sq3.connect(str(_udp3()))
            rows = con.execute(
                "SELECT role, content FROM conversation_turns WHERE lower(role) = 'user' ORDER BY id DESC LIMIT 20"
            ).fetchall()
            con.close()
            if rows:
                lines = []
                for role, content in reversed(rows):
                    snippet = (content or '')[:120].replace('\n', ' ')
                    lines.append(f"  [{role}]: {snippet}")
                context_parts.append("Recent conversation history:\n" + "\n".join(lines))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        return "\n\n".join(context_parts)

    def _on_thinking_toggled(self, checked: bool):
        """Deep-thinking toggle for models whose chat template enables reasoning.
        ON = the answer call may think (quality, slower); OFF = no thinking.
        Utility calls (routing/JSON/summary) never think regardless. Live: sets
        ELI_MODEL_THINK now (read per-call by gguf_inference._no_think_prefill) — no
        model reload — and persists the choice. No effect when the loaded model
        has no thinking channel."""
        try:
            import os as _os
            _os.environ["ELI_MODEL_THINK"] = "1" if checked else "0"
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            self.thinking_btn.setText('🧠 Think: ON' if checked else '🧠 Think: OFF')
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            from eli.core.runtime_settings import update_settings as _us
            _us(model_thinking=bool(checked))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _on_full_control_toggled(self, checked: bool):
        """Master override — lifts ALL of ELI's safety barriers. Confirms on enable."""
        if checked:
            try:
                from PySide6.QtWidgets import QMessageBox
                resp = QMessageBox.warning(
                    self, "Enable ELI Full Control?",
                    "This lifts EVERY safety barrier:\n\n"
                    "• Network is always allowed (offline-by-default is off)\n"
                    "• ELI may edit its own code automatically\n"
                    "• ELI auto-approves its own autonomous actions\n"
                    "• ANY shell command runs — including normally-blocked\n"
                    "  destructive ones (rm -rf, mkfs, curl | bash, …)\n\n"
                    "Enable only if you fully trust ELI on this machine. Continue?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if resp != QMessageBox.Yes:
                    self.full_control_btn.blockSignals(True)
                    self.full_control_btn.setChecked(False)
                    self.full_control_btn.blockSignals(False)
                    return
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        try:
            from eli.core.full_control import set_full_control
            set_full_control(checked)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            self.full_control_btn.setText("🔓 Full Control: ON" if checked else "🔒 Full Control: OFF")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            colour = "#ff5252" if checked else "#88c0d0"
            note = ("🔓 ELI Full Control ENABLED — all safety barriers lifted."
                    if checked else "🔒 ELI Full Control disabled — normal safety restored.")
            self.chat_display.append(
                f'<span style="color:{colour};font-size:11px;">{note}</span><br>'
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _on_network_toggled(self, checked: bool):
        try:
            from eli.core import config as _cfg
            _cfg.set("network_enabled", checked)
            self.net_btn.setText("🌐 Net: ON" if checked else "🌐 Net: OFF")
            # Record the flip in the tamper-evident audit ledger (the web toggle already
            # does; the desktop one did not). Enabling is logged at 'warning' so it stands
            # out. Once on, netguard records each outbound connection separately.
            try:
                from eli.runtime.evidence_ledger import record_event
                record_event(
                    "net_toggle", source="gui",
                    action="NET_ENABLE" if checked else "NET_DISABLE",
                    outcome="enabled" if checked else "disabled",
                    severity="warning" if checked else "info",
                    payload={"enabled": bool(checked)},
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        except Exception as e:
            log.debug(f"[GUI] network toggle failed: {e}")

    def _on_ambient_vision_toggled(self, checked: bool):
        try:
            from eli.perception.ambient_vision import set_ambient_vision
            st = set_ambient_vision(checked)
            self.ambient_vision_btn.setText("👁 Watch: ON" if checked else "👁 Watch: OFF")
            if checked:
                # Be honest in the UI if the vision model isn't installed yet.
                try:
                    from eli.perception import vision as _vision
                    ok, reason = _vision.vision_available()
                    if not ok:
                        self.ambient_vision_btn.setToolTip(
                            f"Ambient watch ON, but vision can't run yet: {reason}"
                        )
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            log.debug(f"[GUI] ambient vision toggled -> {checked} ({st})")
        except Exception as e:
            log.debug(f"[GUI] ambient vision toggle failed: {e}")

    def _on_wake_word_toggled(self, wake_on: bool):
        """Wake word button in the chat toolbar — mirrors the Audio settings checkbox."""
        import os as _os
        # wake_on=True → wake word required → direct chat disabled
        _os.environ["ELI_STT_ALLOW_DIRECT_CHAT"] = "0" if wake_on else "1"
        self.wake_word_btn.setText("Wake: ON" if wake_on else "Wake: OFF")
        _wake_msg = (
            'ON — say "computer" before commands' if wake_on
            else 'OFF — all speech dispatched (≥2 words)'
        )
        log.debug(f"[WAKE] {_wake_msg}")
        # Keep settings-page checkbox in sync
        if hasattr(self, "allow_direct_chat_checkbox"):
            self.allow_direct_chat_checkbox.blockSignals(True)
            self.allow_direct_chat_checkbox.setChecked(not wake_on)
            self.allow_direct_chat_checkbox.blockSignals(False)
        self.save_settings(silent=True)

    def _on_auto_mode_toggled(self, checked: bool):
        self._auto_reasoning_mode = bool(checked)
        try:
            self.auto_mode_btn.setText("🤖 Auto-Mode" if checked else "🧊 Manual")
            note = "auto-selecting reasoning mode from prompt keywords" if checked \
                   else "manual reasoning mode (auto-detect off)"
            self.chat_display.append(
                f'<span style="color:#88c0d0;font-size:11px;">⚙️ {note}</span><br>'
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _get_mode_prefix(self) -> str:
        """Return private reasoning strategy prompt prefix for backend handoff.

        This must never request visible chain-of-thought, branches,
        self-consistency samples, or constitutional critique passes.
        """
        try:
            from eli.cognition.reasoning_modes import gui_prompt_prefix_for_mode
            return gui_prompt_prefix_for_mode(getattr(self, "_reasoning_mode", "quick"))
        except Exception:
            return ""

    def _pick_user_color(self):
        """Open a colour picker so the user can customise their message colour."""
        color = QColorDialog.getColor(
            QColor(getattr(self, '_user_text_color', '#a3be8c')), self,
            "Pick your message colour"
        )
        if color.isValid():
            self._user_text_color = color.name()
            self.save_settings(silent=True)

    def _toggle_operator_console_dock(self, checked: bool = None):
        """Show or hide the Operator Console dock. Wired to View → Operator Console
        (Ctrl+Shift+O)."""
        try:
            dock = getattr(self, "_operator_console_dock", None)
            if dock is None:
                try:
                    from eli.gui.docks.operator_console_dock import OperatorConsoleDock
                    dock = OperatorConsoleDock(self)
                    self._operator_console_dock = dock
                    self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
                except Exception as _err:
                    log.debug(f"[GUI] OperatorConsoleDock attach failed: {_err}")
                    return
            if checked is None:
                checked = not dock.isVisible()
            dock.setVisible(bool(checked))
            if checked:
                dock.raise_()
            try:
                self._toggle_operator_console_action.setChecked(bool(checked))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        except Exception as _e:
            log.debug(f"[GUI] toggle operator console dock failed: {_e}")

    def _install_hardware_log_relay(self, dock) -> None:
        """Attach the dock to ELI's hardware/load logging, exactly once."""
        if getattr(self, "_hardware_log_relay", None) is not None:
            return
        try:
            import logging as _logging
            from eli.gui.panels.startup import HardwareTuningLogRelay
            relay = HardwareTuningLogRelay(dock)
            # The "eli" parent, not the root logger: every module that writes this
            # story is eli.*, and propagation carries all of them. Attaching to
            # root would format every record in the process — including third-party
            # libraries — just to substring-match it.
            #
            # The level is deliberately NOT raised. These lines are emitted at
            # DEBUG; if the app is running quieter than that they are absent from
            # the terminal too, and the tab showing the same thing as the terminal
            # is the point. Forcing DEBUG here would add console noise the operator
            # did not ask for.
            _logging.getLogger("eli").addHandler(relay)
            self._hardware_log_relay = relay
        except Exception as exc:
            log.debug(f"[GUI] hardware log relay not installed: {exc}")
            self._hardware_log_relay = None

    def _ensure_hardware_tuning_dock(self):
        dock = getattr(self, "_hardware_tuning_dock", None)
        if dock is not None:
            return dock
        try:
            dock = HardwareTuningDock(self)
            self._hardware_tuning_dock = dock
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            dock.hide()
            # Subscribe the dock to the hardware/load lines ELI already writes to
            # the terminal, so the tab and the terminal cannot report different
            # numbers for the same launch. Installed once, on the root logger, so
            # it captures the optimizer, the fit, the probe and the load ladder
            # wherever they run.
            self._install_hardware_log_relay(dock)
            return dock
        except Exception as e:
            log.debug(f"[GUI] hardware tuning dock attach failed: {e}")
            self._hardware_tuning_dock = None
            return None

    def _toggle_hardware_tuning_dock(self, checked: bool = None):
        dock = self._ensure_hardware_tuning_dock()
        if dock is None:
            return
        if checked is None:
            checked = not dock.isVisible()
        dock.setVisible(bool(checked))
        if checked:
            dock.raise_()
        try:
            self._toggle_hardware_dock_action.setChecked(bool(checked))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _hardware_tuning_log(self, text: str):
        dock = self._ensure_hardware_tuning_dock()
        if dock is not None:
            dock.append_log(text)

    def adjust_active_zoom(self, direction: int):
        widget = self.tabs.currentWidget()
        if widget is getattr(self, "_settings_root", None):
            if direction > 0:
                self._settings_zoom_view.zoom_in()
            else:
                self._settings_zoom_view.zoom_out()
            return
        if widget is getattr(self, "image_tab_widget", None):
            if direction > 0:
                self._image_zoom_view.zoom_in()
            else:
                self._image_zoom_view.zoom_out()
            return

        focus = QApplication.focusWidget()
        if hasattr(focus, "zoomIn") and hasattr(focus, "zoomOut"):
            if direction > 0:
                focus.zoomIn(1)
            else:
                focus.zoomOut(1)
            return
        if hasattr(self, "chat_display"):
            if direction > 0:
                self.chat_display.zoomIn(1)
            else:
                self.chat_display.zoomOut(1)

    def reset_active_zoom(self):
        widget = self.tabs.currentWidget()
        if widget is getattr(self, "_settings_root", None):
            self._settings_zoom_view.zoom_reset()
            return
        if widget is getattr(self, "image_tab_widget", None):
            self._image_zoom_view.zoom_reset()

    def _labs_generate(self, prompt: str) -> str:
        """A Labs tool's own request to the model (File Chat, Sim/IDE, the Test & Review summary).

        The loaded backend answers it as ELI, in the whole free window. It is not a chat turn:
        nothing routes it, runs an action it names or records it as something the user said, so a
        command written inside a loaded file or a test's output is read as text. These prompts
        went through engine.process as the user's own message."""
        prompt = str(prompt or "").strip()
        if not prompt:
            return ""
        backend = self._text_backend_ready(notify=False)
        if backend is None:
            raise RuntimeError("no model is loaded" + (" yet; it is still loading"
                                                       if getattr(self, "_model_loading", False) else ""))
        return str(backend.generate(prompt=prompt, max_tokens=-1) or "").strip()

    def _convert_selected_file(self):
        """Convert the file currently selected in the Files tree to the chosen
        format via the CONVERT_DOCUMENT action."""
        try:
            idx = self.file_tree.currentIndex()
            path = self.file_model.filePath(idx) if idx.isValid() else ""
            if not path or Path(path).is_dir():
                self.convert_status_label.setText("Select a file (not a folder) in the tree first.")
                return
            fmt = self._convert_formats[self.convert_format_combo.currentIndex()][1]
            self.convert_status_label.setText(f"Converting {Path(path).name} → {fmt}…")
            # Called self.execute_action, which this window has never had (it's
            # ExecutorBridge's), so the button failed with an AttributeError.
            from eli.execution.executor_enhanced import execute as _execute
            _t0 = time.perf_counter()
            res = _execute("CONVERT_DOCUMENT", {"source": path, "format": fmt})
            if isinstance(res, dict):
                _ok = bool(res.get("ok", True))
                _msg = str(res.get("response") or res.get("content") or res.get("message")
                           or res.get("error") or "")
            else:
                _ok, _msg = bool(res), str(res or "")
            self.convert_status_label.setText(_msg or ("Converted." if _ok else "Convert failed."))
            # A button, not a turn through the engine, so it writes its own audit row.
            try:
                from eli.runtime import orchestrator_audit_ledger as _oal
                _ce = getattr(self, "_cognitive_engine", None)
                _oal.record_direct_action(
                    action="CONVERT_DOCUMENT", ok=_ok,
                    outcome="ok" if _ok else (_msg[:300] or "failed"),
                    source="gui_button",
                    session_id=str(getattr(_ce, "session_id", "") or ""),
                    user_id=str(getattr(_ce, "user_id", "") or ""),
                    elapsed_ms=(time.perf_counter() - _t0) * 1000.0)
            except Exception:
                log.debug("convert audit row failed", exc_info=True)
        except Exception as e:
            self.convert_status_label.setText(f"Convert failed: {e}")

    def _load_mode_presets_for_page(self) -> dict:
        try:
            from eli.core.runtime_settings import load_settings as _ls
            _settings = _ls() or {}
        except Exception:
            _settings = {}
        presets = dict(_settings.get("mode_presets") or {})
        if not presets:
            # Pre-first-run: derive from the current base tune so the page is
            # never empty; saving persists these as the live presets.
            try:
                from eli.core.hardware_profile import _derive_mode_presets
                presets = _derive_mode_presets(
                    int(_settings.get("n_ctx") or 8192),
                    int(_settings.get("max_tokens") or 2048),
                    float(_settings.get("temperature") or 0.7))
            except Exception:
                presets = {}
        return presets

    def _eli_server_lan_ip(self) -> str:
        """Best-effort LAN IP the phone can actually reach (cross-platform)."""
        try:
            from eli.runtime.server_util import resolve_lan_ip
            return resolve_lan_ip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
            return "<this-computer-ip>"

    def _eli_server_refresh_firewall_hint(self) -> None:
        try:
            from eli.runtime.server_util import firewall_hint
            fw = firewall_hint()
            cmds = fw.get("commands") or []
            tool = fw.get("tool") or "firewall"
            if cmds:
                self._srv_fw_cmd.setText(cmds[0])
                self._srv_fw_note.setText(
                    f"If your phone cannot connect, allow ELI through {tool} on your home network. "
                    "A tech-savvy person can run the command below once in Terminal "
                    "(it will ask for your computer password)."
                )
            else:
                self._srv_fw_cmd.clear()
                self._srv_fw_note.setText(
                    "If your phone cannot connect, check your computer's firewall settings "
                    "and allow incoming connections for Python / ELI on port 8081."
                )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _eli_server_copy_firewall_cmd(self) -> None:
        cmd = (self._srv_fw_cmd.text() or "").strip()
        if not cmd:
            return
        try:
            QApplication.clipboard().setText(cmd)
            self._srv_status.setText("● Firewall command copied — paste into Terminal")
            self._srv_status.setStyleSheet("font-size:12px;font-weight:700;color:#ebcb8b;")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _eli_server_open_firewall_terminal(self) -> None:
        import shlex
        import sys
        cmd = (self._srv_fw_cmd.text() or "").strip()
        if not cmd:
            QMessageBox.information(
                self,
                "Firewall",
                "Start phone / Wi-Fi mode first, or copy a firewall command from the Connect tab.",
            )
            return
        from eli.utils.platform_compat import terminal_argv

        if sys.platform.startswith("win"):
            # Frozen Windows CREATE_NO_WINDOW + bash -lc is a dead end — use PowerShell.
            ps = (
                f"Write-Host 'Run this as Administrator if it fails:'; "
                f"{cmd}; Write-Host ''; pause"
            )
            term_cmd = ["powershell", "-NoProfile", "-Command",
                        f"Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-Command',{shlex.quote(ps)}"]
            # Fallback without elevation if UAC is declined / unavailable.
            try:
                subprocess.Popen(term_cmd)
                return
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            try:
                subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
                return
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        script = f"{cmd}\necho\necho Done. You can close this window.\nread -r -p 'Press Enter to close…' _"
        term_cmd = terminal_argv(["bash", "-lc", script])
        if term_cmd:
            try:
                subprocess.Popen(term_cmd)
                return
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        QMessageBox.information(
            self,
            "Firewall",
            "Could not open a terminal automatically.\n\nCopy the command and run it manually:\n\n" + cmd,
        )

    def _eli_server_show_running_dialog(self, probe: dict, lan: bool) -> bool:
        """Return True if caller should treat the server as available."""
        if probe.get("eli_running"):
            title = "ELI is already running"
            body = (
                "The web server is already active on this computer — "
                "you do not need to start it again.\n\n"
                "Open the link below on this computer or your phone."
            )
            url = (probe.get("lan_url") if lan else None) or probe.get("local_url") or ""
        else:
            title = "Port already in use"
            body = (
                f"Port {probe.get('port', 8081)} is used by another program, so ELI cannot start here.\n\n"
                "Close the other program, or run ELI Server from only one place at a time."
            )
            url = ""

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(480)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(body))
        if url:
            link = QLabel(f'<a href="{url}" style="color:#a3be8c;">{url}</a>')
            link.setOpenExternalLinks(True)
            link.setWordWrap(True)
            lay.addWidget(link)
            self._srv_url.setText(url)
            self._eli_server_update_ui("running")
        box = QDialogButtonBox()
        if url:
            open_btn = box.addButton("Open in browser", QDialogButtonBox.ButtonRole.AcceptRole)
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        box.addButton(QDialogButtonBox.StandardButton.Close)
        lay.addWidget(box)
        box.rejected.connect(dlg.reject)
        if url:
            box.accepted.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        dlg.exec()
        return bool(probe.get("eli_running"))

    def _eli_server_refresh_qr(self, http_url: str = "", https_url: str = "") -> None:
        """Render connect QRs in the settings page (best-effort)."""
        try:
            from eli.runtime.server_util import qr_png_bytes
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QPixmap
        except Exception as ex:
            for lab in (
                getattr(self, "_srv_qr_http", None),
                getattr(self, "_srv_qr_https", None),
            ):
                if lab is not None:
                    lab.setText(f"QR unavailable\n({ex})")
            return
        for lab, url, label in (
            (getattr(self, "_srv_qr_http", None), http_url, "HTTP connect"),
            (getattr(self, "_srv_qr_https", None), https_url, "HTTPS voice"),
        ):
            if lab is None:
                continue
            if url:
                try:
                    # QLabel rich-text can't load data: URI <img> — decode the PNG into a
                    # QPixmap and set it directly. This is what makes the QR actually appear.
                    pix = QPixmap()
                    if not pix.loadFromData(qr_png_bytes(url), "PNG"):
                        raise RuntimeError("QR decode failed")
                    pix = pix.scaled(190, 190, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                    lab.setPixmap(pix)
                    lab.setToolTip(f"{label}: {url}")
                except Exception as ex:
                    lab.clear()
                    lab.setText(f"{label}\n(QR unavailable: {ex})")
            else:
                lab.clear()
                lab.setText(
                    f"{label}\n\nClick “📱 Start (phone / Wi-Fi)” above.\n"
                    "The QR appears here for your phone\n"
                    "to scan. (“This computer” mode is\nlocalhost-only — no phone QR.)"
                )

    def _eli_server_stop_https(self) -> None:
        uv = getattr(self, "_srv_https_uv", None)
        if uv is not None:
            try:
                uv.should_exit = True
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        self._srv_https_uv = None
        self._srv_https_thread = None
        import os
        os.environ.pop("ELI_API_HTTPS_PORT", None)
        try:
            self._srv_https_url.clear()
            self._eli_server_refresh_qr()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    @staticmethod
    def _eli_server_auth(lan: bool) -> tuple:
        """Where the web server listens and the token it asks for, set for the server to read.

        The token is the stable, saved one, reused across restarts so a paired phone is not
        stranded (rotate it to drop devices). "This computer" needs it too: any page the browser
        opens can reach 127.0.0.1, so a page whose name re-resolves there could drive ELI's whole
        API when it ran with no token. The link the window shows carries it, as the LAN link does."""
        import os
        from api.api_token import get_stable_token
        token = get_stable_token()
        host = "0.0.0.0" if lan else "127.0.0.1"
        os.environ["ELI_API_HOST"] = host
        os.environ["ELI_API_TOKEN"] = token
        os.environ.pop("ELI_API_ALLOW_TOKENLESS", None)
        return host, token

    def _eli_server_start(self, lan: bool) -> None:
        # Prefer sharing the already-loaded model (in-process uvicorn thread). On
        # frozen Windows that path often dies on the worker thread AFTER the outer
        # try succeeded — so we verify the port binds, then fall back to
        # ELI-Server.exe / ``--server`` (and paint LAN QRs as soon as URLs exist).
        import os, threading, sys, time, socket
        if getattr(self, "_srv_thread", None) and self._srv_thread.is_alive():
            return  # already running
        if getattr(self, "_srv_proc", None) is not None:
            try:
                if self._srv_proc.poll() is None:
                    return
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        from eli.runtime.server_util import effective_api_port
        # Prefer the spinbox / saved setting over a stale ELI_API_PORT from a prior Start.
        os.environ.pop("ELI_API_PORT", None)
        try:
            port = int(self._srv_port_spin.value())
        except Exception:
            port = effective_api_port()
        try:
            from eli.runtime.server_util import probe_eli_server
            probe = probe_eli_server(port)
            if probe.get("in_use") and probe.get("eli_running"):
                if self._eli_server_show_running_dialog(probe, lan):
                    if lan:
                        self._eli_server_refresh_firewall_hint()
                    return
            elif probe.get("in_use"):
                # another program (ELI v2 among them) holds this port: serve on the next free one
                from eli.runtime.server_util import free_port_after
                moved = free_port_after(port)
                if moved:
                    log.info("port %s is used by another program; ELI v3 serves on %s", port, moved)
                    port = moved
                    try:
                        self._srv_port_spin.setValue(moved)
                    except (AttributeError, RuntimeError, TypeError):
                        log.debug("no port box to show the move", exc_info=True)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            host, token = self._eli_server_auth(lan)
        except Exception as e:
            self._eli_server_update_ui("error", f"token: {e}")
            try:
                QMessageBox.warning(self, "Web server", f"Could not create the server's token:\n{e}")
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            return
        if lan:
            lan_ip = self._eli_server_lan_ip()
            url = f"http://{lan_ip}:{port}/?open=connect#token={token}"
            http_qr = f"http://{lan_ip}:{port}/#token={token}"
        else:
            lan_ip = "127.0.0.1"
            os.environ.pop("ELI_API_HTTPS_PORT", None)
            url = f"http://127.0.0.1:{port}/#token={token}"
            http_qr = ""
        os.environ["ELI_API_PORT"] = str(port)
        self._srv_err = ""
        https_url = ""
        if lan:
            # Paint QRs immediately so a slow/failed bind still shows the pair URL.
            try:
                self._srv_url.setText(url)
                self._eli_server_refresh_qr(http_url=http_qr, https_url="")
                self._eli_server_refresh_firewall_hint()
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        frozen = bool(getattr(sys, "frozen", False))
        # Frozen Windows: in-process uvicorn routinely fails on the worker thread;
        # go straight to the console ELI-Server.exe sibling (reliable + logs).
        if frozen and sys.platform == "win32":
            if self._eli_server_start_subprocess(
                lan=lan, port=port, host=host, token=token, url=url, http_qr=http_qr,
            ):
                return
            err = getattr(self, "_srv_err", "") or "subprocess server failed to start"
            try:
                QMessageBox.warning(self, "Web server", "Could not start the ELI web server.\n\n" + err[:500])
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            self._eli_server_update_ui("error", err)
            return

        try:
            # uvicorn needs websockets>=14 — self-heal an old/unpinned one live.
            try:
                from websockets.server import ServerProtocol  # noqa: F401
            except ImportError:
                log.warning("[SERVER] websockets package too old for uvicorn's "
                            "WebSocket support — upgrading in place")
                _pip_base = [sys.executable, "-m", "pip", "install", "--quiet",
                             "--upgrade", "websockets>=14.0"]
                _upgraded = False
                try:
                    subprocess.run(_pip_base, timeout=60, check=True,
                                    capture_output=True, text=True)
                    _upgraded = True
                except subprocess.CalledProcessError as _ws_fix_err:
                    # PEP 668 (bare system python, not our venv) blocks plain pip — retry once.
                    _stderr = (_ws_fix_err.stderr or "")
                    if "externally-managed-environment" in _stderr:
                        try:
                            subprocess.run(_pip_base + ["--break-system-packages"],
                                           timeout=60, check=True,
                                           capture_output=True, text=True)
                            _upgraded = True
                        except Exception as _ws_fix_err2:
                            log.warning("[SERVER] websockets self-upgrade failed even with "
                                        "--break-system-packages: %s", _ws_fix_err2)
                    else:
                        log.warning("[SERVER] websockets self-upgrade failed: %s", _ws_fix_err)
                except Exception as _ws_fix_err:
                    log.warning("[SERVER] websockets self-upgrade failed: %s", _ws_fix_err)
                if _upgraded:
                    try:
                        import importlib
                        import websockets.server as _ws_server
                        importlib.reload(_ws_server)
                    except Exception as _ws_fix_err3:
                        log.warning("[SERVER] websockets reload after upgrade failed: %s",
                                    _ws_fix_err3)
                else:
                    log.warning("[SERVER] could not self-heal websockets — run manually: "
                                 "%s -m pip install --break-system-packages 'websockets>=14.0'",
                                 sys.executable)

            try:
                import uvicorn
                from api.server import app as _app
            except ImportError as _imp_err:
                from eli.runtime.package_bootstrap import ensure_server_packages
                if not ensure_server_packages():
                    raise _imp_err from None
                import uvicorn
                from api.server import app as _app
            # Signal handlers are auto-skipped off the main thread; safe in a Qt app.
            # Explicit loop="asyncio" avoids Windows Proactor edge cases in a worker thread.
            self._srv_uv = uvicorn.Server(uvicorn.Config(
                _app, host=host, port=port, log_level="warning", loop="asyncio",
            ))

            def _run():
                try:
                    self._srv_uv.run()
                except Exception as e:
                    self._srv_err = str(e)

            self._srv_thread = threading.Thread(target=_run, daemon=True)
            self._srv_thread.start()
            # Confirm the port actually accepted a connection — thread start ≠ listening.
            bound = False
            for _ in range(20):  # ~2s
                if getattr(self, "_srv_err", ""):
                    break
                th = getattr(self, "_srv_thread", None)
                if th is None or not th.is_alive():
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                        bound = True
                        break
                except OSError:
                    time.sleep(0.1)
            if not bound:
                err = getattr(self, "_srv_err", "") or "server did not bind (in-process)"
                log.warning("in-process web server failed to bind: %s", err)
                try:
                    if self._srv_uv is not None:
                        self._srv_uv.should_exit = True
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                self._srv_uv = None
                self._srv_thread = None
                if frozen and self._eli_server_start_subprocess(
                    lan=lan, port=port, host=host, token=token, url=url, http_qr=http_qr,
                ):
                    return
                raise RuntimeError(err)

            if lan:
                from eli.runtime.server_util import start_https_sidecar
                hport, https_uv = start_https_sidecar(host=host)
                self._srv_https_uv = https_uv
                if hport:
                    https_url = f"https://{lan_ip}:{hport}/#token={token}"
                    self._srv_https_url.setText(https_url)
                    os.environ["ELI_API_HTTPS_PORT"] = str(hport)
                else:
                    self._srv_https_url.setText(
                        "(HTTPS unavailable — need cryptography for the phone mic cert)"
                    )
            else:
                self._srv_https_url.clear()
                self._eli_server_stop_https()
            self._srv_url.setText(url)
            self._eli_server_refresh_qr(http_url=http_qr, https_url=https_url)
            self._eli_server_update_ui("starting")
            if lan:
                self._eli_server_refresh_firewall_hint()
                try:
                    QDesktopServices.openUrl(QUrl(url))
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
        except Exception as e:
            err = str(e)
            _busy = (
                "98" in err
                or "10048" in err
                or "wsaeaddrinuse" in err.lower()
                or "already in use" in err.lower()
                or "address already in use" in err.lower()
                or "only one usage of each socket address" in err.lower()
            )
            if _busy:
                try:
                    from eli.runtime.server_util import probe_eli_server
                    probe = probe_eli_server(port)
                    if probe.get("in_use") and self._eli_server_show_running_dialog(probe, lan):
                        if lan:
                            self._eli_server_refresh_firewall_hint()
                        return
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            if frozen and self._eli_server_start_subprocess(
                lan=lan, port=port, host=host, token=token, url=url, http_qr=http_qr,
            ):
                return
            log.exception("ELI web server failed to start")
            try:
                QMessageBox.warning(
                    self,
                    "Web server",
                    "Could not start the ELI web server.\n\n" + err[:500],
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            self._eli_server_update_ui("error", err)

    def _eli_server_start_subprocess(
        self,
        *,
        lan: bool,
        port: int,
        host: str,
        token: str,
        url: str,
        http_qr: str = "",
    ) -> bool:
        """Spawn ELI-Server.exe / ``ELI.exe --server`` (or ``python -m api.server``)."""
        import os
        import sys
        import subprocess
        from pathlib import Path

        exe = Path(sys.executable)
        args: list[str]
        use_console_sibling = False
        if getattr(sys, "frozen", False):
            # Prefer the console sibling so bind errors are visible / loggable.
            sibling = exe.with_name("ELI-Server.exe" if sys.platform == "win32" else "ELI-Server")
            if sibling.is_file():
                args = [str(sibling)]
                use_console_sibling = True
            else:
                args = [str(exe), "--server"]
        else:
            args = [str(exe), "-m", "api.server"]
        if lan:
            args.append("--lan")
            args.append("--https")
        args.extend(["--port", str(port)])
        if token:
            args.extend(["--token", token])
        env = os.environ.copy()
        env["ELI_API_HOST"] = host
        env["ELI_API_PORT"] = str(port)
        if token:
            env["ELI_API_TOKEN"] = token
        env.pop("ELI_API_ALLOW_TOKENLESS", None)      # a token either way (_eli_server_auth)
        if lan:
            env.setdefault("ELI_API_HTTPS_PORT", "8443")
        log_dir = None
        try:
            from eli.core.paths import project_root
            log_dir = Path(project_root()) / "artifacts" / "startup" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        err_fh = None
        out_fh = None
        popen_kw: dict = {"env": env}
        if log_dir is not None:
            try:
                err_path = log_dir / "eli_gui_server.stderr.log"
                out_path = log_dir / "eli_gui_server.stdout.log"
                err_fh = open(err_path, "ab", buffering=0)
                out_fh = open(out_path, "ab", buffering=0)
                popen_kw["stdout"] = out_fh
                popen_kw["stderr"] = err_fh
            except Exception:
                popen_kw["stdout"] = subprocess.DEVNULL
                popen_kw["stderr"] = subprocess.DEVNULL
        else:
            popen_kw["stdout"] = subprocess.DEVNULL
            popen_kw["stderr"] = subprocess.DEVNULL
        # Hide a second console flash when we are NOT using the console sibling.
        if sys.platform == "win32" and not use_console_sibling:
            try:
                popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        try:
            self._srv_proc = subprocess.Popen(args, **popen_kw)
        except Exception as e:
            self._srv_err = str(e)
            for fh in (err_fh, out_fh):
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
            return False
        # Keep file handles alive for the child lifetime.
        self._srv_proc_log_fhs = (out_fh, err_fh)
        self._srv_thread = None
        self._srv_uv = None
        self._srv_url.setText(url)
        https_url = ""
        if lan:
            hport = env.get("ELI_API_HTTPS_PORT") or "8443"
            lan_ip = self._eli_server_lan_ip()
            https_url = f"https://{lan_ip}:{hport}/#token={token}"
            try:
                self._srv_https_url.setText(https_url)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            self._eli_server_refresh_firewall_hint()
            if not http_qr:
                http_qr = f"http://{lan_ip}:{port}/#token={token}"
        else:
            try:
                self._srv_https_url.clear()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        self._eli_server_refresh_qr(http_url=http_qr, https_url=https_url)
        self._eli_server_update_ui("starting")
        if lan:
            try:
                QDesktopServices.openUrl(QUrl(url))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        return True

    def _eli_server_apply_port(self) -> None:
        """Persist the chosen API port and clear stale env so the next Start uses it."""
        try:
            port = int(self._srv_port_spin.value())
            if not (1024 <= port <= 65535):
                raise ValueError("Port must be between 1024 and 65535.")
            from eli.core import config
            config.set("api_port", port)
            import os
            os.environ.pop("ELI_API_PORT", None)
            QMessageBox.information(
                self, "Server port",
                f"Server port set to {port}.\n\nStop and Start the server for it to take effect.")
        except Exception as ex:
            QMessageBox.warning(self, "Server port", str(ex))

    def _eli_server_stop(self) -> None:
        self._eli_server_stop_https()
        uv = getattr(self, "_srv_uv", None)
        if uv is not None:
            try:
                uv.should_exit = True
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        self._srv_uv = None
        self._srv_thread = None
        proc = getattr(self, "_srv_proc", None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            self._srv_proc = None
        for fh in getattr(self, "_srv_proc_log_fhs", ()) or ():
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
        self._srv_proc_log_fhs = ()
        import os
        os.environ.pop("ELI_API_PORT", None)
        os.environ.pop("ELI_API_HOST", None)
        self._srv_url.clear()
        try:
            self._eli_server_refresh_qr()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self._eli_server_update_ui("stopped")

    def _eli_server_rotate_token(self) -> None:
        """Mint a fresh LAN access token, retiring every currently-paired device.

        The running server reads ELI_API_TOKEN LIVE (see api/server.py), so the new
        token takes effect immediately — old links go 401 at once. If the server is
        up in phone/Wi-Fi mode we refresh the displayed URL so you can re-pair; if
        it's stopped or loopback-only, the new token is simply persisted for the
        next LAN launch.
        """
        import os
        try:
            from api.api_token import rotate_token
            token = rotate_token()  # persists to disk + exports ELI_API_TOKEN
        except Exception as e:
            self._eli_server_update_ui("error", f"token rotate failed: {e}")
            return
        running = bool(
            (getattr(self, "_srv_thread", None) and self._srv_thread.is_alive())
            or (
                getattr(self, "_srv_proc", None) is not None
                and self._srv_proc.poll() is None
            )
        )
        lan = os.environ.get("ELI_API_HOST", "") == "0.0.0.0"
        if running and lan:
            port = int(os.environ.get("ELI_API_PORT", "8081"))
            self._srv_url.setText(
                f"http://{self._eli_server_lan_ip()}:{port}/?open=connect#token={token}")
            hport = os.environ.get("ELI_API_HTTPS_PORT", "")
            https_url = (
                f"https://{self._eli_server_lan_ip()}:{hport}/#token={token}" if hport else "")
            if https_url:
                self._srv_https_url.setText(https_url)
            self._eli_server_refresh_qr(
                http_url=f"http://{self._eli_server_lan_ip()}:{port}/#token={token}",
                https_url=https_url,
            )
            try:
                self._srv_status.setText("● Token rotated — re-open the new link on each phone")
                self._srv_status.setStyleSheet("font-size:12px;font-weight:700;color:#ebcb8b;")
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        else:
            try:
                self._srv_status.setText("● Token rotated — applies on next phone/Wi-Fi launch")
                self._srv_status.setStyleSheet("font-size:12px;font-weight:700;color:#ebcb8b;")
            except Exception:
                log.debug("suppressed exception", exc_info=True)

    def _eli_server_poll(self) -> None:
        proc = getattr(self, "_srv_proc", None)
        if proc is not None:
            code = proc.poll()
            if code is not None:
                self._srv_proc = None
                err = getattr(self, "_srv_err", "") or (
                    f"server process exited ({code})" if code else ""
                )
                if not err:
                    self._srv_url.clear()
                self._eli_server_update_ui("error" if err else "stopped", err[:160])
                return
            import os as _os, socket
            try:
                port = int(_os.environ.get("ELI_API_PORT", "8081"))
            except Exception:
                port = 8081
            up = False
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                    up = True
            except Exception:
                up = False
            self._eli_server_update_ui("running" if up else "starting")
            return
        th = getattr(self, "_srv_thread", None)
        if th is None:
            return
        if not th.is_alive():  # thread exited (error / stopped)
            self._srv_thread = None
            err = getattr(self, "_srv_err", "")
            if err and ("98" in err or "already in use" in err.lower()):
                try:
                    from eli.runtime.server_util import probe_eli_server
                    import os as _os
                    port = int(_os.environ.get("ELI_API_PORT", "8081"))
                    probe = probe_eli_server(port)
                    lan = _os.environ.get("ELI_API_HOST", "") == "0.0.0.0"
                    if probe.get("in_use") and self._eli_server_show_running_dialog(probe, lan):
                        if lan:
                            self._eli_server_refresh_firewall_hint()
                        return
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            if not err:
                self._srv_url.clear()
            self._eli_server_update_ui("error" if err else "stopped", err[:160])
            return
        import os as _os, socket
        try:
            port = int(_os.environ.get("ELI_API_PORT", "8081"))
        except Exception:
            port = 8081
        up = False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                up = True
        except Exception:
            up = False
        self._eli_server_update_ui("running" if up else "starting")

    def _eli_server_update_ui(self, state: str, detail: str = "") -> None:
        running = state in ("running", "starting")
        try:
            self._srv_btn_local.setEnabled(not running)
            self._srv_btn_lan.setEnabled(not running)
            self._srv_btn_stop.setEnabled(running)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        text, colour = {
            "stopped": ("● Stopped", "#bf616a"),
            "starting": ("● Starting…", "#ebcb8b"),
            "running": ("● Running — open the address below", "#a3be8c"),
            "error": (f"● Error: {detail}", "#bf616a"),
        }.get(state, ("● Stopped", "#bf616a"))
        try:
            self._srv_status.setText(text)
            self._srv_status.setStyleSheet(f"font-size:12px;font-weight:700;color:{colour};")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def current_provider(self) -> str:
        if not hasattr(self, 'provider_combo'):
            return 'bundled_gguf'
        return str(self.provider_combo.currentData() or 'bundled_gguf')

    def on_provider_changed(self):
        provider = self.current_provider()
        bundled_enabled = provider == 'bundled_gguf'
        custom_enabled = provider == 'custom_gguf'
        ollama_enabled = provider == 'ollama'
        self.bundled_model_combo.setEnabled(bundled_enabled)
        self.model_path_input.setEnabled(custom_enabled)
        self.ollama_host_input.setEnabled(ollama_enabled)
        self.ollama_model_combo.setEnabled(ollama_enabled)

    def apply_recommended_setup(self):
        models = discover_gguf_models()
        try:
            ollama_models = self.ollama_manager.list_models(self.ollama_host_input.text().strip())
        except Exception:
            ollama_models = []
        rec = recommend_model_setup(models, detect_system_capabilities(), ollama_models)
        provider = rec.get('provider', 'bundled_gguf')
        idx = self.provider_combo.findData(provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        if provider == 'bundled_gguf' and rec.get('path'):
            j = self.bundled_model_combo.findData(rec['path'])
            if j >= 0:
                self.bundled_model_combo.setCurrentIndex(j)
        elif provider == 'ollama' and rec.get('ollama_model'):
            self.ollama_model_combo.setEditText(rec['ollama_model'])
        self.system_recommendation_label.setText(rec.get('reason', 'Recommendation applied.'))
        self.status_signal.emit('Recommendation applied')

    def maybe_run_first_time_setup(self):
        try:
            # Skip auto-load if the launcher already wired a model
            if getattr(self, 'active_backend', None) and getattr(self.active_backend, 'is_loaded', False):
                return
            if getattr(self, "_startup_model_prompt_done", False):
                return
            self._startup_model_prompt_done = True

            if hasattr(self, "startup_model_picker_checkbox"):
                self._show_startup_model_picker = bool(self.startup_model_picker_checkbox.isChecked())

            if self._show_startup_model_picker:
                self._run_startup_model_picker()
                # Model loading is selection-driven from the picker itself.
                # Never fall through to auto-load when picker is enabled.
                return

            if self.auto_load_checkbox.isChecked():
                self.load_model()
        except Exception as e:
            log.debug(f'[FIRST RUN] setup warning: {e}')

    def _text_backend_ready(self, notify: bool = True):
        backend = self.get_active_backend()
        if backend and getattr(backend, 'is_loaded', False):
            return backend
        # While the model loads a message waits for it (ConversationMixin._take_message); this
        # answered it with "Still loading the model" and dropped it.
        if notify:
            try:
                _eli_color = getattr(self, '_eli_text_color', '#88c0d0')
                self.chat_display.append(
                    f'\n<b><span style="color:{_eli_color};">ELI:</span></b>'
                    f'<br>No model is loaded yet. Open the <b>Settings</b> tab '
                    f'or use the <b>Model</b> menu to load one.<br>'
                )
            except Exception:
                log.debug('[WARN] Model not loaded')
        return None

    def get_active_backend(self):
        return self.ollama_manager if self.current_provider() == 'ollama' else self.model_manager

    def _resolve_dropped_attachments(self, raw_text: str, dropped_paths: list) -> str:
        """Expand [Image:], [File:], [PDF:] tags into inline content for LLM context."""
        import re as _re
        from pathlib import Path as _P

        # Extract tags from the raw text
        tag_pattern = _re.compile(r'\[(Image|File|PDF):\s*(.+?)\]')
        extras = []
        for m in tag_pattern.finditer(raw_text):
            kind, path = m.group(1), m.group(2).strip()
            p = _P(path)
            if not p.exists():
                continue
            if kind == "Image":
                extras.append(f"[Attached image: {p.name} — path: {path}]")
            else:
                # A dropped file becomes its PATH ONLY — never its inlined content. Every
                # file action (FIX_FILE / EXAMINE_CODE / SUMMARIZE_FILE / ANALYZE_PDF)
                # resolves the path on disk itself, so inlining the content was redundant
                # AND harmful: it bloated the prompt and mis-routed "fix <file>" into the
                # codebase-audit path on the pasted text (the file's body looked like a
                # paste to audit, not a path to fix). Drop = the path; the action reads it.
                extras.append(f"[File: {path}]")

        # Strip the tags from the visible message, append content
        clean = tag_pattern.sub("", raw_text).strip()
        if extras:
            clean = clean + "\n\n" + "\n\n".join(extras)
        return clean

    def new_conversation(self):
        if self.conversation_history:
            self.save_conversation()
        self.clear_chat()

    def save_conversation(self, quiet: bool = False):
        """One file per conversation, rewritten as it grows: quietly after each reply when auto-save is
        on. It was written only on close, and a crash lost the whole evening's conversation."""
        if not self.conversation_history or (quiet and not getattr(self, "_autosave_enabled", True)):
            return
        filename = getattr(self, "_conversation_file", None)
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = self._conversation_file = CONVERSATIONS_DIR / f"conversation_{timestamp}.json"
        try:
            part = filename.with_suffix(".json.part")
            with open(part, 'w', encoding='utf-8') as f:
                json.dump({
                    "timestamp": now_timestamp(),
                    "messages": list(self.conversation_history)
                }, f, indent=2, ensure_ascii=False)
            part.replace(filename)
            if not quiet:
                self.status_signal.emit(f"Conversation saved: {filename.name}")
        except Exception as e:
            if quiet:
                log.debug("conversation autosave failed", exc_info=True)
            else:
                QMessageBox.warning(self, "Save Error", f"Failed to save: {str(e)}")

    def show_recent_memories(self):
        self.memory_results_display.clear()
        self.memory_results_display.append("<b>Recent Memories:</b><br><br>")
        results = self.memory_system.get_recent(limit=20)
        if results:
            for i, mem in enumerate(results, 1):
                ts = mem.get('timestamp') or mem.get('ts','')
                if isinstance(ts, float):
                    import datetime
                    ts = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                self.memory_results_display.append(
                    f"<b>{i}. [{ts}] ({mem.get('kind','?')})</b><br>"
                    f"{mem.get('text','')}<br>"
                    f"<i>Tags: {mem.get('tags','')}</i><br><br>"
                )
        else:
            self.memory_results_display.append("<i>No memories found.</i>")

    def _update_suggestions_display(self, html: str):
        from datetime import datetime as _dt
        import re as _re

        event_no = int(getattr(self, "_proactive_event_count", 0) or 0) + 1
        self._proactive_event_count = event_no
        stamp = _dt.now().strftime("%H:%M:%S")
        plain = _re.sub(r'<[^>]+>', '', html)[:300]

        entry = (
            f"<div style='margin:6px 0;padding:8px;border-left:3px solid #88c0d0;'>"
            f"<b>Proactive #{event_no} · {stamp}</b><br>{html}</div>"
        )
        self.suggestions_display.append(entry)

        # Update tab label and status bar
        try:
            for _i in range(self.tabs.count()):
                if "Proactive" in self.tabs.tabText(_i):
                    self.tabs.setTabText(_i, f"🎯 Proactive ({event_no})")
                    break
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self._update_proactive_status_label()

        # Forward to ProactiveDock (hidden by default — don't auto-show or auto-speak)
        if self._proactive_dock is not None:
            self._proactive_dock.post_message(f"[{stamp}] #{event_no}: {plain}")
            # TTS only if user explicitly opened dock AND toggled TTS on
            if self._proactive_dock.isVisible() and self._proactive_dock.tts_toggle.isChecked():
                try:
                    from eli.perception.tts_router import maybe_speak
                    maybe_speak(plain, enabled=True)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

    def _update_confidence_meta_label(self):
        """Refresh the status-bar confidence/grounding badge from engine._last_request_meta."""
        label = getattr(self, "_confidence_meta_label", None)
        if label is None:
            return
        try:
            ce = getattr(self, "_cognitive_engine", None)
            meta = dict(getattr(ce, "_last_request_meta", {}) or {}) if ce is not None else {}
            if not meta:
                label.setText("")
                return
            agg = meta.get("aggregated_confidence") or meta.get("confidence")
            grnd = meta.get("grounding_confidence")
            lbl = meta.get("confidence_label", "")
            action = meta.get("result_action") or meta.get("action") or ""
            parts = []
            if lbl == "phatic_skip":
                parts.append("phatic (no retrieval)")
            if agg is not None and lbl != "phatic_skip":
                try:
                    _a = float(agg)
                    parts.append(f"conf {_a:.2f}" + (f" ({lbl})" if lbl else ""))
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            if grnd is not None:
                try:
                    _g = float(grnd)
                    grnd_tag = "✓" if _g >= 0.40 else ("~" if _g >= 0.10 else "⚠")
                    parts.append(f"grounding {grnd_tag}{_g:.2f}")
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            if action:
                parts.append(action)
            label.setText("  " + "  |  ".join(parts) + "  " if parts else "")
            label.setToolTip(
                f"Last response metadata:\n"
                + "\n".join(f"  {k}: {v}" for k, v in sorted(meta.items()))
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _update_summary_display(self, html: str):
        self.summaries_display.clear()
        self.summaries_display.append(html)

    def _update_insights_display(self, html: str):
        self.insights_display.clear()
        self.insights_display.append(html)

    def generate_suggestions(self):
        backend = self._text_backend_ready(notify=False)
        if backend is None:
            self.status_signal.emit('⚠️ Load a model first to generate suggestions.')
            return

        self.suggestions_display.clear()
        self.suggestions_display.append('<i>🔮 Generating grounded suggestions — please wait...</i>')

        def worker():
            try:
                g = self._build_proactive_ground_truth()

                # Build rich context from actual data
                parts = []

                conv_lines = [
                    f"{'User' if m['role']=='user' else 'ELI'}: {m['content'][:200]}"
                    for m in self.conversation_history[-8:]
                ]
                if conv_lines:
                    parts.append("RECENT CONVERSATION:\n" + "\n".join(conv_lines))

                if g["memories"]:
                    mem_lines = [f"• {m.get('text','')[:160]}" for m in g["memories"][:6]]
                    parts.append(f"RETRIEVED MEMORIES (FTS5+FAISS, {g['faiss_count']} vectors indexed):\n"
                                 + "\n".join(mem_lines))

                if g["daemon_patterns"]:
                    pat_lines = [f"• [{p.get('type','')}] {p.get('suggestion','')} "
                                 f"{'(×'+str(p['count'])+')' if p.get('count') else ''}"
                                 for p in g["daemon_patterns"][:5]]
                    parts.append("DETECTED USAGE PATTERNS:\n" + "\n".join(pat_lines))

                if g["habit_rules"]:
                    rule_lines = [f"• {r.get('name','?')} @ "
                                  f"{r.get('hour',0):02d}:{r.get('minute',0):02d}"
                                  for r in g["habit_rules"][:4]]
                    parts.append("ACTIVE HABIT RULES:\n" + "\n".join(rule_lines))

                if g["agent_errors"]:
                    err_lines = [f"• {e.get('error','')[:100]} (×{e.get('occurrence_count',1)})"
                                 for e in g["agent_errors"][:4]]
                    parts.append("RECENT ERRORS (self-improvement backlog):\n" + "\n".join(err_lines))

                if g["bus_context"]:
                    parts.append(f"AGENT INTELLIGENCE:\n{g['bus_context'][:800]}")

                if g["agent_obs"]:
                    obs_lines = [f"• {str(o.get('suggestion') or o.get('phrase') or o)[:120]}"
                                 for o in g["agent_obs"][:4]]
                    parts.append("PROACTIVE OBSERVATIONS:\n" + "\n".join(obs_lines))

                grounded = "\n\n".join(parts) if parts else "No runtime data available yet."
                stats = g["memory_stats"]

                system = (
                    "You are ELI — a fully local AI assistant with persistent memory and self-awareness. "
                    "Generate 3-5 SPECIFIC, ACTIONABLE suggestions grounded ONLY in the data below. "
                    "Reference actual topics, memory items, patterns, errors, and habits by name. "
                    "NO generic advice. Every suggestion must cite its source data. "
                    "Format: numbered list. Each item: one clear action + why (from the data)."
                )
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content":
                     f"Runtime context:\n{grounded}\n\n"
                     f"Memory: {stats.get('total',0)} entries, {g['faiss_count']} vectors. "
                     f"Session: {len(self.conversation_history)} turns.\n\n"
                     "What should I focus on next?"},
                ]
                temp = self.temperature_input.value() if hasattr(self, "temperature_input") else 0.5
                response = backend.chat(messages=messages, max_tokens=640, temperature=temp)

                src_note = (f"<small>Sources: {len(g['memories'])} memory hits · "
                            f"{len(g['daemon_patterns'])} patterns · "
                            f"{len(g['agent_errors'])} errors · "
                            f"{g['faiss_count']} FAISS vectors · AgentBus active</small>")
                self.proactive_suggestions_signal.emit(
                    f"<b>💡 ELI Proactive Suggestions</b><br>{src_note}<hr><br>{response}")
            except Exception as e:
                self.proactive_suggestions_signal.emit(f"❌ Error: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def generate_summary(self):
        backend = self._text_backend_ready(notify=False)
        if backend is None:
            self.status_signal.emit('⚠️ Load a model first to summarize.')
            return
        if not self.conversation_history:
            QMessageBox.information(self, 'No Conversation', 'No conversation to summarize.')
            return

        self.summaries_display.clear()
        self.summaries_display.append('📝 Generating grounded summary...')

        def worker():
            try:
                g = self._build_proactive_ground_truth()

                # Full conversation transcript
                conv_text = "\n".join(
                    f"{'User' if m['role']=='user' else 'ELI'}: {m['content'][:300]}"
                    for m in self.conversation_history
                )

                # Semantic memories triggered by this session
                mem_block = ""
                if g["memories"]:
                    mem_block = "\n\nRelated stored memories:\n" + "\n".join(
                        f"• {m.get('text','')[:180]}" for m in g["memories"][:5])

                # Agent insights
                bus_block = f"\n\nAgent context:\n{g['bus_context'][:600]}" if g["bus_context"] else ""

                system = (
                    "You are ELI. Produce a precise, factual summary of this conversation. "
                    "Extract: main topics, decisions made, open questions, action items. "
                    "Reference specific things said. No filler. No generic openers."
                )
                user_content = (
                    f"CONVERSATION TRANSCRIPT:\n{conv_text}"
                    f"{mem_block}{bus_block}\n\n"
                    f"Session: {len(self.conversation_history)} turns. "
                    f"Memory: {g['memory_stats'].get('total',0)} stored entries."
                )
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_content},
                ]
                temp = self.temperature_input.value() if hasattr(self, "temperature_input") else 0.4
                response = backend.chat(messages=messages, max_tokens=700, temperature=temp)

                self.proactive_summary_signal.emit(
                    f"<b>📝 Conversation Summary</b> "
                    f"<small>({len(self.conversation_history)} turns · "
                    f"{g['memory_stats'].get('total',0)} memories · "
                    f"{g['faiss_count']} FAISS vectors)</small><hr><br>{response}")
            except Exception as e:
                self.proactive_summary_signal.emit(f"❌ Error: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def analyze_context(self):
        backend = self._text_backend_ready(notify=False)
        if backend is None:
            self.status_signal.emit('⚠️ Load a model first to analyze context.')
            return

        self.insights_display.clear()
        self.insights_display.append('🔬 Analyzing full ELI runtime context...')

        def worker():
            try:
                g = self._build_proactive_ground_truth()
                stats = g["memory_stats"]

                parts = []

                # Memory system state
                mem_block = (
                    f"MEMORY SYSTEM STATE:\n"
                    f"• Total memories: {stats.get('total',0)}\n"
                    f"• FAISS vectors indexed: {g['faiss_count']}\n"
                    f"• Memory by kind: {stats.get('by_kind',{})}\n"
                    f"• Conversation turns: {len(self.conversation_history)} (session)"
                )
                parts.append(mem_block)

                if g["memories"]:
                    parts.append("TOP RECALLED MEMORIES (FTS5+FAISS):\n" + "\n".join(
                        f"• [{m.get('_source','?')}] {m.get('text','')[:160]}"
                        for m in g["memories"][:6]))

                if g["daemon_patterns"]:
                    parts.append("DETECTED BEHAVIOURAL PATTERNS:\n" + "\n".join(
                        f"• [{p.get('type','')}] {p.get('suggestion','')} "
                        f"{'(×'+str(p['count'])+')' if p.get('count') else ''}"
                        for p in g["daemon_patterns"][:6]))

                if g["habit_rules"]:
                    parts.append("ACTIVE HABITS:\n" + "\n".join(
                        f"• {r.get('name','?')} at {r.get('hour',0):02d}:{r.get('minute',0):02d}"
                        for r in g["habit_rules"][:4]))

                if g["agent_errors"]:
                    parts.append("SELF-IMPROVEMENT BACKLOG (failures):\n" + "\n".join(
                        f"• {e.get('error','')[:100]} (×{e.get('occurrence_count',1)})"
                        for e in g["agent_errors"][:5]))

                if g["agent_improvements"]:
                    parts.append("RECENT IMPROVEMENTS LOGGED:\n" + "\n".join(
                        f"• [{i.get('category','')}] {i.get('detail','')[:120]}"
                        for i in g["agent_improvements"][:4]))

                if g["bus_context"]:
                    parts.append(f"AGENT BUS INTELLIGENCE:\n{g['bus_context'][:900]}")

                grounded = "\n\n".join(parts)

                system = (
                    "You are ELI — a local AI that inspects its own runtime state. Analyse the runtime state below. "
                    "Identify: cognitive strengths, memory gaps, failure patterns, "
                    "habit opportunities, and areas for self-improvement. "
                    "Be analytical and specific. Reference actual data. No generic observations."
                )
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": f"RUNTIME STATE:\n{grounded}"},
                ]
                temp = self.temperature_input.value() if hasattr(self, "temperature_input") else 0.6
                response = backend.chat(messages=messages, max_tokens=700, temperature=temp)

                self.proactive_insights_signal.emit(
                    f"<b>🔬 ELI Runtime Analysis</b> "
                    f"<small>({stats.get('total',0)} memories · {g['faiss_count']} vectors · "
                    f"{len(g['daemon_patterns'])} patterns · {len(g['agent_errors'])} errors)</small>"
                    f"<hr><br>{response}")
            except Exception as e:
                self.proactive_insights_signal.emit(f"❌ Error: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def _focus_tab_widget(self, widget) -> None:
        try:
            idx = self.tabs.indexOf(widget)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _load_path_into_main_ide(self, path: Path) -> bool:
        try:
            content = path.read_text(encoding='utf-8', errors='replace')
            if QSCI_AVAILABLE:
                self.code_editor.setText(content)
            else:
                self.code_editor.setPlainText(content)
            self.current_file_path = str(path)
            self.current_file_label.setText(f"File: {path.name}")
            self._focus_tab_widget(getattr(self, "_ide_widget", None))
            self.status_signal.emit(f"Opened in IDE: {path.name}")
            return True
        except Exception as e:
            self.status_signal.emit(f"Failed to open generated script in IDE: {e}")
            return False

    def _load_path_into_labs_sim_ide(self, path: Path) -> bool:
        try:
            labs = getattr(self, "_labs_widget", None)
            sim = getattr(labs, "_sim_ide_tab", None)
            if labs is None or sim is None:
                return False
            content = path.read_text(encoding='utf-8', errors='replace')
            if hasattr(sim, "_set_code"):
                sim._set_code(content)
            elif hasattr(sim, "_editor"):
                editor = sim._editor
                if hasattr(editor, "setText"):
                    editor.setText(content)
                else:
                    editor.setPlainText(content)
            else:
                return False
            try:
                sim._current_file = path
                sim._file_label.setText(str(path))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            try:
                inner = getattr(labs, "_inner_tabs", None)
                if inner is not None:
                    idx = inner.indexOf(sim)
                    if idx >= 0:
                        inner.setCurrentIndex(idx)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            self._focus_tab_widget(labs)
            self.status_signal.emit(f"Opened in Labs Sim/IDE: {path.name}")
            return True
        except Exception as e:
            log.debug(f"[GUI] Labs Sim/IDE open failed: {e}")
            return False

    def _open_generated_artifact_from_result(self, result) -> None:
        if not isinstance(result, dict):
            return

        action = str(result.get("action") or "").upper()
        if action not in {
            "GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT",
            "FIX_FILE", "GENERATE_DOCUMENT", "CREATE_DOCUMENT",
            "CREATE_DOC", "WRITE_DOCUMENT",
        }:
            return

        raw_path = (
            result.get("script_path")
            or result.get("doc_path")
            or result.get("path")
            or result.get("file")
        )
        if not raw_path:
            return

        path = Path(str(raw_path)).expanduser()
        if not path.exists() or not path.is_file():
            self.status_signal.emit(f"Generated artifact path not found: {path}")
            return

        suffix = path.suffix.lower()
        wants_labs = bool(result.get("open_in_labs"))
        wants_ide = bool(result.get("open_in_ide"))
        is_script_action = action in {
            "GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT",
            "GENERATE_CODE", "WRITE_CODE", "FIX_FILE",
        }

        # Every generated script goes to the IDE tab — no extension whitelist.
        if is_script_action:
            if wants_labs and suffix == ".py" and self._load_path_into_labs_sim_ide(path):
                return
            self._load_path_into_main_ide(path)
            return

        if suffix in {".md", ".txt", ".log"}:
            try:
                self.open_text_file(path)
                self.status_signal.emit(f"Opened document: {path.name}")
            except Exception as e:
                self.status_signal.emit(f"Failed to open generated document: {e}")
            return

        if suffix == ".pdf":
            try:
                self.open_pdf(path)
                self.status_signal.emit(f"Opened PDF: {path.name}")
            except Exception as e:
                self.status_signal.emit(f"Failed to open generated PDF: {e}")
            return

    def ide_new_file(self):
        if QSCI_AVAILABLE:
            self.code_editor.clear()
        else:
            self.code_editor.clear()
        self.current_file_path = None
        self.current_file_label.setText("New file (unsaved)")

    def ide_open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open File",
            str(Path.home()),
            "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                if QSCI_AVAILABLE:
                    self.code_editor.setText(content)
                else:
                    self.code_editor.setPlainText(content)
                self.current_file_path = file_path
                self.current_file_label.setText(f"File: {Path(file_path).name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to open file: {str(e)}")

    def ide_save_file(self):
        if not self.current_file_path:
            self.ide_save_as()
            return
        try:
            if QSCI_AVAILABLE:
                content = self.code_editor.text()
            else:
                content = self.code_editor.toPlainText()
            with open(self.current_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self.status_signal.emit(f"Saved: {Path(self.current_file_path).name}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file: {str(e)}")

    def ide_save_as(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save As",
            str(Path.home()),
            "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            self.current_file_path = file_path
            self.ide_save_file()
            self.current_file_label.setText(f"File: {Path(file_path).name}")

    def ide_run_code(self):
        if not self.current_file_path:
            QMessageBox.warning(self, "No File", "Please save the file first.")
            return
        self.console_output.clear()
        self.console_output.append(f"Running: {Path(self.current_file_path).name}\n")
        def run_worker():
            try:
                result = subprocess.run(
                    [sys.executable, self.current_file_path],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                output = result.stdout + result.stderr
                self.console_output.append(output)
                if result.returncode == 0:
                    self.console_output.append("\n✅ Execution completed successfully")
                else:
                    self.console_output.append(f"\n❌ Execution failed with code {result.returncode}")
            except subprocess.TimeoutExpired:
                self.console_output.append("\n⏱️  Execution timeout (30s)")
            except Exception as e:
                self.console_output.append(f"\n❌ Error: {str(e)}")
        thread = threading.Thread(target=run_worker, daemon=True)
        thread.start()

    def open_text_file(self, path: Path):
        if self._load_into_labs_file_chat(path):
            return
        # Fallback: floating read-only viewer
        try:
            content = path.read_text(encoding='utf-8', errors='replace')
            dlg = QDialog(self)
            dlg.setWindowTitle(str(path.name))
            dlg.resize(800, 600)
            v = QVBoxLayout(dlg)
            t = QTextEdit()
            t.setReadOnly(True)
            t.setPlainText(content)
            v.addWidget(t)
            btn = QPushButton("Close")
            btn.clicked.connect(dlg.accept)
            v.addWidget(btn)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open file: {str(e)}")

    def open_pdf(self, path: Path):
        text = ""
        try:
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                for page in reader.pages[:10]:
                    text += page.extract_text() + "\n\n"
            except ImportError:
                try:
                    import PyPDF2
                    with open(path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        for page in reader.pages[:10]:
                            text += page.extract_text() + "\n\n"
                except ImportError:
                    text = (
                        f"PDF viewing requires pypdf or PyPDF2.\n\n"
                        f"Install with: pip install pypdf\n\nFile: {path}"
                    )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open PDF: {str(e)}")
            return
        # Write extracted text to a temp .txt and load into Labs File Chat
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', prefix=path.stem + '_',
                delete=False, encoding='utf-8'
            ) as tmp:
                tmp.write(text)
                tmp_path = Path(tmp.name)
            if self._load_into_labs_file_chat(tmp_path):
                return
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # Fallback: floating viewer
        dlg = QDialog(self)
        dlg.setWindowTitle(str(path.name))
        dlg.resize(800, 600)
        v = QVBoxLayout(dlg)
        t = QTextEdit()
        t.setReadOnly(True)
        t.setPlainText(text)
        v.addWidget(t)
        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        v.addWidget(btn)
        dlg.exec()

    def browse_directory(self, path: str):
        self.file_tree.setRootIndex(self.file_model.index(path))
        self.path_label.setText(f"Current: {path}")

    def browse_project_root(self):
        project_root = Path(__file__).parent.parent.parent.parent
        self.browse_directory(str(project_root))

    def on_file_double_click(self, index):
        path = self.file_model.filePath(index)
        path_obj = Path(path)
        if path_obj.is_file():
            if path_obj.suffix == '.py':
                self.tabs.setCurrentIndex(3)
                try:
                    content = path_obj.read_text(encoding='utf-8')
                    if QSCI_AVAILABLE:
                        self.code_editor.setText(content)
                    else:
                        self.code_editor.setPlainText(content)
                    self.current_file_path = str(path_obj)
                    self.current_file_label.setText(f"File: {path_obj.name}")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to open: {str(e)}")
            elif path_obj.suffix in ['.txt', '.md', '.pdf', '.log']:
                self.tabs.setCurrentIndex(4)
                if path_obj.suffix == '.pdf':
                    self.open_pdf(path_obj)
                else:
                    self.open_text_file(path_obj)

    def show_about(self):
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h2>{APP_NAME}</h2>"
            f"<p>Version {APP_VERSION}</p>"
            f"<p>Local-first AI assistant. Runs any configured GGUF model; "
            f"Ollama can be used when selected.</p>"
            f"<hr>"
            f"<p>Features:</p>"
            f"<ul>"
            f"<li>Local GGUF model inference</li>"
            f"<li>SQLite memory system</li>"
            f"<li>Integrated code editor</li>"
            f"<li>Proactive suggestions</li>"
            f"<li>Document viewer</li>"
            f"<li>File browser</li>"
            f"<li>Habits scheduler</li>"
            f"</ul>"
        )

    def _append_log(self, message: str):
        log.debug(f"[LOG] {message}")

    def _update_status(self, message: str):
        if message == 'Send enabled':
            self.send_btn.setEnabled(True)
            self.send_btn.setStyleSheet(getattr(self, '_send_btn_style', ''))
            self._note_waiting()
            return
        if message == 'Generating':
            # Send stays Send (a message meanwhile runs at once or waits its turn); Stop is its own
            self.send_btn.setEnabled(True)
            if getattr(self, "stop_btn", None) is not None:
                self.stop_btn.show()
            return
        if message == 'Turn ended':
            if getattr(self, "stop_btn", None) is not None:
                self.stop_btn.hide()
            self._on_turn_ended()
            return
        if message == 'Take next message':     # the model has finished loading (or failed to)
            self._drain_waiting()
            return
        if message == 'Send disabled':
            self.send_btn.setEnabled(False)
            return
        if message.startswith('🟢 Model ready:'):
            rest = message.split(':', 1)[1].strip()
            import re as _re_status
            _m = _re_status.match(
                r'^(.+?)\s*\(ctx=(\d+)\s+gpu=([^)]+?)\s+batch=(\d+)\)\s*$',
                rest,
            )
            if _m:
                _name, _ctx, _gpu, _batch = _m.groups()
                self.model_info_label.setText(
                    f"🟢 {_name.strip()} | ctx={_ctx} | gpu={_gpu.strip()} | batch={_batch}"
                )
            else:
                self.model_info_label.setText(f"🟢 {rest}")
            self.send_btn.setEnabled(True)
            self.status_label.setText("🟢 Ready")
            return
        elif message.startswith('🔴 Model:'):
            self.model_info_label.setText(message)
            self.send_btn.setEnabled(True)
            self._note_waiting()
            self.status_label.setText("🔴 No model")
            return
        self.status_label.setText(message)
