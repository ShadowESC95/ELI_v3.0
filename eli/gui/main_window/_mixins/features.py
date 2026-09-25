"""FeaturesMixin — 29 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403
from eli.gui.main_window.widgets import _MiniTelemetryGraph


class FeaturesMixin:
    def _on_gaze_toggled(self, checked: bool):
        """Start/stop the webcam gaze engine. When on, voice clicks act where
        the user is looking (see GAZE_CLICK)."""
        try:
            if checked:
                from eli.perception.gaze_engine import start_gaze_engine, needs_calibration
                res = start_gaze_engine() or {}
                if not res.get("ok"):
                    # Failed to start (no camera / cv2 / gaze core) — revert the toggle.
                    self.gaze_btn.blockSignals(True)
                    self.gaze_btn.setChecked(False)
                    self.gaze_btn.blockSignals(False)
                    self.gaze_btn.setText("🎯 Gaze: OFF")
                    _err = res.get("error") or res.get("message") or "Gaze engine failed to start"
                    self.status_signal.emit(f"🎯 Gaze unavailable: {_err}")
                    self.gaze_btn.setToolTip(f"Gaze cursor — unavailable: {_err}")
                    log.debug(f"[GUI] gaze start failed: {_err}")
                    return
                self.gaze_btn.setText("🎯 Gaze: ON")
                try:
                    if needs_calibration():
                        self.status_signal.emit(
                            "🎯 Gaze ON — not calibrated yet; say 'calibrate gaze' for steps.")
                    else:
                        self.status_signal.emit(
                            "🎯 Gaze ON — look at something and say 'left click' / 'open it'.")
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            else:
                from eli.perception.gaze_engine import stop_gaze_engine
                stop_gaze_engine()
                self.gaze_btn.setText("🎯 Gaze: OFF")
                self.status_signal.emit("🎯 Gaze tracking off.")
            log.debug(f"[GUI] gaze toggled -> {checked}")
        except Exception as e:
            log.debug(f"[GUI] gaze toggle failed: {e}")
            try:
                self.gaze_btn.blockSignals(True)
                self.gaze_btn.setChecked(False)
                self.gaze_btn.blockSignals(False)
                self.gaze_btn.setText("🎯 Gaze: OFF")
            except Exception:
                log.debug("suppressed exception", exc_info=True)

    def _toggle_proactive_dock(self, checked: bool = None):
        """Show or hide the Proactive Dock. Wired to View → Proactive Dock
        (Ctrl+Shift+P)."""
        try:
            dock = getattr(self, "_proactive_dock", None)
            if dock is None:
                # Lazy-attach if it failed at startup.
                try:
                    from eli.gui.docks.proactive_dock import ProactiveDock
                    dock = ProactiveDock(self)
                    self._proactive_dock = dock
                    self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
                except Exception as _err:
                    log.debug(f"[GUI] ProactiveDock attach failed: {_err}")
                    return
            if checked is None:
                checked = not dock.isVisible()
            dock.setVisible(bool(checked))
            if checked:
                dock.raise_()
            try:
                self._toggle_proactive_action.setChecked(bool(checked))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        except Exception as _e:
            log.debug(f"[GUI] toggle proactive dock failed: {_e}")

    def refresh_habit_list(self):
        """Refresh the habits table from memory."""
        try:
            rules = memory_system.get_habit_rules(enabled_only=False)
            self.habit_table.setRowCount(len(rules))
            for i, rule in enumerate(rules):
                # ID
                id_item = QTableWidgetItem(str(rule["id"]))
                self.habit_table.setItem(i, 0, id_item)
                # Name
                name_item = QTableWidgetItem(rule.get("name", ""))
                self.habit_table.setItem(i, 1, name_item)
                # Command
                cmd_item = QTableWidgetItem(rule.get("command", ""))
                self.habit_table.setItem(i, 2, cmd_item)
                # Time (HH:MM)
                time_str = f"{rule.get('hour', 0):02d}:{rule.get('minute', 0):02d}"
                time_item = QTableWidgetItem(time_str)
                self.habit_table.setItem(i, 3, time_item)
                # Days
                days = rule.get("days")
                if days is None:
                    days_str = "Every day"
                else:
                    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    days_str = ", ".join(day_names[d] for d in days if 0 <= d < 7)
                days_item = QTableWidgetItem(days_str)
                self.habit_table.setItem(i, 4, days_item)
                # Status — Active when enabled, otherwise an ELI suggestion awaiting
                # the user's approval (or a habit the user switched off).
                enabled = rule.get("enabled", False)
                status_item = QTableWidgetItem("✅ Active" if enabled else "💡 Suggested (off)")
                self.habit_table.setItem(i, 5, status_item)
                # Enabled (checkbox) = approve/activate
                chk = QCheckBox()
                chk.setChecked(enabled)
                chk.stateChanged.connect(lambda state, rid=rule["id"]: self.toggle_habit_rule(rid, state == Qt.Checked))
                self.habit_table.setCellWidget(i, 6, chk)
            self.habit_info.setText(f"Total habits: {len(rules)}")
        except Exception as e:
            self.habit_info.setText(f"Error loading habits: {e}")

    def _habit_rule_dialog(self, title: str, prefill: dict = None):
        """Shared add/edit dialog. Returns (name, command, hour, minute, days) or None."""
        prefill = prefill or {}
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QFormLayout(dialog)

        name_edit = QLineEdit(str(prefill.get("name", "")))
        layout.addRow("Name:", name_edit)

        command_edit = QLineEdit(str(prefill.get("command", "")))
        layout.addRow("Command:", command_edit)

        hour_spin = QSpinBox()
        hour_spin.setRange(0, 23)
        hour_spin.setValue(int(prefill.get("hour", 0) or 0))
        layout.addRow("Hour (0-23):", hour_spin)

        minute_spin = QSpinBox()
        minute_spin.setRange(0, 59)
        minute_spin.setValue(int(prefill.get("minute", 0) or 0))
        layout.addRow("Minute (0-59):", minute_spin)

        # Days of week (multi-select)
        days_group = QGroupBox("Days (leave empty for every day)")
        days_layout = QHBoxLayout()
        day_checkboxes = []
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        _pre_days = set(prefill.get("days") or [])
        for i, dname in enumerate(day_names):
            cb = QCheckBox(dname)
            cb.setChecked(i in _pre_days)
            days_layout.addWidget(cb)
            day_checkboxes.append(cb)
        days_group.setLayout(days_layout)
        layout.addRow(days_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = name_edit.text().strip()
        command = command_edit.text().strip()
        if not (name and command):
            QMessageBox.warning(self, "Error", "Name and command are both required.")
            return None
        days = [i for i, cb in enumerate(day_checkboxes) if cb.isChecked()] or None
        return name, command, hour_spin.value(), minute_spin.value(), days

    def add_habit_rule(self):
        """Open a dialog to add a new (active) habit rule. Manual adds are enabled
        immediately — the user is explicitly creating it."""
        vals = self._habit_rule_dialog("Add Habit Rule")
        if not vals:
            return
        name, command, hour, minute, days = vals
        try:
            rule_id = memory_system.add_habit_rule(name, command, hour, minute, days, enabled=True)
            if rule_id and rule_id > 0:
                self.refresh_habit_list()
                self.status_signal.emit(f"Habit '{name}' added.")
            else:
                QMessageBox.warning(self, "Error", "Failed to add habit rule.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to add habit: {e}")

    def edit_habit_rule(self):
        """Edit the selected habit rule via a prefilled dialog."""
        selected = self.habit_table.currentRow()
        if selected < 0:
            QMessageBox.information(self, "No Selection", "Please select a habit to edit.")
            return
        try:
            rule_id = int(self.habit_table.item(selected, 0).text())
        except Exception:
            return
        prefill = {}
        try:
            for rule in memory_system.get_habit_rules(enabled_only=False):
                if int(rule.get("id", -1)) == rule_id:
                    prefill = {
                        "name": rule.get("name", ""), "command": rule.get("command", ""),
                        "hour": rule.get("hour", 0), "minute": rule.get("minute", 0),
                        "days": rule.get("days"),
                    }
                    break
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        vals = self._habit_rule_dialog("Edit Habit Rule", prefill)
        if not vals:
            return
        name, command, hour, minute, days = vals
        try:
            ok = memory_system.update_habit_rule(
                rule_id, name=name, command=command, hour=hour, minute=minute, days=days)
            if ok:
                self.refresh_habit_list()
                self.status_signal.emit(f"Habit '{name}' updated.")
            else:
                QMessageBox.warning(self, "Error", "Failed to update habit rule.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to update habit: {e}")

    def delete_habit_rule(self):
        """Delete the selected habit rule."""
        selected = self.habit_table.currentRow()
        if selected < 0:
            QMessageBox.information(self, "No Selection", "Please select a habit to delete.")
            return
        rule_id_item = self.habit_table.item(selected, 0)
        if not rule_id_item:
            return
        rule_id = int(rule_id_item.text())
        name_item = self.habit_table.item(selected, 1)
        name = name_item.text() if name_item else "Unknown"
        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete habit '{name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                memory_system.delete_habit_rule(rule_id)
                self.refresh_habit_list()
                self.status_signal.emit(f"Habit '{name}' deleted.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to delete habit: {e}")

    def toggle_habit_rule(self, rule_id: int, enabled: bool):
        """Toggle a habit rule's enabled state."""
        try:
            memory_system.toggle_habit_rule(rule_id, enabled)
            # Optionally refresh the whole table to reflect changes
            self.refresh_habit_list()
        except Exception as e:
            print(f"Failed to toggle habit {rule_id}: {e}")

    def _toggle_proactive_daemon(self):
        daemon = getattr(self, "_proactive_daemon", None)
        if daemon is None:
            return
        if daemon.paused:
            daemon.resume()
            self._proactive_toggle_btn.setText("⏸ Pause Proactive")
            self._proactive_toggle_btn.setStyleSheet(
                "background-color: #2a6030; color: #e8ffe8; border-radius: 4px; padding: 4px 12px;"
            )
            self._proactive_status_label.setText("Proactive: Active")
        else:
            daemon.pause()
            self._proactive_toggle_btn.setText("▶ Resume Proactive")
            self._proactive_toggle_btn.setStyleSheet(
                "background-color: #602020; color: #ffe8e8; border-radius: 4px; padding: 4px 12px;"
            )
            self._proactive_status_label.setText("Proactive: Paused")

    def _live_monitor_panel(self, title: str, accent: str) -> Dict[str, Any]:
        panel = QFrame()
        panel.setObjectName("runtimeMonitorCard")
        panel.setStyleSheet(
            "QFrame#runtimeMonitorCard{background:#151924;border:1px solid #2b3445;border-radius:10px;}"
            "QLabel{background:transparent;}"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        name = QLabel(title)
        name.setStyleSheet("color:#8eaac8;font-size:10px;font-weight:700;letter-spacing:0.7px;")
        value = QLabel("--%")
        value.setStyleSheet(f"color:{accent};font-size:20px;font-weight:800;")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(value)
        layout.addLayout(top)

        detail = QLabel("Waiting for telemetry...")
        detail.setStyleSheet("color:#d6dce8;font-size:12px;font-weight:600;")
        layout.addWidget(detail)

        subtext = QLabel("No live sample yet.")
        subtext.setWordWrap(True)
        subtext.setStyleSheet("color:#6f8098;font-size:10px;")
        layout.addWidget(subtext)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setTextVisible(False)
        progress.setFixedHeight(8)
        progress.setStyleSheet(
            "QProgressBar{background:#0f131b;border:1px solid #202737;border-radius:4px;}"
            f"QProgressBar::chunk{{background:{accent};border-radius:4px;}}"
        )
        layout.addWidget(progress)

        graph = _MiniTelemetryGraph(accent)
        layout.addWidget(graph)

        return {
            "widget": panel,
            "value": value,
            "detail": detail,
            "subtext": subtext,
            "progress": progress,
            "graph": graph,
        }

    def _build_live_runtime_monitor_section(self, parent_layout):
        card = QFrame()
        card.setObjectName("liveRuntimeMonitor")
        card.setStyleSheet(
            "QFrame#liveRuntimeMonitor{background:#1e2230;border-radius:8px;}"
            "QLabel{background:transparent;}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("LIVE SYSTEM MONITOR")
        title.setStyleSheet("color:#5e81ac;font-size:9px;font-weight:700;letter-spacing:0.8px;")
        layout.addWidget(title)

        self.runtime_monitor_summary_label = QLabel("Sampling live CPU, RAM, and VRAM telemetry...")
        self.runtime_monitor_summary_label.setWordWrap(True)
        self.runtime_monitor_summary_label.setStyleSheet("color:#c8d0e0;font-size:12px;font-weight:600;")
        layout.addWidget(self.runtime_monitor_summary_label)

        self.runtime_monitor_meta_label = QLabel("Refresh cadence: 2s")
        self.runtime_monitor_meta_label.setStyleSheet("color:#6f8098;font-size:10px;")
        layout.addWidget(self.runtime_monitor_meta_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self._cpu_monitor = self._live_monitor_panel("CPU LOAD", "#88c0d0")
        self._ram_monitor = self._live_monitor_panel("RAM USAGE", "#a3be8c")
        self._vram_monitor = self._live_monitor_panel("VRAM USAGE", "#ebcb8b")

        grid.addWidget(self._cpu_monitor["widget"], 0, 0)
        grid.addWidget(self._ram_monitor["widget"], 0, 1)
        grid.addWidget(self._vram_monitor["widget"], 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        parent_layout.addWidget(card)

    def _start_runtime_monitoring(self):
        if self._runtime_stats_timer is not None:
            return
        try:
            import psutil
            psutil.cpu_percent(interval=None)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        self._runtime_stats_timer = QTimer(self)
        self._runtime_stats_timer.setInterval(2000)
        self._runtime_stats_timer.timeout.connect(self.refresh_runtime_telemetry)
        self.refresh_runtime_telemetry()
        self._runtime_stats_timer.start()

    def _start_live_data_monitoring(self):
        """Phase 7: keep memory / proactive panels live without the user
        clicking Refresh. Memory counts update every 30 s (cheap SQLite read);
        proactive daemon status updates every 5 s (in-process flag check).

        Each tick is wrapped in try/except so a transient read error never
        crashes the GUI loop. The same callbacks are still exposed for
        manual Refresh buttons."""
        # Memory stats — SQLite count read; safe to call on the main thread
        # because it's a single SELECT and finishes in < 5 ms.
        if self._memory_stats_timer is None:
            def _tick_memory():
                try:
                    self.refresh_memory_stats()
                except Exception as exc:
                    log.debug(f"[GUI] memory-stats tick failed (non-fatal): {exc}")
            self._memory_stats_timer = QTimer(self)
            self._memory_stats_timer.setInterval(30_000)
            self._memory_stats_timer.timeout.connect(_tick_memory)
            self._memory_stats_timer.start()

        # Proactive status label — pure in-memory flag check, very cheap.
        if self._proactive_status_timer is None:
            def _tick_proactive():
                try:
                    self._update_proactive_status_label()
                    self._check_proactive_daemon_crash()
                except Exception as exc:
                    log.debug(f"[GUI] proactive-status tick failed (non-fatal): {exc}")
            self._proactive_status_timer = QTimer(self)
            self._proactive_status_timer.setInterval(5_000)
            self._proactive_status_timer.timeout.connect(_tick_proactive)
            self._proactive_status_timer.start()

    def _read_live_runtime_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "timestamp": now_hms(),
            "cpu_percent": 0.0,
            "cpu_threads": int(os.cpu_count() or 1),
            "ram_total_gb": 0.0,
            "ram_used_gb": 0.0,
            "ram_free_gb": 0.0,
            "ram_percent": 0.0,
            "gpu_present": False,
            "gpu_name": "No GPU telemetry",
            "gpu_util_percent": None,
            "vram_total_mb": 0,
            "vram_used_mb": 0,
            "vram_free_mb": 0,
            "vram_percent": 0.0,
        }
        try:
            import psutil
            vm = psutil.virtual_memory()
            stats["cpu_percent"] = float(psutil.cpu_percent(interval=None))
            stats["ram_total_gb"] = vm.total / (1024 ** 3)
            stats["ram_used_gb"] = vm.used / (1024 ** 3)
            stats["ram_free_gb"] = vm.available / (1024 ** 3)
            stats["ram_percent"] = float(vm.percent)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,memory.total,memory.free,utilization.gpu,name",
                        "--format=csv,noheader,nounits",
                    ],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                ).decode().strip().splitlines()
                if out:
                    parts = [p.strip() for p in out[0].split(",")]
                    used_mb = int(float(parts[0]))
                    total_mb = int(float(parts[1]))
                    free_mb = int(float(parts[2]))
                    util = None
                    if len(parts) > 3 and parts[3] not in ("", "[N/A]", "N/A"):
                        util = float(parts[3])
                    name = parts[4] if len(parts) > 4 else "NVIDIA GPU"
                    stats.update({
                        "gpu_present": True,
                        "gpu_name": name,
                        "gpu_util_percent": util,
                        "vram_used_mb": used_mb,
                        "vram_total_mb": total_mb,
                        "vram_free_mb": free_mb,
                        "vram_percent": (used_mb / total_mb * 100.0) if total_mb > 0 else 0.0,
                    })
            except Exception:
                stats["gpu_name"] = "GPU detected, live VRAM probe failed"
        elif self.detected_system_info.get("has_gpu"):
            stats["gpu_name"] = "GPU detected, live VRAM telemetry unavailable"

        self.detected_system_info.update({
            "cpu_count": stats["cpu_threads"],
            "total_ram_gb": stats["ram_total_gb"] or self.detected_system_info.get("total_ram_gb", 0.0),
            "available_ram_gb": stats["ram_free_gb"] or self.detected_system_info.get("available_ram_gb", 0.0),
            "has_gpu": stats["gpu_present"] or self.detected_system_info.get("has_gpu", False),
            "vram_mb": stats["vram_free_mb"] or self.detected_system_info.get("vram_mb", 0),
        })
        return stats

    def _update_runtime_monitor_panel(
        self,
        panel: Dict[str, Any],
        percent: float,
        detail: str,
        subtext: str,
        history: deque,
        available: bool = True,
    ):
        pct = max(0.0, min(100.0, float(percent or 0.0)))
        panel["value"].setText(f"{pct:.0f}%" if available else "--")
        panel["detail"].setText(detail)
        panel["subtext"].setText(subtext)
        panel["progress"].setValue(int(round(pct)) if available else 0)
        panel["graph"].set_values(list(history) if available and history else [])

    def refresh_runtime_telemetry(self):
        stats = self._read_live_runtime_stats()

        self._runtime_stat_history["cpu"].append(stats["cpu_percent"])
        self._runtime_stat_history["ram"].append(stats["ram_percent"])
        if stats["gpu_present"]:
            self._runtime_stat_history["vram"].append(stats["vram_percent"])

        if not hasattr(self, "_cpu_monitor"):
            return

        self.runtime_monitor_summary_label.setText(
            f"CPU {stats['cpu_threads']} threads visible · "
            f"RAM {stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} GB used · "
            f"{stats['gpu_name']}"
        )
        self.runtime_monitor_meta_label.setText(
            f"Live sample: {stats['timestamp']} · Refresh cadence: 2s"
        )

        self._update_runtime_monitor_panel(
            self._cpu_monitor,
            stats["cpu_percent"],
            f"{stats['cpu_percent']:.1f}% current CPU load",
            f"{stats['cpu_threads']} logical threads available to the runtime.",
            self._runtime_stat_history["cpu"],
            available=True,
        )
        self._update_runtime_monitor_panel(
            self._ram_monitor,
            stats["ram_percent"],
            f"{stats['ram_used_gb']:.1f} / {stats['ram_total_gb']:.1f} GB RAM used",
            f"{stats['ram_free_gb']:.1f} GB available right now.",
            self._runtime_stat_history["ram"],
            available=stats["ram_total_gb"] > 0,
        )

        if stats["gpu_present"] and stats["vram_total_mb"] > 0:
            util_txt = (
                f"GPU core util {stats['gpu_util_percent']:.0f}%"
                if stats["gpu_util_percent"] is not None else
                "GPU core utilization unavailable"
            )
            self._update_runtime_monitor_panel(
                self._vram_monitor,
                stats["vram_percent"],
                f"{stats['vram_used_mb'] / 1024.0:.1f} / {stats['vram_total_mb'] / 1024.0:.1f} GB VRAM used",
                f"{util_txt} · {stats['vram_free_mb'] / 1024.0:.1f} GB free on {stats['gpu_name']}.",
                self._runtime_stat_history["vram"],
                available=True,
            )
        else:
            self._update_runtime_monitor_panel(
                self._vram_monitor,
                0.0,
                "No compatible live VRAM telemetry detected.",
                stats["gpu_name"],
                self._runtime_stat_history["vram"],
                available=False,
            )

    def _on_gaze_toggle(self, checked: bool):
        self.save_settings(silent=True)
        if checked:
            self._gaze_start_clicked()
        else:
            self._gaze_stop_clicked()

    def _gaze_start_clicked(self):
        try:
            from eli.perception.gaze_engine import start_gaze_engine
            cam = getattr(self, "gaze_camera_input", None)
            camera = cam.text().strip() if cam else "auto"
            if camera not in ("auto",) and not camera.isdigit():
                camera = "auto"
            result = start_gaze_engine(camera=camera if camera == "auto" else int(camera))
            msg = result.get("message", "")
            if not result.get("ok") and not result.get("already_running"):
                QMessageBox.warning(self, "Gaze Engine", msg)
        except Exception as e:
            QMessageBox.warning(self, "Gaze Engine", str(e))
        self._refresh_gaze_status()

    def _gaze_stop_clicked(self):
        try:
            from eli.perception.gaze_engine import stop_gaze_engine
            stop_gaze_engine()
        except Exception as e:
            QMessageBox.warning(self, "Gaze Engine", str(e))
        self._refresh_gaze_status()

    def _gaze_calibrate_info(self):
        try:
            import subprocess, sys as _sys
            from eli.perception.gaze_engine import get_calibration_path, needs_calibration
            cal_path = get_calibration_path()
            script = str(
                __import__("pathlib").Path(__file__).resolve().parents[2]
                / "experimental" / "eli_ar_avatar_kit" / "scripts"
                / "eli_gaze_calibrate_plus.py"
            )
            exists_note = "✅ Calibration file exists." if not needs_calibration() else "⚠ No calibration file yet."
            QMessageBox.information(
                self,
                "Gaze Calibration",
                f"{exists_note}\n\n"
                f"To calibrate, run in a terminal:\n\n"
                f"  python {script} --points 25\n\n"
                f"Saved to:\n  {cal_path}",
            )
        except Exception as e:
            QMessageBox.information(self, "Gaze Calibration", str(e))

    def _refresh_gaze_status(self):
        try:
            from eli.perception.gaze_engine import get_gaze_status
            st = get_gaze_status()
            running = st.get("running", False)
            calibrated = st.get("calibrated", False)
            if hasattr(self, "_gaze_status_label"):
                if running:
                    self._gaze_status_label.setText("● running")
                    self._gaze_status_label.setStyleSheet("color:#88c0d0;font-size:11px;font-weight:600;")
                else:
                    self._gaze_status_label.setText("● stopped")
                    self._gaze_status_label.setStyleSheet("color:#6b7a90;font-size:11px;")
            if hasattr(self, "_gaze_cal_label"):
                if calibrated:
                    self._gaze_cal_label.setText(f"Calibration: ✅  {st.get('calibration_path','')}")
                    self._gaze_cal_label.setStyleSheet("color:#a3be8c;font-size:10px;")
                else:
                    self._gaze_cal_label.setText("Calibration: ⚠  not found — run: gaze calibrate")
                    self._gaze_cal_label.setStyleSheet("color:#bf616a;font-size:10px;")
        except Exception:
            if hasattr(self, "_gaze_status_label"):
                self._gaze_status_label.setText("● unavailable")

    def clear_memory_dialog(self):
        """Confirm + run a full memory/identity reset via eli.core.memory_reset.
        Backed up first; schema preserved; requires a restart to fully take effect."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Clear Memory / Factory Reset")
        box.setText("Wipe ALL of ELI's learned memory and identity?")
        box.setInformativeText(
            "This clears the databases (rows only — table schema is kept), the "
            "semantic vector index, the learned user profile, conversation logs, "
            "persona overlay, device labels, and the stored name. Full DB structure "
            "is rebuilt immediately. Everything is backed up first.\n\n"
            "Restart ELI afterwards so in-memory caches reload cleanly."
        )
        keep_profile = QCheckBox("Keep my name && profile (clear history only)")
        box.setCheckBox(keep_profile)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.button(QMessageBox.StandardButton.Yes).setText("Clear Memory")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        try:
            from eli.core.memory_reset import run_reset
            s = run_reset(keep_profile=keep_profile.isChecked(),
                          keep_conversations=False, do_backup=True)
        except Exception as e:
            QMessageBox.critical(self, "Reset failed", f"Could not clear memory:\n{e}")
            return

        rb = s.get("rebuild") or {}
        msg = (
            f"Wiped {s.get('db_rows', 0)} memory rows.\n"
            f"Reset {s.get('faiss_reset', 0)} vector-index file(s).\n"
            f"Removed {s.get('profiles_removed', 0)} profile file(s), "
            f"{s.get('conversations_removed', 0)} conversation log(s), "
            f"{s.get('runtime_wiped', 0)} runtime cache file(s).\n"
            f"Persona overlay {'reset' if s.get('persona_overlay_reset') else 'unchanged'}.\n"
            f"Name {'kept' if keep_profile.isChecked() else 'cleared'}.\n"
            f"DB architecture rebuilt ({rb.get('init_steps', 0)} steps).\n\n"
            f"Backup: {s.get('backup') or '(none)'}\n\n"
            "Please restart ELI now for a clean slate."
        )
        if s.get("errors"):
            msg += f"\n\nNote: {s['errors']}"
        QMessageBox.information(self, "Memory cleared", msg)
        try:
            self.status_label.setText("🧹 Memory cleared — restart ELI for a clean slate")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def refresh_memory_stats(self):
        stats = memory_system.get_stats()
        total = stats.get("total", 0)
        by_kind = stats.get("by_kind", {})
        stats_text = f"Total Memories: {total}\n\n"
        stats_text += "By Type:\n"
        for kind, count in by_kind.items():
            stats_text += f"  • {kind}: {count}\n"
        self.memory_stats_label.setText(stats_text)

    def search_memory(self):
        query = self.memory_search_input.text().strip()
        if not query:
            return
        self.memory_results_display.clear()
        self.memory_results_display.append(f"<b>Searching for:</b> {query}<br><br>")
        results = memory_system.search(query, limit=20)
        if results:
            self.memory_results_display.append(f"<b>Found {len(results)} results:</b><br><br>")
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

    def store_memory(self):
        text = self.memory_store_input.toPlainText().strip()
        if not text:
            return
        tags_text = self.memory_tags_input.text().strip()
        tags = [t.strip() for t in tags_text.split(",")] if tags_text else []
        if memory_system.store(text, tags=tags):
            QMessageBox.information(self, "Success", "Memory stored successfully!")
            self.memory_store_input.clear()
            self.memory_tags_input.clear()
            self.refresh_memory_stats()
        else:
            QMessageBox.warning(self, "Error", "Failed to store memory.")

    def _update_proactive_status_label(self):
        """Refresh the status-bar proactive indicator."""
        label = getattr(self, "_proactive_status_label", None)
        if label is None:
            return
        daemon = getattr(self, "_proactive_daemon", None)
        # daemon being set means it was started; running flag lags by one thread tick
        if daemon is not None:
            count = getattr(self, "_proactive_event_count", 0)
            label.setText(f"  🟢 Proactive: active  |  events: {count}  ")
            label.setToolTip("ELI proactive daemon is running. See 🎯 Proactive tab.")
        else:
            label.setText("  🔴 Proactive: off  ")
            label.setToolTip("Proactive daemon is not running.")

    def _check_proactive_daemon_crash(self):
        """Check for a proactive daemon crash flag and surface it to the user.

        The proactive daemon writes artifacts/proactive_daemon_down.flag when it
        crashes.  This method reads that flag, shows a status-bar warning with a
        restart button, and clears the flag once acknowledged.
        """
        try:
            from eli.core.paths import get_paths as _gp
            flag_path = _gp().artifacts_dir / "proactive_daemon_down.flag"
            if not flag_path.exists():
                return
            crash_msg = flag_path.read_text(encoding="utf-8", errors="replace").strip()
            flag_path.unlink(missing_ok=True)

            label = getattr(self, "_proactive_status_label", None)
            if label is not None:
                label.setText("  ⚠️ Proactive: crashed  ")
                label.setToolTip(
                    f"Proactive daemon crashed: {crash_msg[:120]}\n"
                    "Click to restart."
                )
                # One-shot: clicking the label attempts to restart the daemon
                try:
                    label.mousePressEvent = lambda _e: self._restart_proactive_daemon()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            log.debug(f"[GUI] Proactive daemon crash detected: {crash_msg[:200]}")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _restart_proactive_daemon(self):
        """Attempt to restart the proactive daemon after a crash."""
        try:
            from eli.planning.proactive_daemon import start_daemon as _start_pd
            self._proactive_daemon = _start_pd()
            log.debug("[GUI] Proactive daemon restarted.")
            self._update_proactive_status_label()
        except Exception as exc:
            log.debug(f"[GUI] Failed to restart proactive daemon: {exc}")

    def _build_proactive_ground_truth(self, query: str = "") -> dict:
        """
        Gather live grounded data from every available ELI subsystem.
        Returns a dict with all evidence for use in proactive methods.
        """
        import sqlite3 as _sq, json as _json
        ground = {
            "memories": [],
            "recent_conv": [],
            "daemon_patterns": [],
            "agent_obs": [],
            "agent_errors": [],
            "agent_improvements": [],
            "bus_context": "",
            "memory_stats": {},
            "faiss_count": 0,
            "habit_rules": [],
        }

        # 1. FTS5 + FAISS memory recall on query / recent topic
        if self._central_memory:
            _q = query or " ".join(
                m.get("content", "")[:100] for m in self.conversation_history[-3:]
                if m.get("role") == "user"
            )
            try:
                ground["memories"] = self._central_memory.recall_memory(_q[:300], limit=10) or []
            except Exception: pass
            try:
                ground["recent_conv"] = self._central_memory.get_recent_conversation(limit=20) or []
            except Exception: pass
            try:
                ground["memory_stats"] = memory_system.get_stats() if memory_system else {}
            except Exception: pass
            try:
                vs = getattr(self._central_memory, "vector_store", None)
                if vs:
                    ground["faiss_count"] = vs.ntotal
            except Exception: pass

        # 2. Proactive daemon patterns (live analysis)
        if self._proactive_daemon:
            try:
                ground["daemon_patterns"] = self._proactive_daemon.analyze_user_patterns() or []
            except Exception: pass
            try:
                mem_h = self._proactive_daemon.user_mem
                if mem_h and hasattr(mem_h, "get_habit_rules"):
                    ground["habit_rules"] = mem_h.get_habit_rules(enabled_only=True) or []
            except Exception: pass

        # 3. Agent DB: errors, improvements, observations
        try:
            from eli.core.paths import agent_db_path as _adp
            _acon = _sq.connect(str(_adp()))
            _acon.row_factory = _sq.Row
            try:
                ground["agent_errors"] = [dict(r) for r in _acon.execute(
                    "SELECT user_input, error, occurrence_count FROM failures "
                    "ORDER BY timestamp DESC LIMIT 8").fetchall()]
            except Exception: pass
            try:
                ground["agent_improvements"] = [dict(r) for r in _acon.execute(
                    "SELECT category, detail FROM improvements "
                    "ORDER BY timestamp DESC LIMIT 8").fetchall()]
            except Exception: pass
            try:
                for (content,) in _acon.execute(
                        "SELECT content FROM observations ORDER BY ts DESC LIMIT 5").fetchall():
                    try:
                        items = _json.loads(content or "[]")
                        ground["agent_obs"].extend(
                            items if isinstance(items, list) else [items])
                    except Exception:
                        ground["agent_obs"].append({"raw": str(content)[:150]})
            except Exception: pass
            _acon.close()
        except Exception: pass

        # 4. AgentBus dispatch (13 specialist agents — SQLite-only, fast)
        try:
            from eli.cognition.agent_bus import get_bus as _gb
            _last_user = next(
                (m["content"] for m in reversed(self.conversation_history)
                 if m.get("role") == "user"), query or "proactive analysis")
            _dr = _gb().dispatch(_last_user, {"action": "CHAT"},
                                 session_id="proactive-gui", user_id="local-user")
            ground["bus_context"] = (_dr.memory_context or "")[:1200]
        except Exception: pass

        return ground
