"""SettingsModelMixin — 46 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class SettingsModelMixin:
    def change_reasoning_mode(self, label: str = 'quick'):
        self._reasoning_mode = self._REASONING_MODE_LABELS.get(label, "quick")
        try:
            self.chat_display.append(
                f'<span style="color:#88c0d0;font-size:11px;">⚙️ Mode: {label}</span><br>'
            )
        except Exception:
            pass

    def _auto_detect_reasoning_mode(self, text: str) -> Optional[str]:
        """Return the canonical reasoning-mode id implied by `text`, or None."""
        if not text:
            return None
        low = text.lower()
        for mode, patterns in self._AUTO_MODE_PATTERNS:
            for pat in patterns:
                if re.search(pat, low):
                    return mode
        return None

    def _maybe_auto_select_reasoning_mode(self, user_text: str) -> None:
        """If auto-mode is on, pick a reasoning mode from `user_text` keywords."""
        if not getattr(self, "_auto_reasoning_mode", False):
            return
        detected = self._auto_detect_reasoning_mode(user_text)
        if not detected:
            return
        if detected == getattr(self, "_reasoning_mode", "quick"):
            return
        # Find a label in the combo that maps to `detected`
        for i in range(self.reasoning_mode_combo.count()):
            label = self.reasoning_mode_combo.itemText(i)
            if self._REASONING_MODE_LABELS.get(label) == detected:
                self.reasoning_mode_combo.blockSignals(True)
                self.reasoning_mode_combo.setCurrentIndex(i)
                self.reasoning_mode_combo.blockSignals(False)
                self._reasoning_mode = detected
                try:
                    self.chat_display.append(
                        f'<span style="color:#88c0d0;font-size:11px;">'
                        f'🤖 Auto-mode → {label}</span><br>'
                    )
                except Exception:
                    pass
                break

    def open_settings_tab(self):
        try:
            idx = self.tabs.indexOf(self._settings_root)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
        except Exception:
            pass

    def open_agent_wizard(self):
        """Open the AgentSpec wizard — same format as Settings ▸ Agents ▸ Create."""
        from eli.gui.panels.agent_wizard import AgentCreateDialog
        dlg = AgentCreateDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._wizard_display_message(
                "Agent specification saved. If it is still disabled, enable it in "
                "**Settings → Agents**, then try a message that matches its triggers."
            )

    def _open_prefilled_agent_spec(self, prefill: dict):
        from eli.gui.panels.agent_wizard import AgentCreateDialog
        dlg = AgentCreateDialog(parent=self, prefill=prefill)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._wizard_display_message(
                "Agent specification saved. Enable it in **Settings → Agents** if needed."
            )

    def _wizard_display_message(self, text: str):
        """Append a wizard ELI message to the chat display (must run on main thread)."""
        ts = now_hms()
        # Convert markdown bold to HTML bold
        html = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
        html = html.replace("\n", "<br>")
        self.chat_display.append(
            f'<br><b><span style="color:#88c0d0;">🤖 ELI Wizard [{ts}]:</span></b>'
            f'<br><span style="color:#d8dee9;">{html}</span><br>'
        )
        self.chat_display.ensureCursorVisible()

    def _handle_wizard_input(self, user_text: str):
        """Process one wizard answer; advance to next question or generate the agent."""
        if user_text.strip().lower() in ("cancel", "exit", "stop", "quit"):
            self._agent_wizard_state = None
            self._wizard_display_message("Wizard cancelled. Back to normal chat mode.")
            return

        state = self._agent_wizard_state
        state["answers"].append(user_text)
        step = state["step"]
        state["step"] += 1

        if state["step"] < len(self._WIZARD_QUESTIONS):
            # Ask next question
            self.wizard_say_signal.emit(self._WIZARD_QUESTIONS[state["step"]])
        else:
            self._agent_wizard_state = None
            answers = state["answers"]
            self._wizard_display_message(
                "Got all 3 answers — opening the agent specification editor for review."
            )
            from eli.gui.panels.agent_wizard import AgentCreateDialog
            prefill = AgentCreateDialog.prefill_from_legacy_wizard(*answers)
            QTimer.singleShot(0, lambda: self._open_prefilled_agent_spec(prefill))

    def open_advanced_settings(self, tab: int = 0):
        dlg = AdvancedSettingsDialog(parent=self, start_tab=tab)
        dlg.exec()

    def _settings_page(self, title: str, subtitle: str = "") -> tuple:
        """Return (page_widget, vbox) with a compact styled header."""
        page = QWidget()
        page.setStyleSheet("QWidget { background: transparent; }")
        vbox = QVBoxLayout(page)
        vbox.setContentsMargins(20, 14, 20, 12)
        vbox.setSpacing(10)

        h = QLabel(title)
        h.setStyleSheet("font-size:14px;font-weight:700;color:#e8eaf0;")
        vbox.addWidget(h)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet("font-size:10px;color:#606880;margin-top:-4px;")
            s.setWordWrap(True)
            vbox.addWidget(s)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("QFrame{color:#282c38;max-height:1px;}")
        vbox.addWidget(sep)

        return page, vbox

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#8b9ab0;font-size:10px;font-weight:600;letter-spacing:0.3px;")
        return lbl

    def _section_card(self, parent_layout, section_title: str = "") -> QFormLayout:
        """Add a card section, return its QFormLayout."""
        card = QWidget()
        card.setStyleSheet(
            "QWidget{background:#1e2230;border-radius:6px;}"
            "QLabel{color:#c8d0e0;}"
        )
        card_vbox = QVBoxLayout(card)
        card_vbox.setContentsMargins(14, 10, 14, 10)
        card_vbox.setSpacing(7)
        if section_title:
            t = QLabel(section_title)
            t.setStyleSheet("color:#5e81ac;font-size:9px;font-weight:700;letter-spacing:0.8px;")
            card_vbox.addWidget(t)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)
        card_vbox.addLayout(form)
        parent_layout.addWidget(card)
        return form

    def _build_settings_model_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Model",
            "Choose and configure the AI backend ELI uses for inference."
        )

        # Provider card
        form = self._section_card(vbox, "PROVIDER")
        self.provider_combo = QComboBox()
        self.provider_combo.addItem(MODEL_PROVIDER_LABELS['bundled_gguf'], 'bundled_gguf')
        self.provider_combo.addItem(MODEL_PROVIDER_LABELS['custom_gguf'], 'custom_gguf')
        self.provider_combo.addItem(MODEL_PROVIDER_LABELS['ollama'], 'ollama')
        self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)
        form.addRow(self._field_label("Backend"), self.provider_combo)

        self.system_recommendation_label = QLabel("Recommendation pending.")
        self.system_recommendation_label.setWordWrap(True)
        self.system_recommendation_label.setStyleSheet("color:#606880;font-size:12px;")
        form.addRow(self._field_label("Hardware"), self.system_recommendation_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        refresh_btn = QPushButton("Refresh Sources")
        refresh_btn.clicked.connect(self.refresh_model_sources)
        recommend_btn = QPushButton("Detect & Recommend")
        recommend_btn.clicked.connect(self.apply_recommended_setup)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(recommend_btn)
        btn_row.addStretch()
        form.addRow("", btn_row)

        # Bundled card
        form2 = self._section_card(vbox, "BUNDLED GGUF")
        self.bundled_model_combo = QComboBox()
        self.bundled_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        form2.addRow(self._field_label("Model"), self.bundled_model_combo)

        # Custom card
        form3 = self._section_card(vbox, "CUSTOM GGUF")
        self.model_path_input = QLineEdit()
        self.model_path_input.setText(DEFAULT_MODEL_PATH)
        form3.addRow(self._field_label("File path"), self.model_path_input)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.browse_model_file)
        form3.addRow("", browse_btn)

        # Ollama card
        form4 = self._section_card(vbox, "OLLAMA")
        self.ollama_host_input = QLineEdit("http://localhost:11434")
        form4.addRow(self._field_label("Host"), self.ollama_host_input)
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        form4.addRow(self._field_label("Model"), self.ollama_model_combo)
        refresh_ollama_btn = QPushButton("Refresh Ollama Models")
        refresh_ollama_btn.clicked.connect(self.refresh_ollama_models)
        form4.addRow("", refresh_ollama_btn)

        vbox.addStretch()
        return page

    def _reload_voice_selector(self) -> None:
        """Repopulate the voice dropdown after the library dialog installs one.

        Without this the newly downloaded voice exists on disk but the open
        Settings page still shows the old list, so it looks like the download
        did nothing.
        """
        try:
            sel = getattr(self, "_voice_selector", None)
            if sel is None:
                return
            from eli.perception.tts_router import list_voices, get_active_voice
            current = get_active_voice()
            sel.blockSignals(True)
            sel.clear()
            voices = list_voices()
            for v in voices:
                sel.addItem(v)
            if current in voices:
                sel.setCurrentText(current)
            sel.blockSignals(False)
        except Exception:
            log.debug("[TTS] voice selector reload failed", exc_info=True)

    def _build_settings_runtime_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Runtime",
            "Control how the model is loaded and how much hardware it uses."
        )
        form = self._section_card(vbox, "INFERENCE ENGINE")

        self.n_ctx_input = QSpinBox()
        self.n_ctx_input.setRange(512, 32768)
        self.n_ctx_input.setValue(16384)
        self.n_ctx_input.setSingleStep(512)
        self.n_ctx_input.setToolTip("Token context window — larger = more memory")
        form.addRow(self._field_label("Context size"), self.n_ctx_input)

        self.n_threads_input = QSpinBox()
        self.n_threads_input.setRange(1, 64)
        self.n_threads_input.setValue(8)
        form.addRow(self._field_label("CPU threads"), self.n_threads_input)

        self.n_gpu_layers_input = QSpinBox()
        self.n_gpu_layers_input.setRange(0, 9999)
        self.n_gpu_layers_input.setValue(99)
        self.n_gpu_layers_input.setToolTip("Loader parameter requested for GPU offload. Backend support is reported separately. (0 = CPU request, 99 = all-layer request)")
        form.addRow(self._field_label("GPU-layer parameter"), self.n_gpu_layers_input)

        self.batch_size_input = QSpinBox()
        self.batch_size_input.setRange(1, 2048)
        self.batch_size_input.setValue(512)
        self.batch_size_input.setSingleStep(64)
        self.batch_size_input.setToolTip("Prompt processing batch size — larger is faster but uses more VRAM")
        form.addRow(self._field_label("Batch size"), self.batch_size_input)

        # KV-cache quantization. q4_0 cuts KV memory ~75% with negligible
        # quality loss for chat workloads — the unlock for ctx ≥ 16k on
        # small VRAM cards. Empty value = no quantization (fp16).
        self.cache_type_k_combo = QComboBox()
        self.cache_type_k_combo.addItems(["", "q4_0", "q8_0", "f16"])
        self.cache_type_k_combo.setToolTip(
            "KV cache quantization for keys. q4_0 = 4× more ctx for the same "
            "VRAM. Empty = fp16 (no quantization)."
        )
        form.addRow(self._field_label("KV cache K"), self.cache_type_k_combo)

        self.cache_type_v_combo = QComboBox()
        self.cache_type_v_combo.addItems(["", "q4_0", "q8_0", "f16"])
        self.cache_type_v_combo.setToolTip(
            "KV cache quantization for values. Match this to the K setting."
        )
        form.addRow(self._field_label("KV cache V"), self.cache_type_v_combo)

        self.auto_load_checkbox = QCheckBox("Auto-load backend on startup")
        self.auto_load_checkbox.setStyleSheet("color:#c8d0e0;")
        form.addRow("", self.auto_load_checkbox)

        self.startup_model_picker_checkbox = QCheckBox("Show startup model picker")
        self.startup_model_picker_checkbox.setStyleSheet("color:#c8d0e0;")
        self.startup_model_picker_checkbox.setChecked(True)
        form.addRow("", self.startup_model_picker_checkbox)

        self._build_live_runtime_monitor_section(vbox)

        # TTS backend status + voice selector
        tts_form = self._section_card(vbox, "VOICE / TTS")
        try:
            from eli.perception.tts_router import (
                available_backends as _tts_backends,
                list_voices as _list_voices,
                get_active_voice as _get_active_voice,
                set_active_voice as _set_active_voice,
            )
            _be = _tts_backends()
            _status_lines = [
                f"Piper (Python): ✅",
                f"Piper binary:   {'✅ found' if _be.get('piper_bin') else '⚠ not found'}",
                f"espeak-ng:      {'✅' if _be.get('espeak_ng') else '❌'}",
            ]
            _tts_lbl = QLabel("\n".join(_status_lines))
            _tts_lbl.setStyleSheet("color:#8eaac8; font-family:monospace; font-size:11px;")
            tts_form.addRow(_tts_lbl)

            # Voice selector — uses qt_compat-loaded QComboBox (PySide6 / PyQt6 / PyQt5)
            self._voice_selector = QComboBox()
            self._voice_selector.setStyleSheet(
                "QComboBox { background:#1e2535; color:#c8d0e0; border:1px solid #3a4560;"
                " border-radius:4px; padding:4px 8px; min-height:28px; }"
                "QComboBox::drop-down { border:none; }"
                "QComboBox QAbstractItemView { background:#1e2535; color:#c8d0e0; }"
            )
            _voices = _list_voices()
            _active = _get_active_voice()
            for _v in _voices:
                self._voice_selector.addItem(_v)
            if _active in _voices:
                self._voice_selector.setCurrentText(_active)

            def _on_voice_changed(_name: str):
                try:
                    _set_active_voice(_name)
                except Exception:
                    pass

            self._voice_selector.currentTextChanged.connect(_on_voice_changed)

            _voice_row_widget = QWidget()
            _voice_row_layout = QHBoxLayout(_voice_row_widget)
            _voice_row_layout.setContentsMargins(0, 0, 0, 0)
            _voice_row_layout.setSpacing(8)
            _voice_lbl = QLabel("Voice:")
            _voice_lbl.setStyleSheet("color:#8eaac8; min-width:50px;")
            _voice_row_layout.addWidget(_voice_lbl)
            _voice_row_layout.addWidget(self._voice_selector, 1)

            # Voice library — ELI ships a small pack; upstream hosts ~166 across 45
            # languages and voice_assets could already fetch any of them, but until
            # now nothing exposed that to the user. This button is that path.
            _get_btn = QPushButton("Get more…")
            _get_btn.setToolTip(
                "Browse and download additional Piper voices — more English accents, "
                "other languages, and the ideal base voices for the character presets.")

            def _open_voice_library():
                try:
                    from eli.gui.widgets.voice_downloader import VoiceDownloadDialog
                    dlg = VoiceDownloadDialog(self)
                    dlg.voices_changed.connect(self._reload_voice_selector)
                    dlg.exec()
                except Exception as _e:
                    log.debug(f"[TTS] voice library dialog failed: {_e}", exc_info=True)
                    QMessageBox.warning(self, "Voice library",
                                        f"Could not open the voice library:\n{_e}")

            _get_btn.clicked.connect(_open_voice_library)
            _voice_row_layout.addWidget(_get_btn)
            tts_form.addRow(_voice_row_widget)

            # Test voice button
            _test_btn = QPushButton("▶ Test Voice")
            _test_btn.setStyleSheet(
                "QPushButton { background:#1e3a2a; color:#5dbb7a; border:1px solid #2a5a3a;"
                " border-radius:4px; padding:4px 10px; }"
                "QPushButton:hover { background:#254a30; }"
            )
            def _test_voice():
                try:
                    from eli.perception.tts_router import speak
                    speak("ELI voice test. Systems operational.", voice_name=self._voice_selector.currentText())
                except Exception as e:
                    log.debug(f"[TTS] Test error: {e}")
            _test_btn.clicked.connect(_test_voice)
            tts_form.addRow(_test_btn)

        except Exception as _tts_err:
            tts_form.addRow(QLabel(f"TTS status unavailable: {_tts_err}"))

        vbox.addStretch()
        return page

    def _build_settings_generation_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Generation",
            "Tune how ELI generates text — length, creativity, and sampling."
        )
        form = self._section_card(vbox, "SAMPLING")

        self.max_tokens_input = QSpinBox()
        self.max_tokens_input.setRange(128, 4096)
        self.max_tokens_input.setValue(4096)
        self.max_tokens_input.setSingleStep(128)
        form.addRow(self._field_label("Max tokens"), self.max_tokens_input)

        self.temperature_input = QDoubleSpinBox()
        self.temperature_input.setRange(0.0, 2.0)
        self.temperature_input.setValue(0.7)
        self.temperature_input.setSingleStep(0.05)
        self.temperature_input.setToolTip("0 = deterministic · 1 = creative · 2 = chaotic")
        form.addRow(self._field_label("Temperature"), self.temperature_input)

        # Visual temperature slider synced to spinbox
        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 200)
        self.temp_slider.setValue(70)
        self.temp_slider.setToolTip("Drag to adjust temperature")
        self.temp_slider.valueChanged.connect(
            lambda v: self.temperature_input.setValue(v / 100)
        )
        self.temperature_input.valueChanged.connect(
            lambda v: self.temp_slider.setValue(int(v * 100))
        )
        form.addRow("", self.temp_slider)

        vbox.addStretch()
        return page

    def _build_settings_reasoning_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Reasoning",
            "Live parameters for every reasoning mode — budgets, temperature, sampling, "
            "algorithm. Saved values apply from the next request. Re-derive rebuilds "
            "hardware-fitted defaults from the current base tune."
        )
        self._reasoning_preset_fields = {}
        presets = self._load_mode_presets_for_page()

        for mode, title in self._REASONING_PAGE_MODES:
            preset = presets.get(mode) or {}
            if not preset:
                continue
            form = self._section_card(vbox, title.upper())
            keys = [k for k in self._REASONING_KEY_ORDER if k in preset]
            keys += sorted(k for k in preset if k not in keys and k != "voice")
            if "voice" in preset:
                keys.append("voice")
            for key in keys:
                val = preset[key]
                w = None
                if isinstance(val, bool):
                    w = QCheckBox()
                    w.setChecked(val)
                elif isinstance(val, int):
                    w = QSpinBox()
                    w.setRange(-1, 32768)
                    w.setValue(int(val))
                    if "token" in key:
                        w.setSingleStep(64)
                elif isinstance(val, float):
                    w = QDoubleSpinBox()
                    w.setRange(0.0, 8.0)
                    w.setDecimals(2)
                    w.setSingleStep(0.05)
                    w.setValue(float(val))
                elif isinstance(val, str):
                    if len(val) > 60 or key == "voice":
                        w = QTextEdit()
                        w.setPlainText(val)
                        w.setMaximumHeight(64)
                    else:
                        w = QLineEdit(val)
                if w is None:
                    continue
                tip = self._REASONING_KEY_HELP.get(key)
                if tip:
                    w.setToolTip(tip)
                form.addRow(self._field_label(key.replace("_", " ")), w)
                self._reasoning_preset_fields[(mode, key)] = w

        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 Save reasoning presets")
        save_btn.clicked.connect(self._save_reasoning_presets)
        btn_row.addWidget(save_btn)
        rederive_btn = QPushButton("↺ Re-derive from hardware tune")
        rederive_btn.setToolTip(
            "Recompute every mode's defaults from the current base ctx / max_tokens / "
            "temperature (does not save until you press Save)")
        rederive_btn.clicked.connect(self._rederive_reasoning_presets)
        btn_row.addWidget(rederive_btn)
        btn_row.addStretch()
        self._reasoning_presets_status = QLabel("")
        btn_row.addWidget(self._reasoning_presets_status)
        vbox.addLayout(btn_row)

        vbox.addStretch()
        return page

    def _read_reasoning_field(self, w):
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, QSpinBox):
            return int(w.value())
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        if isinstance(w, QTextEdit):
            return w.toPlainText()
        if isinstance(w, QLineEdit):
            return w.text()
        return None

    def _save_reasoning_presets(self):
        try:
            from eli.core.runtime_settings import load_settings as _ls, save_settings as _ss
            s = _ls() or {}
            mp = dict(s.get("mode_presets") or {})
            for (mode, key), w in self._reasoning_preset_fields.items():
                val = self._read_reasoning_field(w)
                if val is None:
                    continue
                mp.setdefault(mode, {})[key] = val
            s["mode_presets"] = mp
            _ss(s)
            self._reasoning_presets_status.setText("✓ saved — live from the next request")
            log.debug("[SETTINGS] reasoning mode presets saved")
        except Exception as e:
            self._reasoning_presets_status.setText(f"✗ save failed: {e}")
            log.debug(f"[SETTINGS] reasoning preset save failed: {e}")

    def _rederive_reasoning_presets(self):
        try:
            from eli.core.runtime_settings import load_settings as _ls
            from eli.core.hardware_profile import _derive_mode_presets
            s = _ls() or {}
            derived = _derive_mode_presets(
                int(s.get("n_ctx") or 8192),
                int(s.get("max_tokens") or 2048),
                float(s.get("temperature") or 0.7))
            for (mode, key), w in self._reasoning_preset_fields.items():
                val = (derived.get(mode) or {}).get(key)
                if val is None:
                    continue
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(val))
                elif isinstance(w, QSpinBox):
                    w.setValue(int(val))
                elif isinstance(w, QDoubleSpinBox):
                    w.setValue(float(val))
                elif isinstance(w, QTextEdit):
                    w.setPlainText(str(val))
                elif isinstance(w, QLineEdit):
                    w.setText(str(val))
            self._reasoning_presets_status.setText("↺ re-derived — press Save to apply")
        except Exception as e:
            self._reasoning_presets_status.setText(f"✗ re-derive failed: {e}")

    def _build_settings_identity_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Identity",
            "Personalise how ELI presents visuals, remembers your preferences, and defaults the image studio."
        )

        form = self._section_card(vbox, "USER PROFILE")
        self.user_name_input = QLineEdit()
        self.user_name_input.setPlaceholderText("Your preferred name")
        form.addRow(self._field_label("Name"), self.user_name_input)

        # How ELI speaks to you — persists as the explicit tone preference that feeds the
        # persona brief (above ELI's auto-inferred tone). Editable: type your own.
        self.communication_style_combo = QComboBox()
        self.communication_style_combo.setEditable(True)
        self.communication_style_combo.addItems([
            "",  # blank = ELI's default, emergent voice
            "blunt, concise, no preamble",
            "warm and encouraging",
            "dry humour, a bit sarcastic",
            "formal and precise",
            "playful and casual",
            "thorough — walk me through the reasoning",
        ])
        self.communication_style_combo.setToolTip(
            "How you want ELI to talk to you (tone / humour). Blank = ELI's default emergent "
            "voice. This takes priority over the tone ELI infers. You can also just tell ELI "
            "in chat, e.g. 'speak to me more bluntly'."
        )
        # An editable QComboBox has no native placeholder support the way QLineEdit
        # does — left unset, a blank (default) value renders as a genuinely empty
        # box with no hint text, unlike every field around it.
        if self.communication_style_combo.lineEdit() is not None:
            self.communication_style_combo.lineEdit().setPlaceholderText(
                "Default — ELI's own emergent voice (or type e.g. blunt, warm, formal...)")
        form.addRow(self._field_label("Tone / how ELI speaks"), self.communication_style_combo)

        self.image_profile_notes_input = QTextEdit()
        self.image_profile_notes_input.setPlaceholderText(
            "Visual identity cues, preferred subjects, moods, brand notes, favourite colours..."
        )
        self.image_profile_notes_input.setMinimumHeight(110)
        form.addRow(self._field_label("Visual notes"), self.image_profile_notes_input)

        form2 = self._section_card(vbox, "IMAGE PERSONALISATION")
        self.image_style_profile_combo = QComboBox()
        self.image_style_profile_combo.addItems(["auto", "balanced", "cinematic", "minimal", "luxury", "neon", "fantasy"])
        form2.addRow(self._field_label("Style bias"), self.image_style_profile_combo)

        self.image_palette_profile_combo = QComboBox()
        self.image_palette_profile_combo.addItems(["auto", "blue_dawn", "crimson_sunset", "emerald_aurora", "golden_storm", "monochrome_luxury", "neon_noir", "rose_steel", "solar_glass", "violet_dusk"])
        form2.addRow(self._field_label("Palette bias"), self.image_palette_profile_combo)

        self.image_auto_personalize_checkbox = QCheckBox("Blend image prompts with user/profile context")
        self.image_auto_personalize_checkbox.setStyleSheet("color:#c8d0e0;")
        form2.addRow("", self.image_auto_personalize_checkbox)

        self.image_use_chat_context_checkbox = QCheckBox("Use recent chat context in image prompting")
        self.image_use_chat_context_checkbox.setStyleSheet("color:#c8d0e0;")
        form2.addRow("", self.image_use_chat_context_checkbox)

        self.image_use_proactive_context_checkbox = QCheckBox("Use proactive patterns and habits in image prompting")
        self.image_use_proactive_context_checkbox.setStyleSheet("color:#c8d0e0;")
        form2.addRow("", self.image_use_proactive_context_checkbox)

        form3 = self._section_card(vbox, "IMAGE STUDIO DEFAULTS")
        self.image_default_project_input = QLineEdit()
        self.image_default_project_input.setPlaceholderText("Optional reference folder for project-aware image generation")
        form3.addRow(self._field_label("Reference folder"), self.image_default_project_input)

        browse_row = QHBoxLayout()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.browse_image_project_folder)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch()
        form3.addRow("", browse_row)

        self.image_default_count_input = QSpinBox()
        self.image_default_count_input.setRange(1, 24)
        self.image_default_count_input.setValue(1)
        form3.addRow(self._field_label("Default batch"), self.image_default_count_input)

        size_row = QHBoxLayout()
        self.image_default_width_input = QSpinBox()
        self.image_default_width_input.setRange(256, 4096)
        self.image_default_width_input.setSingleStep(64)
        self.image_default_width_input.setValue(1400)
        self.image_default_height_input = QSpinBox()
        self.image_default_height_input.setRange(256, 4096)
        self.image_default_height_input.setSingleStep(64)
        self.image_default_height_input.setValue(900)
        size_row.addWidget(self.image_default_width_input)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.image_default_height_input)
        form3.addRow(self._field_label("Default size"), size_row)

        self.image_auto_open_checkbox = QCheckBox("Auto-preview the first generated image")
        self.image_auto_open_checkbox.setStyleSheet("color:#c8d0e0;")
        form3.addRow("", self.image_auto_open_checkbox)

        form4 = self._section_card(vbox, "IMAGE RENDER BACKEND")
        self.image_backend_default_combo = QComboBox()
        self.image_backend_default_combo.addItems(["auto", "diffusion", "procedural"])
        form4.addRow(self._field_label("Backend"), self.image_backend_default_combo)

        self.image_model_path_input = QLineEdit()
        self.image_model_path_input.setPlaceholderText("Path to a local SDXL / Flux / diffusion model directory or .safetensors checkpoint, ideally under models/image/")
        form4.addRow(self._field_label("Model path"), self.image_model_path_input)

        model_btn_row = QHBoxLayout()
        image_model_browse_btn = QPushButton("Browse…")
        image_model_browse_btn.clicked.connect(self.browse_image_model_path)
        model_btn_row.addWidget(image_model_browse_btn)
        model_refresh_btn = QPushButton("Detect Local Models")
        model_refresh_btn.clicked.connect(self.refresh_image_model_sources)
        model_btn_row.addWidget(model_refresh_btn)
        model_btn_row.addStretch()
        form4.addRow("", model_btn_row)

        self.image_device_default_combo = QComboBox()
        self.image_device_default_combo.addItems(["auto", "cuda", "cpu"])
        form4.addRow(self._field_label("Device"), self.image_device_default_combo)

        self.image_quality_default_combo = QComboBox()
        self.image_quality_default_combo.addItems(["draft", "balanced", "ultra", "extreme"])
        self.image_quality_default_combo.currentTextChanged.connect(self._apply_image_default_quality_preset)
        form4.addRow(self._field_label("Quality"), self.image_quality_default_combo)

        default_render_row = QHBoxLayout()
        self.image_steps_default_input = QSpinBox()
        self.image_steps_default_input.setRange(8, 120)
        self.image_steps_default_input.setValue(36)
        self.image_guidance_default_input = QDoubleSpinBox()
        self.image_guidance_default_input.setRange(1.0, 20.0)
        self.image_guidance_default_input.setSingleStep(0.1)
        self.image_guidance_default_input.setValue(7.2)
        default_render_row.addWidget(QLabel("Steps"))
        default_render_row.addWidget(self.image_steps_default_input)
        default_render_row.addSpacing(8)
        default_render_row.addWidget(QLabel("CFG"))
        default_render_row.addWidget(self.image_guidance_default_input)
        form4.addRow(self._field_label("Render"), default_render_row)

        self.image_negative_default_input = QTextEdit()
        self.image_negative_default_input.setPlaceholderText("Default negative prompt for diffusion renders")
        self.image_negative_default_input.setMaximumHeight(90)
        form4.addRow(self._field_label("Negative"), self.image_negative_default_input)

        vbox.addStretch()
        return page

    def _build_settings_audio_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Audio",
            "STT calibration, voice profile, and TTS diagnostics."
        )

        # ── Microphone selector ───────────────────────────────────────────
        mic_card = self._section_card(vbox, "STT — MICROPHONE DEVICE")

        self.mic_device_combo = QComboBox()
        self.mic_device_combo.setToolTip(
            "Select the microphone ELI listens on.\n"
            "Bluetooth headsets appear as PulseAudio sources.\n"
            "Changes take effect the next time the mic is toggled."
        )
        self._populate_mic_device_combo()
        self.mic_device_combo.currentIndexChanged.connect(lambda _: self._on_mic_device_changed(_save=True))
        mic_card.addRow(self._field_label("Device"), self.mic_device_combo)

        refresh_mic_btn = QPushButton("↻ Refresh")
        refresh_mic_btn.setToolTip("Re-scan for new microphones or Bluetooth devices.")
        refresh_mic_btn.clicked.connect(lambda: self._populate_mic_device_combo(restore=True))
        mic_card.addRow("", refresh_mic_btn)

        # Sensitivity
        self.dynamic_energy_checkbox = QCheckBox("Dynamic energy threshold (auto-adjusts to ambient noise)")
        self.dynamic_energy_checkbox.setToolTip(
            "Continuously adapts the voice detection threshold to ambient noise.\n"
            "Enable this if you have to shout or if ELI mishears background noise.\n"
            "Env: ELI_STT_DYNAMIC_ENERGY"
        )
        self.dynamic_energy_checkbox.stateChanged.connect(self._apply_stt_sensitivity)
        mic_card.addRow(self.dynamic_energy_checkbox)

        _thresh_row = QHBoxLayout()
        self.energy_threshold_input = QSpinBox()
        self.energy_threshold_input.setRange(50, 10000)
        self.energy_threshold_input.setValue(1200)
        self.energy_threshold_input.setSingleStep(50)
        self.energy_threshold_input.setToolTip(
            "Minimum audio energy to register as speech.\n"
            "Lower = more sensitive (picks up quiet speech).\n"
            "Higher = less sensitive (ignores background noise).\n"
            "Only used when dynamic threshold is OFF. Env: ELI_STT_ENERGY_THRESHOLD"
        )
        self.energy_threshold_input.valueChanged.connect(self._apply_stt_sensitivity)
        _thresh_row.addWidget(self.energy_threshold_input)
        _thresh_row.addStretch()
        mic_card.addRow(self._field_label("Energy threshold"), _thresh_row)

        # ── Wake word / listen mode ───────────────────────────────────────
        wake_card = self._section_card(vbox, "STT — WAKE WORD SETTINGS")

        self.allow_direct_chat_checkbox = QCheckBox(
            "Allow chat without wake word (direct listen mode)"
        )
        self.allow_direct_chat_checkbox.setToolTip(
            "When enabled, ELI transcribes every phrase as a chat message without\n"
            "requiring 'computer' first. Useful for hands-free or quiet environments.\n"
            "Env: ELI_STT_ALLOW_DIRECT_CHAT"
        )
        self.allow_direct_chat_checkbox.stateChanged.connect(
            lambda _: self._apply_direct_chat_env()
        )
        wake_card.addRow(self.allow_direct_chat_checkbox)

        # ── STT diagnostic panel ─────────────────────────────────────────
        stt_card = self._section_card(vbox, "STT — VOICE PROFILE & CALIBRATION")

        self._stt_diag_label = QLabel("Loading…")
        self._stt_diag_label.setStyleSheet(
            "color:#c8d0e0; padding:6px 10px; "
            "background:#14171f; border:1px solid #2a2d3a; border-radius:4px; "
            "font-family:'Courier New',monospace; font-size:10px;"
        )
        self._stt_diag_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._stt_diag_label.setWordWrap(True)
        stt_card.addRow(self._stt_diag_label)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("↻ Refresh Diagnostics")
        refresh_btn.clicked.connect(self._refresh_stt_diagnostics)
        btn_row.addWidget(refresh_btn)
        reset_btn = QPushButton("🗑 Reset Voice Profile")
        reset_btn.setToolTip("Clear voice_profile.json so STT recalibrates from scratch.")
        reset_btn.clicked.connect(self._reset_voice_profile)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        stt_card.addRow(btn_row)

        # ── TTS diagnostic panel ─────────────────────────────────────────
        tts_card = self._section_card(vbox, "TTS — BACKENDS & VOICE")

        self._tts_diag_label = QLabel("Loading…")
        self._tts_diag_label.setStyleSheet(
            "color:#c8d0e0; padding:6px 10px; "
            "background:#14171f; border:1px solid #2a2d3a; border-radius:4px; "
            "font-family:'Courier New',monospace; font-size:10px;"
        )
        self._tts_diag_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._tts_diag_label.setWordWrap(True)
        tts_card.addRow(self._tts_diag_label)

        tts_btn_row = QHBoxLayout()
        tts_refresh = QPushButton("↻ Refresh TTS Status")
        tts_refresh.clicked.connect(self._refresh_tts_diagnostics)
        tts_btn_row.addWidget(tts_refresh)
        tts_test = QPushButton("🔊 Test Voice")
        tts_test.clicked.connect(self._test_tts_voice)
        tts_btn_row.addWidget(tts_test)
        tts_btn_row.addStretch(1)
        tts_card.addRow(tts_btn_row)

        vbox.addStretch(1)

        # Initial paint, then auto-refresh every 5 seconds while visible.
        self._refresh_stt_diagnostics()
        self._refresh_tts_diagnostics()
        try:
            self._stt_diag_timer = QTimer(self)
            self._stt_diag_timer.setInterval(5000)
            self._stt_diag_timer.timeout.connect(self._refresh_stt_diagnostics)
            self._stt_diag_timer.start()
        except Exception:
            pass
        return page

    def _build_settings_server_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Web Server",
            "Open ELI in a browser on this computer, or on your phone/tablet over your "
            "own Wi-Fi. Inference stays on this machine — the browser is just a screen.",
        )
        self._srv_status = QLabel("● Stopped")
        self._srv_status.setStyleSheet("font-size:12px;font-weight:700;color:#bf616a;")
        vbox.addWidget(self._srv_status)

        row = QHBoxLayout()
        self._srv_btn_local = QPushButton("▶  Start (this computer)")
        self._srv_btn_lan = QPushButton("📱  Start (phone / Wi-Fi)")
        self._srv_btn_stop = QPushButton("■  Stop")
        self._srv_btn_rotate = QPushButton("🔄  Rotate token")
        self._srv_btn_rotate.setToolTip(
            "Mint a fresh access token. Kicks off every currently-paired device — "
            "re-open the link on each phone to pair again.")
        for b, col in ((self._srv_btn_local, "#5e81ac"), (self._srv_btn_lan, "#5e81ac"),
                       (self._srv_btn_stop, "#3b4252"), (self._srv_btn_rotate, "#4c566a")):
            b.setFixedHeight(30)
            b.setStyleSheet(
                f"QPushButton{{background:{col};color:#fff;font-weight:600;border:none;"
                "border-radius:5px;padding:0 14px;font-size:11px;}"
                "QPushButton:hover{background:#81a1c1;}"
                "QPushButton:disabled{background:#2a2d36;color:#5b6275;}")
            row.addWidget(b)
        row.addStretch()
        vbox.addLayout(row)
        self._srv_btn_local.clicked.connect(lambda: self._eli_server_start(False))
        self._srv_btn_lan.clicked.connect(lambda: self._eli_server_start(True))
        self._srv_btn_stop.clicked.connect(self._eli_server_stop)
        self._srv_btn_rotate.clicked.connect(self._eli_server_rotate_token)

        # Server port — move ELI off a busy port. Saved; applied on the next Start.
        port_row = QHBoxLayout()
        _port_lbl = QLabel("Server port:")
        _port_lbl.setStyleSheet("color:#d8dee9;font-size:11px;")
        port_row.addWidget(_port_lbl)
        self._srv_port_spin = QSpinBox()
        self._srv_port_spin.setRange(1024, 65535)
        try:
            from eli.runtime.server_util import effective_api_port
            self._srv_port_spin.setValue(int(effective_api_port()))
        except Exception:
            self._srv_port_spin.setValue(8081)
        self._srv_port_spin.setToolTip(
            "Change if 8081 clashes with another app on your network. Saved; used on the next Start.")
        port_row.addWidget(self._srv_port_spin)
        _port_apply = QPushButton("Apply")
        _port_apply.setFixedHeight(28)
        _port_apply.clicked.connect(self._eli_server_apply_port)
        port_row.addWidget(_port_apply)
        port_row.addStretch()
        vbox.addLayout(port_row)

        self._srv_url = QLineEdit()
        self._srv_url.setReadOnly(True)
        self._srv_url.setPlaceholderText("The address to open will appear here when running…")
        self._srv_url.setStyleSheet(
            "QLineEdit{background:#12141a;color:#a3be8c;border:1px solid #2a2d36;"
            "border-radius:5px;padding:7px;font-size:12px;}")
        vbox.addWidget(self._srv_url)

        qr_title = QLabel("Scan with your phone (Connect tab also has these)")
        qr_title.setStyleSheet("color:#88c0d0;font-size:12px;font-weight:700;margin-top:10px;")
        vbox.addWidget(qr_title)
        qr_row = QHBoxLayout()
        self._srv_qr_http = QLabel("HTTP QR appears when phone/Wi-Fi mode is running…")
        self._srv_qr_http.setAlignment(Qt.AlignCenter)
        self._srv_qr_http.setMinimumSize(200, 200)
        self._srv_qr_https = QLabel("")
        self._srv_qr_https.setAlignment(Qt.AlignCenter)
        self._srv_qr_https.setMinimumSize(200, 200)
        for lab in (self._srv_qr_http, self._srv_qr_https):
            lab.setStyleSheet(
                "QLabel{background:#eafcff;border:1px solid #2a2d36;border-radius:8px;"
                "padding:8px;color:#4c566a;font-size:10px;}")
            qr_row.addWidget(lab)
        qr_row.addStretch()
        vbox.addLayout(qr_row)

        self._srv_https_url = QLineEdit()
        self._srv_https_url.setReadOnly(True)
        self._srv_https_url.setPlaceholderText("HTTPS link for phone microphone (port 8443)…")
        self._srv_https_url.setStyleSheet(
            "QLineEdit{background:#12141a;color:#88c0d0;border:1px solid #2a2d36;"
            "border-radius:5px;padding:7px;font-size:12px;}")
        vbox.addWidget(self._srv_https_url)

        https_note = QLabel(
            "Phone chat uses HTTP (port 8081). Phone microphone needs HTTPS (port 8443) — "
            "accept the one-time “connection not private” warning on the phone. "
            "Do not use https:// on port 8081 (that causes “Invalid HTTP request” errors).")
        https_note.setWordWrap(True)
        https_note.setStyleSheet("color:#606880;font-size:10px;")
        vbox.addWidget(https_note)

        hint = QLabel(
            "“Phone / Wi-Fi” mode is protected by a one-time access token baked into the "
            "link — open it once on the phone and it’s remembered. Keep this on your own "
            "network; don’t expose it to the public internet.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#606880;font-size:10px;")
        vbox.addWidget(hint)

        fw_title = QLabel("Phone can't connect? — Firewall (one-time)")
        fw_title.setStyleSheet("color:#88c0d0;font-size:12px;font-weight:700;margin-top:8px;")
        vbox.addWidget(fw_title)
        self._srv_fw_note = QLabel(
            "If your phone is on the same Wi-Fi but cannot open ELI, your computer's "
            "firewall is usually blocking port 8081. Ask a tech-savvy person to run "
            "the command below once — or copy it and paste into Terminal."
        )
        self._srv_fw_note.setWordWrap(True)
        self._srv_fw_note.setStyleSheet("color:#7b8394;font-size:10px;line-height:1.45;")
        vbox.addWidget(self._srv_fw_note)
        self._srv_fw_cmd = QLineEdit()
        self._srv_fw_cmd.setReadOnly(True)
        self._srv_fw_cmd.setPlaceholderText("Firewall command appears here when phone/Wi-Fi mode is used…")
        self._srv_fw_cmd.setStyleSheet(
            "QLineEdit{background:#12141a;color:#ebcb8b;border:1px solid #2a2d36;"
            "border-radius:5px;padding:7px;font-size:11px;font-family:monospace;}")
        vbox.addWidget(self._srv_fw_cmd)
        fw_row = QHBoxLayout()
        self._srv_fw_copy = QPushButton("Copy command")
        self._srv_fw_term = QPushButton("Open in Terminal")
        for b in (self._srv_fw_copy, self._srv_fw_term):
            b.setFixedHeight(28)
            b.setStyleSheet(
                "QPushButton{background:#3b4252;color:#eceff4;border:none;border-radius:5px;"
                "padding:0 12px;font-size:11px;font-weight:600;}"
                "QPushButton:hover{background:#4c566a;}")
            fw_row.addWidget(b)
        fw_row.addStretch()
        vbox.addLayout(fw_row)
        self._srv_fw_copy.clicked.connect(self._eli_server_copy_firewall_cmd)
        self._srv_fw_term.clicked.connect(self._eli_server_open_firewall_terminal)
        self._eli_server_refresh_firewall_hint()

        vbox.addStretch()

        # Poll the child process so the UI reflects crashes / external stops.
        self._srv_timer = QTimer(self)
        self._srv_timer.setInterval(1500)
        self._srv_timer.timeout.connect(self._eli_server_poll)
        self._srv_timer.start()
        self._eli_server_update_ui("stopped")
        return page

    def _build_settings_app_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Application",
            "General application behaviour — persistence, logging, and theme."
        )
        form = self._section_card(vbox, "BEHAVIOUR")

        self.auto_save_checkbox = QCheckBox("Auto-save conversations")
        self.auto_save_checkbox.setChecked(True)
        self.auto_save_checkbox.setStyleSheet("color:#c8d0e0;")
        form.addRow("", self.auto_save_checkbox)

        self.log_to_file_checkbox = QCheckBox("Write session log to file")
        self.log_to_file_checkbox.setChecked(False)
        self.log_to_file_checkbox.setStyleSheet("color:#c8d0e0;")
        form.addRow("", self.log_to_file_checkbox)

        form_web = self._section_card(vbox, "WEB SEARCH")

        self.web_safe_search_checkbox = QCheckBox("Filter adult content from web results")
        self.web_safe_search_checkbox.setChecked(True)
        self.web_safe_search_checkbox.setStyleSheet("color:#c8d0e0;")
        self.web_safe_search_checkbox.setToolTip(
            "Asks every search provider for strict filtering (DuckDuckGo kp=1,\n"
            "SearXNG safesearch=2, Bing adlt=strict) and screens the results\n"
            "again by host before ELI sees them, in case a provider ignores it.\n"
            "Screening is by site, not by keyword, so medical, anatomical and\n"
            "biological results are never suppressed.\n"
            "Applies to searches ELI reads out AND to search pages it opens\n"
            "in your browser. Setting: web_safe_search"
        )
        self.web_safe_search_checkbox.stateChanged.connect(
            lambda _: self._apply_web_safe_search()
        )
        form_web.addRow("", self.web_safe_search_checkbox)

        form2 = self._section_card(vbox, "APPEARANCE")
        theme_btn = QPushButton("🌗  Toggle Dark / Light Theme")
        theme_btn.clicked.connect(self.toggle_theme)
        form2.addRow("", theme_btn)

        vbox.addStretch()
        return page

    def _apply_web_safe_search(self):
        """Persist the toggle the moment it changes.

        Search runs from the plugin choke point, which reads this setting per
        call, so writing it here takes effect on the next search without a
        restart or a Save.
        """
        try:
            from eli.core import config as _cfg
            _cfg.set("web_safe_search", bool(self.web_safe_search_checkbox.isChecked()))
        except Exception:
            log.debug("[GUI] web_safe_search write failed", exc_info=True)

    def _build_settings_agents_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Agents",
            "Create a new custom ELI agent through a guided 3-question chat wizard."
        )

        card = QWidget()
        card.setStyleSheet(
            "QWidget{background:#1e2230;border-radius:8px;}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        wizard_label = QLabel(
            "The <b>Agent Wizard</b> switches to your Chat tab and asks three questions:\n"
            "  1. Name &amp; purpose of the new agent\n"
            "  2. Trigger keywords and data sources\n"
            "  3. Persona and output style\n\n"
            "Specs are saved under your ELI data directory, registered live on the agent bus, "
            "and reloaded on startup. Enable/disable in <b>Settings → Agents</b>."
        )
        wizard_label.setWordWrap(True)
        wizard_label.setStyleSheet(
            "color:#8b9ab0;font-size:11px;background:transparent;"
        )
        card_layout.addWidget(wizard_label)

        launch_btn = QPushButton("🤖  Launch Agent Creator Wizard")
        launch_btn.setFixedHeight(34)
        launch_btn.setStyleSheet(
            "QPushButton{background:#5e81ac;color:#fff;font-weight:700;"
            "border:none;border-radius:6px;font-size:11px;}"
            "QPushButton:hover{background:#81a1c1;}"
        )
        launch_btn.clicked.connect(self.open_agent_wizard)
        card_layout.addWidget(launch_btn)

        vbox.addWidget(card)
        vbox.addStretch()
        return page

    def _build_settings_gaze_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Gaze Engine",
            "Webcam-based gaze tracking — iris position → screen coordinates via ridge regression.",
        )

        # ── Enable toggle ─────────────────────────────────────────────────────
        form = self._section_card(vbox, "ENGINE")

        self.gaze_enabled_checkbox = QCheckBox("Enable gaze engine on startup")
        self.gaze_enabled_checkbox.setChecked(False)
        self.gaze_enabled_checkbox.setStyleSheet("color:#c8d0e0;")
        self.gaze_enabled_checkbox.toggled.connect(self._on_gaze_toggle)
        form.addRow("", self.gaze_enabled_checkbox)

        # Camera index
        gaze_cam_row = QWidget()
        gaze_cam_lay = QHBoxLayout(gaze_cam_row)
        gaze_cam_lay.setContentsMargins(0, 0, 0, 0)
        gaze_cam_lay.setSpacing(6)
        self.gaze_camera_input = QLineEdit("auto")
        self.gaze_camera_input.setFixedWidth(60)
        self.gaze_camera_input.setStyleSheet(
            "QLineEdit{background:#252930;color:#c8d0e0;border:1px solid #3a3f4b;"
            "border-radius:4px;padding:2px 6px;font-size:11px;}"
        )
        self.gaze_camera_input.setToolTip(
            "Camera index (0, 1, 2 …) or 'auto' to scan all V4L2 devices"
        )
        gaze_cam_lay.addWidget(self.gaze_camera_input)
        gaze_cam_lay.addStretch()
        form.addRow(self._field_label("Camera"), gaze_cam_row)

        # ── Status row ───────────────────────────────────────────────────────
        form2 = self._section_card(vbox, "STATUS")

        self._gaze_status_label = QLabel("● stopped")
        self._gaze_status_label.setStyleSheet("color:#6b7a90;font-size:11px;")
        form2.addRow("", self._gaze_status_label)

        self._gaze_cal_label = QLabel("Calibration: unknown")
        self._gaze_cal_label.setStyleSheet("color:#6b7a90;font-size:10px;")
        form2.addRow("", self._gaze_cal_label)

        refresh_btn = QPushButton("↻  Refresh status")
        refresh_btn.setFixedHeight(26)
        refresh_btn.setStyleSheet(
            "QPushButton{background:#2e3440;color:#8b9ab0;font-size:10px;"
            "border:1px solid #3a3f4b;border-radius:4px;}"
            "QPushButton:hover{background:#3b4252;color:#d8dee9;}"
        )
        refresh_btn.clicked.connect(self._refresh_gaze_status)
        form2.addRow("", refresh_btn)

        # ── Control buttons ───────────────────────────────────────────────────
        form3 = self._section_card(vbox, "CONTROLS")

        btn_row = QWidget()
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(8)

        start_btn = QPushButton("▶  Start")
        start_btn.setFixedHeight(30)
        start_btn.setStyleSheet(
            "QPushButton{background:#4c7a4c;color:#fff;font-weight:600;"
            "border:none;border-radius:5px;padding:0 14px;font-size:11px;}"
            "QPushButton:hover{background:#5a9a5a;}"
        )
        start_btn.clicked.connect(self._gaze_start_clicked)
        btn_lay.addWidget(start_btn)

        stop_btn = QPushButton("⏹  Stop")
        stop_btn.setFixedHeight(30)
        stop_btn.setStyleSheet(
            "QPushButton{background:#7a3c3c;color:#fff;font-weight:600;"
            "border:none;border-radius:5px;padding:0 14px;font-size:11px;}"
            "QPushButton:hover{background:#9a4c4c;}"
        )
        stop_btn.clicked.connect(self._gaze_stop_clicked)
        btn_lay.addWidget(stop_btn)

        btn_lay.addStretch()
        form3.addRow("", btn_row)

        # ── Calibration ───────────────────────────────────────────────────────
        form4 = self._section_card(vbox, "CALIBRATION")

        cal_info = QLabel(
            "Calibration maps your iris position to screen coordinates via ridge\n"
            "regression on MediaPipe landmarks.  Run the script below with your\n"
            "webcam active and follow the on-screen dot targets (~2 min)."
        )
        cal_info.setWordWrap(True)
        cal_info.setStyleSheet("color:#6b7a90;font-size:10px;background:transparent;")
        form4.addRow("", cal_info)

        cal_btn = QPushButton("📋  Show calibration instructions")
        cal_btn.setFixedHeight(28)
        cal_btn.setStyleSheet(
            "QPushButton{background:#3b4252;color:#d8dee9;font-weight:500;"
            "border:none;border-radius:5px;padding:0 14px;font-size:11px;}"
            "QPushButton:hover{background:#4c566a;}"
        )
        cal_btn.clicked.connect(self._gaze_calibrate_info)
        form4.addRow("", cal_btn)

        vbox.addStretch()

        # Populate status on page build
        QTimer.singleShot(200, self._refresh_gaze_status)
        return page

    def _build_settings_advanced_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Advanced",
            "Deep control over agents, installed models, plugins, and self-upgrade tools."
        )

        _ADVANCED_CARDS = [
            (
                "🤖  Agents",
                "View, edit, enable or disable every agent in the bus.\nAdjust timeout, persona, and description per agent.",
                lambda: self.open_advanced_settings(0),
                "Manage Agents",
                "#5e81ac",
            ),
            (
                "🧠  Cognition",
                "Tune knowledge-gathering & context limits: how many memory facts, KG\nfacts, rerank depth, and the synthesis prompt cap ELI uses per answer.",
                lambda: self.open_advanced_settings(2),
                "Tune Cognition",
                "#b48ead",
            ),
            (
                "🔌  Plugins",
                "Install plugins from the registry, enable, disable, or uninstall them.",
                lambda: self.open_advanced_settings(3),
                "Manage Plugins",
                "#88c0d0",
            ),
            (
                "🔄  Self-Upgrade",
                "Run improvement cycles, rebuild the FAISS index, update the capability manifest, and refresh ELI's persona.",
                lambda: self.open_advanced_settings(4),
                "Open Upgrade Tools",
                "#a3be8c",
            ),
            (
                "🧹  Clear Memory / Factory Reset",
                "Wipe all learned memory + identity (databases, vector index, profile,\nconversations). Table schema is kept and everything is backed up first.\nELI restarts as a blank slate and asks your name again.",
                self.clear_memory_dialog,
                "Clear Memory",
                "#bf616a",
            ),
        ]

        for icon_title, desc, callback, btn_label, color in _ADVANCED_CARDS:
            card = QWidget()
            card.setStyleSheet(
                f"QWidget{{background:#1e2230;border-radius:8px;border-left:3px solid {color};}}"
                "QLabel{background:transparent;}"
            )
            cl = QHBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(12)

            text_col = QVBoxLayout()
            title_lbl = QLabel(f"<b>{icon_title}</b>")
            title_lbl.setStyleSheet("color:#e8eaf0;font-size:11px;")
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color:#6b7a90;font-size:10px;")
            text_col.addWidget(title_lbl)
            text_col.addWidget(desc_lbl)
            cl.addLayout(text_col, stretch=1)

            action_btn = QPushButton(btn_label)
            action_btn.setFixedHeight(28)
            action_btn.setFixedWidth(130)
            action_btn.setStyleSheet(
                f"QPushButton{{background:{color};color:#1a1d23;font-weight:700;"
                f"border:none;border-radius:5px;font-size:10px;}}"
                f"QPushButton:hover{{background:#fff;color:#1a1d23;}}"
            )
            action_btn.clicked.connect(callback)
            cl.addWidget(action_btn)

            vbox.addWidget(card)

        vbox.addStretch()
        return page

    def refresh_model_sources(self):
        self.detected_system_info = detect_system_capabilities()
        models = discover_gguf_models()
        if hasattr(self, 'bundled_model_combo'):
            self.bundled_model_combo.blockSignals(True)
            self.bundled_model_combo.clear()
            for m in models:
                if m.get('source') == 'bundled':
                    label = f"{m['name']}  ({m['size_gb']:.2f} GB)"
                    self.bundled_model_combo.addItem(label, m['path'])
            self.bundled_model_combo.blockSignals(False)
        self.refresh_ollama_models(quiet=True)
        if hasattr(self, 'bundled_model_combo') and self.bundled_model_combo.count() == 0:
            print("⚠️ No bundled GGUF models found – adding default path.")
            default_path = DEFAULT_MODEL_PATH
            label = f"{Path(default_path).name} (default)"
            self.bundled_model_combo.addItem(label, default_path)
        ram = self.detected_system_info.get('total_ram_gb') or 0.0
        cpu = self.detected_system_info.get('cpu_count') or 1
        self.system_recommendation_label.setText(f"Detected: {format_gb(float(ram))} RAM, {cpu} CPU threads visible.")
        pending = getattr(self, '_pending_bundled_model_path', '')
        if pending:
            idx = self.bundled_model_combo.findData(pending)
            if idx >= 0:
                self.bundled_model_combo.setCurrentIndex(idx)
        self.on_provider_changed()

    def refresh_ollama_models(self, quiet: bool = False):
        if not hasattr(self, 'ollama_model_combo'):
            return
        host = self.ollama_host_input.text().strip() if hasattr(self, 'ollama_host_input') else 'http://localhost:11434'
        current = self.ollama_model_combo.currentText().strip()
        self.ollama_model_combo.clear()
        try:
            names = self.ollama_manager.list_models(host)
            self.ollama_model_combo.addItems(names)
            if current:
                self.ollama_model_combo.setEditText(current)
            if not quiet:
                self.status_signal.emit(f"Found {len(names)} Ollama models")
        except Exception as e:
            if current:
                self.ollama_model_combo.setEditText(current)
            if not quiet:
                self.status_signal.emit(f"Ollama scan failed: {e}")

    def resolve_selected_model_path(self) -> str:
        provider = self.current_provider()
        if provider == 'bundled_gguf':
            path = self.bundled_model_combo.currentData()
            if path:
                return str(path)
            else:
                print("⚠️ Bundled combo empty – using default model path.")
                return DEFAULT_MODEL_PATH
        return self.model_path_input.text().strip()

    def detect_optimal_settings(self):
        selected_path = self.resolve_selected_model_path()
        if selected_path:
            tuned = self._apply_hardware_recommendation_for_model(selected_path)
            if tuned.get("ok"):
                return
        sysinfo = detect_system_capabilities()
        optimal = recommend_optimal_settings(sysinfo)
        self.n_ctx_input.setValue(optimal['n_ctx'])
        self.n_gpu_layers_input.setValue(optimal['n_gpu_layers'])
        self.n_threads_input.setValue(optimal['n_threads'])
        self.batch_size_input.setValue(optimal.get('batch_size', 128))
        self.temperature_input.setValue(optimal['temperature'])
        self.max_tokens_input.setValue(optimal['max_tokens'])
        self.status_signal.emit(
            f"Auto-detected: ctx={optimal['n_ctx']}  gpu={optimal['n_gpu_layers']}  "
            f"threads={optimal['n_threads']}  batch={optimal.get('batch_size', 128)}"
        )
        try:
            self.save_settings(silent=True)
        except Exception as _se:
            log.debug(f"[SETTINGS] Auto-save after detect_optimal failed: {_se}")

    def _apply_hardware_recommendation_for_model(self, model_path: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {"ok": False, "model_path": model_path}
        dock = self._ensure_hardware_tuning_dock()
        if dock is not None:
            dock.show()
            dock.raise_()
            dock.set_status("Running hardware tuning…")
            dock.set_summary(f"Model: {Path(model_path).name}")
            dock.log_view.clear()
            self._toggle_hardware_tuning_dock(True)
        self._hardware_tuning_log(f"Starting tuning for {model_path}")
        try:
            from eli.core.hardware_profile import detect_hardware as _hp_detect
            from eli.core.hardware_profile import recommend as _hp_recommend
            _gpu_support = None
            try:
                from llama_cpp import llama_cpp as _llama_native
                _supports_fn = getattr(_llama_native, "llama_supports_gpu_offload", None)
                _gpu_support = bool(_supports_fn()) if callable(_supports_fn) else None
                self._hardware_tuning_log(f"llama.cpp GPU offload support: {_gpu_support}")
                # A bare "False" here used to be a dead end: it gave no way to tell
                # whether a GPU pack was live, stale, or had been torn out for not
                # reaching a backend. Report which runtime answered the probe.
                try:
                    import llama_cpp as _llama_pkg

                    _rt_path = str(getattr(_llama_pkg, "__file__", "") or "").replace("\\", "/")
                    _rt_src = "GPU pack" if "runtime/gpu" in _rt_path else "bundled runtime"
                    _rt_ver = getattr(_llama_pkg, "__version__", "unknown")
                    self._hardware_tuning_log(
                        f"llama.cpp runtime: {_rt_ver} ({_rt_src})"
                    )
                    if _gpu_support is False:
                        self._hardware_tuning_log(
                            "No GPU backend is active - this runtime will use CPU only."
                        )
                except Exception:
                    # Provenance is a diagnostic extra: never let it break tuning.
                    # Observable rather than silent -- a swallowed failure here
                    # would hide why the runtime line is missing.
                    log.debug("runtime provenance probe failed", exc_info=True)
            except Exception as _gpu_probe_err:
                self._hardware_tuning_log(f"llama.cpp GPU offload probe failed: {_gpu_probe_err}")

            model_file = resolve_model_path(model_path)
            if not model_file.exists():
                raise FileNotFoundError(f"Model not found: {model_file}")
            size_bytes = int(model_file.stat().st_size)
            hw = _hp_detect()
            # The user's EXPLICITLY chosen ctx must anchor the recommendation, not be
            # overwritten by the DEFAULT_N_CTX target. Source of truth (highest first):
            # the startup dialog's "Direct context window" field (ELI_FORCE_CTX), else
            # the live ctx spinbox (persisted from settings.json n_ctx). recommend()
            # honours it and only trims it on a real VRAM constraint (logged).
            import os as _os_ctx
            _forced_ctx = (_os_ctx.environ.get("ELI_FORCE_CTX") or "").strip()
            try:
                _spin_ctx = int(self.n_ctx_input.value())
            except Exception:
                _spin_ctx = 0
            if _forced_ctx.isdigit() and int(_forced_ctx) >= 2048:
                _user_pinned_ctx = int(_forced_ctx)
            elif _spin_ctx >= 2048:
                _user_pinned_ctx = _spin_ctx
            else:
                _user_pinned_ctx = None
            # The user's own GPU-layer choice, protected exactly like ctx. A tuning
            # run made while the GPU is busy computes a low layer count for THOSE
            # conditions; persisting it as the preference makes a transient state
            # permanent (observed in v2: a pinned 99 became 13 while a video played,
            # and every later boot loaded at 13 with the GPU idle).
            try:
                _user_pinned_layers = int(self.n_gpu_layers_input.value())
            except Exception:
                _user_pinned_layers = 0
            try:
                _user_pinned_batch = int(self.batch_size_input.value())
            except Exception:
                _user_pinned_batch = 0
            rec = _hp_recommend(hw, [{
                "name": model_file.name,
                "path": str(model_file),
                "size_bytes": size_bytes,
                "size_gb": size_bytes / 1e9,
            }], user_ctx=_user_pinned_ctx)

            # Runtime CUDA/backend may be unavailable even if VRAM probe reports
            # a GPU. In that case force CPU-safe values so the GUI does not keep
            # applying aggressive GPU-centric params.
            if _gpu_support is False:
                rec.n_gpu_layers = 0
                rec.batch_size = min(int(getattr(rec, "batch_size", 128) or 128), 128)
                self._hardware_tuning_log(
                    "GPU offload unavailable at runtime -> forcing CPU-safe tuning "
                    "(gpu_layers=0, batch<=128)."
                )

            # KV-cache quantization: hardware fact — safe to update in the combo
            # (affects how VRAM is used, not a user performance preference).
            _ck = str(getattr(rec, "cache_type_k", "") or "")
            _cv = str(getattr(rec, "cache_type_v", "") or "")
            _ki = self.cache_type_k_combo.findText(_ck)
            if _ki < 0 and _ck:
                self.cache_type_k_combo.addItem(_ck)
                _ki = self.cache_type_k_combo.findText(_ck)
            if _ki >= 0:
                self.cache_type_k_combo.setCurrentIndex(_ki)
            _vi = self.cache_type_v_combo.findText(_cv)
            if _vi < 0 and _cv:
                self.cache_type_v_combo.addItem(_cv)
                _vi = self.cache_type_v_combo.findText(_cv)
            if _vi >= 0:
                self.cache_type_v_combo.setCurrentIndex(_vi)

            for line in list(getattr(rec, "reasoning", []) or []):
                self._hardware_tuning_log(str(line))

            # HW Profile is AUTHORITATIVE — these values are freshly computed
            # for THIS model + THIS hardware's current free VRAM on every load.
            # They become the primary load parameters (attempt 1), not advisory.
            # Stale per-model values must never drive the load: switching from a
            # 7B to a 24B model needs a fully recalculated profile, not the 7B's
            # gpu_layers/ctx/batch left over in settings.json.
            summary = (
                f"HW Profile: ctx={int(rec.n_ctx)} gpu_layers={int(rec.n_gpu_layers)} "
                f"threads={int(rec.n_threads)} batch={int(rec.batch_size)} "
                f"kv={_ck or 'fp16'}"
            )
            self.system_recommendation_label.setText(summary)
            if dock is not None:
                dock.set_status("Tuning complete")
                dock.set_summary(summary)
            self.status_signal.emit(summary)

            # Apply the calculated profile to the live spinboxes so load_model()
            # reads the model-specific values as its primary (attempt 1) load.
            # setValue() clamps to each spinbox's range automatically.
            # CANONICAL ctx = the user's chosen value (preserved), NOT the possibly
            # VRAM-reduced rec.n_ctx — writing the reduced effective back would strand
            # the user at a lower ctx next boot. The load path's smart-fit reduces to
            # fit (and logs) at load time; the effective value lives in hw_profile_*.
            _canonical_ctx = int(_user_pinned_ctx) if _user_pinned_ctx else int(rec.n_ctx)
            # A pinned layer count belongs to the MODEL it was chosen for. It is an
            # absolute number of layers, so it means something entirely different on
            # a different model: 7 was right for a 15.66GB 27B on 8GB of VRAM, and
            # stranded 25 of 32 layers on the CPU when a 4GB model that fits VRAM
            # whole was loaded under the same 7 -- the tuner correctly measured 32,
            # said so, and stood down because "yours" wins. Nothing recorded which
            # model the pin came from, so it followed the user silently across swaps.
            # Same rule the headless/server loader applies, from the same helper,
            # so the desktop app and the API can never disagree about which
            # layer count is live. "All layers" (99 / -1) is exempt there: it is
            # a policy, correct on every model, and the loader reduces it to fit.
            # Same normalisation the matcher uses, or a pin stamped on Windows
            # would never match the key computed when it is read back.
            try:
                from eli.core.runtime_settings import model_identity_key as _mk
                _this_model = _mk(model_path)
            except Exception:
                _this_model = _os_ctx.path.basename(str(model_path)) if model_path else ""
            try:
                from eli.core.runtime_settings import (
                    load_settings as _rs_load_pin,
                    pinned_gpu_layers_for_model as _pin_for_model,
                )
                _live = dict(_rs_load_pin() or {})
                _pin_model = str(_live.get("n_gpu_layers_model") or "")
                _pin_ctx = int(_live.get("n_gpu_layers_ctx") or 0)
                _live["n_gpu_layers"] = int(_user_pinned_layers or 0)
                _applies = _pin_for_model(model_path, _live, current_ctx=_canonical_ctx)
            except Exception:
                _pin_model, _pin_ctx, _applies = "", 0, (_user_pinned_layers or None)
            if _user_pinned_layers and _applies is None:
                if _pin_model and _pin_model != _this_model:
                    _why = f"was pinned for {_pin_model}, not {_this_model}"
                elif _pin_ctx and _pin_ctx != _canonical_ctx:
                    _why = f"was measured at ctx={_pin_ctx}, not the current ctx={_canonical_ctx}"
                else:
                    _why = "no longer applies"
                self._hardware_tuning_log(
                    f"gpu_layers={_user_pinned_layers} {_why} - using "
                    f"the tuner's {int(rec.n_gpu_layers)}, measured for this model at "
                    f"this ctx. Reload with the same ctx to keep a pin across loads."
                )
                _user_pinned_layers = 0
            _canonical_layers = int(_user_pinned_layers) if _user_pinned_layers else int(rec.n_gpu_layers)
            _min_batch = int(_os_ctx.environ.get("ELI_MIN_BATCH", "128") or "128")
            _apply_batch = max(int(rec.batch_size), _min_batch)
            # Batch follows the same rule as ctx and layers: the user's choice is a
            # preference, the tuner's is a measurement. The ELI_MIN_BATCH floor is a
            # safety limit and still applies to whichever value wins.
            _canonical_batch = (max(int(_user_pinned_batch), _min_batch)
                                if _user_pinned_batch else _apply_batch)
            try:
                self.n_ctx_input.setValue(_canonical_ctx)
                self.n_gpu_layers_input.setValue(_canonical_layers)
                self.batch_size_input.setValue(_canonical_batch)
                if int(rec.n_threads) > 0:
                    self.n_threads_input.setValue(int(rec.n_threads))
            except Exception as _spin_err:
                self._hardware_tuning_log(f"Warning: spinbox apply failed: {_spin_err}")

            # Persist the calculated values as the canonical runtime settings AND
            # the hw_profile_* mirror, so both the load path and any settings
            # readers see the recalculated, model-specific profile.
            try:
                from eli.core.runtime_settings import load_settings as _rs_load, save_settings as _rs_save
                _s = dict(_rs_load() or {})
                _s["n_ctx"] = _canonical_ctx        # user's chosen ctx, preserved
                _s["n_gpu_layers"] = _canonical_layers   # user's choice, preserved
                # Stamp the model AND ctx the count now belongs to, so the next
                # swap -- or the next ctx change on the SAME model -- can tell a
                # deliberate pin from one inherited from a different model or a
                # different (usually much smaller) ctx it was never measured at.
                _s["n_gpu_layers_model"] = _this_model
                _s["n_gpu_layers_ctx"] = _canonical_ctx
                _s["batch_size"] = _canonical_batch   # user's choice, preserved
                if int(rec.n_threads) > 0:
                    _s["n_threads"] = int(rec.n_threads)
                _s["hw_profile_n_ctx"] = int(rec.n_ctx)   # effective (VRAM-reduced) value
                _s["hw_profile_n_gpu_layers"] = int(rec.n_gpu_layers)
                _s["hw_profile_batch_size"] = int(rec.batch_size)
                _s["cache_type_k"] = _ck
                _s["cache_type_v"] = _cv
                _rs_save(_s)
                # Say what was applied and what was NOT. Announcing the whole
                # profile as canonical was wrong: ctx and gpu_layers are the user's.
                _applied_desc = ((f"threads={int(rec.n_threads)}" if int(rec.n_threads) > 0
                                  else "nothing"))
                _not_applied = []
                if int(rec.n_ctx) != int(_canonical_ctx):
                    _not_applied.append(f"n_ctx (yours: {_canonical_ctx}, tuner: {int(rec.n_ctx)})")
                if int(rec.n_gpu_layers) != int(_canonical_layers):
                    _not_applied.append(
                        f"n_gpu_layers (yours: {_canonical_layers}, tuner: {int(rec.n_gpu_layers)})")
                if int(_apply_batch) != int(_canonical_batch):
                    _not_applied.append(
                        f"batch_size (yours: {_canonical_batch}, tuner: {int(_apply_batch)})")
                if _not_applied:
                    self._hardware_tuning_log(
                        f"Applied as canonical: {_applied_desc}. NOT applied: "
                        + "; ".join(_not_applied)
                        + ". Your values are kept; the tuner's are stored as hw_profile_* "
                          "fallbacks, used only if your settings fail to load."
                    )
                else:
                    self._hardware_tuning_log(
                        f"Applied as canonical: n_ctx={_canonical_ctx} "
                        f"gpu_layers={_canonical_layers} batch={_canonical_batch} "
                        f"{_applied_desc} (calculated for this model)."
                    )
            except Exception as _save_err:
                self._hardware_tuning_log(f"Warning: hw_profile save failed: {_save_err}")

            result.update({"ok": True, "recommendation": rec.to_dict() if hasattr(rec, "to_dict") else {}})
            return result
        except Exception as e:
            msg = f"Hardware tuning failed: {e}"
            self._hardware_tuning_log(msg)
            if dock is not None:
                dock.set_status("Tuning failed")
                dock.set_summary(msg)
            log.debug(f"[SETTINGS] {msg}")
            result["error"] = str(e)
            return result

    def _run_startup_model_picker(self) -> bool:
        models = discover_gguf_models()
        ollama_names: List[str] = []
        try:
            ollama_names = self.ollama_manager.list_models(
                self.ollama_host_input.text().strip() if hasattr(self, "ollama_host_input") else "http://localhost:11434"
            )
        except Exception:
            ollama_names = []

        dlg = StartupModelSelectionDialog(
            parent=self,
            models=models,
            current_provider=self.current_provider(),
            current_model_path=self.resolve_selected_model_path(),
            ollama_host=self.ollama_host_input.text().strip() if hasattr(self, "ollama_host_input") else "http://localhost:11434",
            ollama_model=self.ollama_model_combo.currentText().strip() if hasattr(self, "ollama_model_combo") else "",
            ollama_models=ollama_names,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False

        provider = dlg.selected_provider()
        idx = self.provider_combo.findData(provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)

        if provider == "ollama":
            self.ollama_host_input.setText(dlg.selected_ollama_host())
            self.ollama_model_combo.setEditText(dlg.selected_ollama_model())
        else:
            selected_path = dlg.selected_model_path()
            if provider == "bundled_gguf":
                j = self.bundled_model_combo.findData(selected_path)
                if j >= 0:
                    self.bundled_model_combo.setCurrentIndex(j)
                else:
                    _custom_idx = self.provider_combo.findData("custom_gguf")
                    if _custom_idx >= 0:
                        self.provider_combo.setCurrentIndex(_custom_idx)
                    self.model_path_input.setText(selected_path)
            else:
                self.model_path_input.setText(selected_path)

            # The dialog's "Target batch" is what the operator just asked for. The
            # tuner treats the Settings-tab batch spinbox as a pinned user choice
            # that beats its own number, and that spinbox still held the previous
            # session's value -- so a dialog set to 256 loaded 512, with nothing
            # anywhere saying the request had been overridden. Carry the dialog's
            # value into the spinbox so there is one request, not two.
            try:
                _dlg_batch = int(dlg.target_batch_spin.value())
                if _dlg_batch > 0:
                    self.batch_size_input.setValue(_dlg_batch)
            except Exception:
                log.debug("could not carry the dialog's target batch forward", exc_info=True)

            if selected_path:
                tune_result = self._apply_hardware_recommendation_for_model(selected_path)
                if not bool(tune_result.get("ok")):
                    try:
                        QMessageBox.warning(
                            self,
                            "Hardware tuning failed",
                            "Could not derive/apply hardware-optimal settings for the selected model. "
                            "Model load was cancelled.",
                        )
                    except Exception:
                        pass
                    return False

        self._first_run_complete = True
        self.save_settings(silent=True)
        if dlg.should_load_now():
            self.load_model()
        return True

    def prompt_load_model(self):
        self._run_startup_model_picker()

    def load_model(self):
        provider = self.current_provider()
        if provider != "ollama":
            selected_path = self.resolve_selected_model_path()
            if selected_path:
                tune_result = self._apply_hardware_recommendation_for_model(selected_path)
                if not bool(tune_result.get("ok")):
                    self.status_signal.emit("🔴 Hardware tuning failed; model load cancelled")
                    return

        n_ctx = self.n_ctx_input.value()
        n_threads = self.n_threads_input.value()
        n_gpu_layers = self.n_gpu_layers_input.value()
        batch_size = self.batch_size_input.value()
        cache_type_k = self.cache_type_k_combo.currentText().strip()
        cache_type_v = self.cache_type_v_combo.currentText().strip()
        use_mmap = True
        use_mlock = False
        try:
            from eli.core.runtime_settings import load_settings as _rs_load
            _s = _rs_load() or {}
            use_mmap = bool(_s.get("use_mmap", True))
            use_mlock = bool(_s.get("use_mlock", False))
        except Exception:
            pass
        self.status_signal.emit("🔄 Loading model...")
        self.status_signal.emit("Send disabled")
        try:
            self.send_btn.setText("Loading...")
        except Exception:
            pass

        def load_worker():
            self._model_loading = True
            try:
                if provider == 'ollama':
                    host = self.ollama_host_input.text().strip()
                    model_name = self.ollama_model_combo.currentText().strip()
                    success = self.ollama_manager.load_model(host, model_name)
                    backend = self.ollama_manager
                    model_name_display = model_name
                else:
                    model_path = self.resolve_selected_model_path()
                    success = self.model_manager.load_model(
                        model_path=model_path,
                        n_ctx=n_ctx,
                        n_threads=n_threads,
                        n_gpu_layers=n_gpu_layers,
                        n_batch=batch_size,
                        cache_type_k=cache_type_k,
                        cache_type_v=cache_type_v,
                        use_mmap=use_mmap,
                        use_mlock=use_mlock,
                    )
                    backend = self.model_manager
                    model_name_display = Path(getattr(self.model_manager, 'model_path', model_path or 'model')).name

                if success:
                    self.active_backend = backend
                    self.memory_system.log_event('model_load', f"Loaded {model_name_display} via {provider}")
                    if provider != "ollama":
                        self.status_signal.emit(
                            f"🟢 Model ready: {model_name_display} "
                            f"(ctx={int(getattr(self.model_manager, 'n_ctx', 0) or 0)} "
                            f"gpu={int(getattr(self.model_manager, 'n_gpu_layers', 0) or 0)} "
                            f"batch={int(getattr(self.model_manager, 'n_batch', 0) or 0)})"
                        )
                    else:
                        self.status_signal.emit(f"🟢 Model ready: {model_name_display}")
                    # Tell the CognitiveEngine the model is available so its
                    # world-model / self-awareness reports the correct state
                    # (avoids "GGUF init deferred" appearing in chat responses).
                    _ce = getattr(self, "_cognitive_engine", None)
                    if _ce is not None:
                        try:
                            _ce._gguf_available = True
                            _ce._gguf_load_error = None
                            _loaded_path = str(
                                getattr(self.model_manager, "model_path", model_path) or model_path
                            )
                            _ce._model_path = _loaded_path
                            _ce._ctx = int(getattr(self.model_manager, "n_ctx", 0) or 0)
                            _ce._gpu_layers = int(getattr(self.model_manager, "n_gpu_layers", 0) or 0)
                        except Exception:
                            pass
                    try:
                        if provider != "ollama":
                            _requested_gpu = int(getattr(self.model_manager, "requested_n_gpu_layers", 0) or 0)
                            _effective_gpu = int(getattr(self.model_manager, "n_gpu_layers", 0) or 0)
                            _gpu_supported = getattr(self.model_manager, "gpu_offload_supported", None)
                            if _requested_gpu > 0 and (_gpu_supported is False or _effective_gpu <= 0):
                                self.status_signal.emit(
                                    "⚠️ GPU offload unavailable; running CPU-only. "
                                    "Check NVIDIA driver/CUDA runtime."
                                )
                    except Exception:
                        pass
                    self.status_signal.emit("Send enabled")
                else:
                    err = getattr(backend, 'load_error', None) or 'Unknown error'
                    self.status_signal.emit(f"🔴 Model load failed: {err}")
                    self.status_signal.emit("🔴 Model: Not loaded")
                    self.status_signal.emit("Send enabled")
                    print(f"Model Load Error: {err}")
            finally:
                self._model_loading = False
                self.status_signal.emit("Ready")

        threading.Thread(target=load_worker, daemon=True).start()

    def load_model_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select GGUF Model",
            str(Path.home()),
            "GGUF Files (*.gguf);;All Files (*)"
        )
        if file_path:
            idx = self.provider_combo.findData('custom_gguf')
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            self.model_path_input.setText(file_path)

    def unload_model(self):
        self.get_active_backend().unload()
        self.status_signal.emit("🔴 Model: Not loaded")
        self.status_signal.emit("Send enabled")

    def browse_model_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select GGUF Model",
            "local_models",
            "GGUF Files (*.gguf);;All Files (*)"
        )
        if file_path:
            idx = self.provider_combo.findData('custom_gguf') if hasattr(self, 'provider_combo') else -1
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            self.model_path_input.setText(file_path)

    def load_settings(self):
        """Load settings via runtime_settings (single canonical source)."""
        self._first_run_complete = False
        try:
            from eli.core.runtime_settings import load_settings as _rs_load
            s = _rs_load() or {}
        except Exception as e:
            print(f"⚠️ Failed to load runtime settings: {e}")
            s = {}

        # Numeric / generation params
        try:
            self.n_ctx_input.setValue(int(s.get("n_ctx", 16384)))
            self.n_threads_input.setValue(int(s.get("n_threads", 8)))
            self.n_gpu_layers_input.setValue(int(s.get("n_gpu_layers", 99)))
            self.max_tokens_input.setValue(int(s.get("max_tokens", 4096)))
            self.temperature_input.setValue(float(s.get("temperature", 0.7)))
            _batch = s.get("batch_size") or s.get("n_batch")
            if _batch:
                self.batch_size_input.setValue(int(_batch))
            # KV cache quantization combos
            try:
                _k = str(s.get("cache_type_k", "") or "")
                _v = str(s.get("cache_type_v", "") or "")
                _kidx = self.cache_type_k_combo.findText(_k)
                if _kidx >= 0:
                    self.cache_type_k_combo.setCurrentIndex(_kidx)
                _vidx = self.cache_type_v_combo.findText(_v)
                if _vidx >= 0:
                    self.cache_type_v_combo.setCurrentIndex(_vidx)
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ Failed to apply numeric settings to widgets: {e}")

        # Provider + paths
        try:
            provider = s.get("provider", "bundled_gguf")
            idx = self.provider_combo.findData(provider)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            model_path = s.get("model_path") or DEFAULT_MODEL_PATH
            self.model_path_input.setText(model_path or "")
            bundled_path = s.get("bundled_model_path", "")
            if bundled_path:
                self._pending_bundled_model_path = bundled_path
            self.ollama_host_input.setText(s.get("ollama_host", "http://localhost:11434"))
            self.ollama_model_combo.setEditText(s.get("ollama_model", ""))
        except Exception as e:
            print(f"⚠️ Failed to apply provider/path settings to widgets: {e}")

        # Mic device selection
        try:
            _mic = s.get("mic_device")
            if _mic:
                self._saved_mic_device = tuple(_mic) if isinstance(_mic, list) else _mic
                self._on_mic_device_changed(_save=False)
        except Exception:
            pass

        # STT sensitivity (energy threshold)
        try:
            _dynamic = bool(s.get("stt_dynamic_energy", False))
            _threshold = int(s.get("stt_energy_threshold", 1200))
            self.dynamic_energy_checkbox.blockSignals(True)
            self.dynamic_energy_checkbox.setChecked(_dynamic)
            self.dynamic_energy_checkbox.blockSignals(False)
            self.energy_threshold_input.blockSignals(True)
            self.energy_threshold_input.setValue(_threshold)
            self.energy_threshold_input.blockSignals(False)
            import os as _os_e
            _os_e.environ["ELI_STT_DYNAMIC_ENERGY"] = "1" if _dynamic else "0"
            _os_e.environ["ELI_STT_ENERGY_THRESHOLD"] = str(_threshold)
        except Exception:
            pass

        # STT behaviour flags
        try:
            _direct = bool(s.get("allow_direct_chat_without_wake", False))
            self.allow_direct_chat_checkbox.blockSignals(True)
            self.allow_direct_chat_checkbox.setChecked(_direct)
            self.allow_direct_chat_checkbox.blockSignals(False)
            import os as _os_s
            _os_s.environ["ELI_STT_ALLOW_DIRECT_CHAT"] = "1" if _direct else "0"
            if hasattr(self, "wake_word_btn"):
                self.wake_word_btn.blockSignals(True)
                self.wake_word_btn.setChecked(not _direct)
                self.wake_word_btn.setText("Wake: ON" if not _direct else "Wake: OFF")
                self.wake_word_btn.blockSignals(False)
        except Exception:
            pass

        # Gaze engine
        try:
            _gaze_on = bool(s.get("gaze_engine_enabled", False))
            _gaze_cam = str(s.get("gaze_camera", "auto") or "auto")
            if hasattr(self, "gaze_enabled_checkbox"):
                self.gaze_enabled_checkbox.blockSignals(True)
                self.gaze_enabled_checkbox.setChecked(_gaze_on)
                self.gaze_enabled_checkbox.blockSignals(False)
            if hasattr(self, "gaze_camera_input"):
                self.gaze_camera_input.blockSignals(True)
                self.gaze_camera_input.setText(_gaze_cam)
                self.gaze_camera_input.blockSignals(False)
            if _gaze_on:
                try:
                    from eli.perception.gaze_engine import start_gaze_engine
                    start_gaze_engine(camera=_gaze_cam if _gaze_cam == "auto" else int(_gaze_cam) if _gaze_cam.isdigit() else "auto")
                except Exception as _ge:
                    log.debug(f"[GUI] Gaze engine auto-start failed: {_ge}")
        except Exception:
            pass

        # GUI-local flags + theme
        try:
            self.auto_save_checkbox.setChecked(bool(s.get("auto_save", True)))
            self.log_to_file_checkbox.setChecked(bool(s.get("log_to_file", False)))
            if hasattr(self, "web_safe_search_checkbox"):
                self.web_safe_search_checkbox.setChecked(bool(s.get("web_safe_search", True)))
            self.auto_load_checkbox.setChecked(bool(s.get("auto_load", True)))
            self._show_startup_model_picker = bool(s.get("show_startup_model_picker", True))
            if hasattr(self, "startup_model_picker_checkbox"):
                self.startup_model_picker_checkbox.setChecked(self._show_startup_model_picker)
            self._first_run_complete = bool(s.get("first_run_complete", False))
            self.current_theme = s.get("theme", self.current_theme)
            self._user_text_color = s.get(
                "user_text_color", getattr(self, "_user_text_color", "#a3be8c"))
        except Exception as e:
            print(f"⚠️ Failed to apply GUI flags: {e}")

        # Identity + image studio defaults
        try:
            _gui_user_name = str(s.get("user_name", "") or "").strip()
            if not _gui_user_name:
                try:
                    from eli.kernel.state import get_user_name as _gun
                    _gui_user_name = _gun() or ""
                except Exception:
                    pass
            self.user_name_input.setText(_gui_user_name)
            if hasattr(self, "communication_style_combo"):
                _cs = str(s.get("communication_style", "") or "")
                try:
                    from eli.kernel.state import get_communication_style as _gcs
                    _cs = _gcs() or _cs
                except Exception:
                    log.debug("communication_style load from state failed", exc_info=True)
                self.communication_style_combo.setCurrentText(_cs)
            self.image_profile_notes_input.setPlainText(str(s.get("image_profile_notes", "") or ""))
            self.image_style_profile_combo.setCurrentText(str(s.get("image_style_profile", "auto") or "auto"))
            self.image_palette_profile_combo.setCurrentText(str(s.get("image_palette_profile", "auto") or "auto"))
            self.image_backend_default_combo.setCurrentText(str(s.get("image_backend", "auto") or "auto"))
            self.image_model_path_input.setText(str(s.get("image_model_path", "") or ""))
            self.image_device_default_combo.setCurrentText(str(s.get("image_device", "auto") or "auto"))
            self.image_quality_default_combo.setCurrentText(str(s.get("image_quality_preset", "ultra") or "ultra"))
            self.image_steps_default_input.setValue(int(s.get("image_steps", 36) or 36))
            self.image_guidance_default_input.setValue(float(s.get("image_guidance", 7.2) or 7.2))
            self.image_negative_default_input.setPlainText(str(s.get("image_negative_prompt", "") or ""))
            self.image_auto_personalize_checkbox.setChecked(bool(s.get("image_auto_personalize", True)))
            self.image_auto_open_checkbox.setChecked(bool(s.get("image_auto_open", True)))
            self.image_use_chat_context_checkbox.setChecked(bool(s.get("image_use_chat_context", True)))
            self.image_use_proactive_context_checkbox.setChecked(bool(s.get("image_use_proactive_context", True)))
            self.image_default_project_input.setText(str(s.get("image_default_project_path", "") or ""))
            self.image_default_count_input.setValue(int(s.get("image_default_count", 1) or 1))
            self.image_default_width_input.setValue(int(s.get("image_default_width", 1400) or 1400))
            self.image_default_height_input.setValue(int(s.get("image_default_height", 900) or 900))

            self.image_project_input.setText(str(s.get("image_default_project_path", "") or ""))
            self.image_count_input.setValue(int(s.get("image_default_count", 1) or 1))
            self.image_width_input.setValue(int(s.get("image_default_width", 1400) or 1400))
            self.image_height_input.setValue(int(s.get("image_default_height", 900) or 900))
            self.image_backend_combo.setCurrentText(str(s.get("image_backend", "auto") or "auto"))
            self.image_model_combo.setEditText(str(s.get("image_model_path", "") or ""))
            self.image_device_combo.setCurrentText(str(s.get("image_device", "auto") or "auto"))
            self.image_quality_preset_combo.setCurrentText(str(s.get("image_quality_preset", "ultra") or "ultra"))
            self.image_steps_input.setValue(int(s.get("image_steps", 36) or 36))
            self.image_guidance_input.setValue(float(s.get("image_guidance", 7.2) or 7.2))
            self.image_negative_input.setPlainText(str(s.get("image_negative_prompt", "") or ""))
            self.image_personalize_checkbox.setChecked(bool(s.get("image_auto_personalize", True)))
            self.image_chat_context_checkbox.setChecked(bool(s.get("image_use_chat_context", True)))
            self.image_proactive_context_checkbox.setChecked(bool(s.get("image_use_proactive_context", True)))
        except Exception as e:
            print(f"⚠️ Failed to apply identity/image settings: {e}")

        self.apply_theme()

    def save_settings(self, silent: bool = False):
        """Save settings via runtime_settings — single canonical merge-write."""
        provider = self.current_provider()
        model_path = self.resolve_selected_model_path() if provider != "ollama" else self.model_path_input.text()
        bundled_path = str(self.bundled_model_combo.currentData() or "")
        existing = {}
        try:
            from eli.core.runtime_settings import load_settings as _rs_load
            existing = _rs_load() or {}
        except Exception:
            existing = {}

        updates = {
            "provider": provider,
            "model_path": model_path,
            "custom_model_path": self.model_path_input.text(),
            "bundled_model_path": bundled_path,
            # Store the canonical form, so "localhost:11434" or a bare IP typed
            # here is persisted as a URL every consumer can actually use.
            "ollama_host": self.ollama_manager._normalize_host(
                self.ollama_host_input.text().strip()),
            "ollama_model": self.ollama_model_combo.currentText().strip(),
            "n_ctx": int(self.n_ctx_input.value()),
            "n_threads": int(self.n_threads_input.value()),
            "n_gpu_layers": int(self.n_gpu_layers_input.value()),
            "batch_size": int(self.batch_size_input.value()),
            "max_tokens": int(self.max_tokens_input.value()),
            "temperature": float(self.temperature_input.value()),
            "cache_type_k": self.cache_type_k_combo.currentText().strip(),
            "cache_type_v": self.cache_type_v_combo.currentText().strip(),
            "auto_save": bool(self.auto_save_checkbox.isChecked()),
            "log_to_file": bool(self.log_to_file_checkbox.isChecked()),
            "web_safe_search": bool(self.web_safe_search_checkbox.isChecked()) if hasattr(self, "web_safe_search_checkbox") else True,
            "auto_load": bool(self.auto_load_checkbox.isChecked()),
            "show_startup_model_picker": bool(getattr(self, "startup_model_picker_checkbox", None).isChecked()) if hasattr(self, "startup_model_picker_checkbox") else bool(getattr(self, "_show_startup_model_picker", True)),
            "first_run_complete": bool(getattr(self, "_first_run_complete", False)),
            "theme": self.current_theme,
            "user_text_color": getattr(self, "_user_text_color", "#a3be8c"),
            "user_name": self.user_name_input.text().strip(),
            "communication_style": self.communication_style_combo.currentText().strip() if hasattr(self, "communication_style_combo") else "",
            "image_style_profile": self.image_style_profile_combo.currentText().strip(),
            "image_palette_profile": self.image_palette_profile_combo.currentText().strip(),
            "image_profile_notes": self.image_profile_notes_input.toPlainText().strip(),
            "image_backend": self.image_backend_default_combo.currentText().strip(),
            "image_model_path": self.image_model_path_input.text().strip() or self._selected_image_model_path(),
            "image_device": self.image_device_default_combo.currentText().strip(),
            "image_quality_preset": self.image_quality_default_combo.currentText().strip(),
            "image_steps": int(self.image_steps_default_input.value()),
            "image_guidance": float(self.image_guidance_default_input.value()),
            "image_negative_prompt": self.image_negative_default_input.toPlainText().strip(),
            "image_default_project_path": self.image_default_project_input.text().strip(),
            "image_default_count": int(self.image_default_count_input.value()),
            "image_default_width": int(self.image_default_width_input.value()),
            "image_default_height": int(self.image_default_height_input.value()),
            "image_auto_personalize": bool(self.image_auto_personalize_checkbox.isChecked()),
            "image_auto_open": bool(self.image_auto_open_checkbox.isChecked()),
            "image_use_chat_context": bool(self.image_use_chat_context_checkbox.isChecked()),
            "image_use_proactive_context": bool(self.image_use_proactive_context_checkbox.isChecked()),
            "allow_direct_chat_without_wake": bool(self.allow_direct_chat_checkbox.isChecked()),
            "mic_device": list(self.mic_device_combo.currentData()) if self.mic_device_combo.currentData() else None,
            "stt_dynamic_energy": bool(self.dynamic_energy_checkbox.isChecked()),
            "stt_energy_threshold": int(self.energy_threshold_input.value()),
            "gaze_engine_enabled": bool(getattr(self, "gaze_enabled_checkbox", None) and self.gaze_enabled_checkbox.isChecked()),
            "gaze_camera": str(self.gaze_camera_input.text().strip() if hasattr(self, "gaze_camera_input") else "auto"),
        }

        try:
            from eli.core.runtime_settings import save_settings as _rs_save
            _rs_save(updates)
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")
            else:
                log.debug(f"[SETTINGS] Save failed: {e}")
            return
        # Propagate the tone preference to user_profile.json + the persona overlay — the
        # persona brief reads communication_style from the profile, not settings.json.
        try:
            from eli.kernel.state import set_communication_style as _scs
            _scs(str(updates.get("communication_style", "")))
        except Exception:
            log.debug("[SETTINGS] communication_style propagate failed", exc_info=True)

        try:
            from eli.kernel.state import set_user_name as _set_user_name, update_user_profile as _update_profile
            _set_user_name(updates.get("user_name", ""))
            _update_profile(
                image_style=updates.get("image_style_profile", "auto"),
                image_palette=updates.get("image_palette_profile", "auto"),
                image_visual_notes=updates.get("image_profile_notes", ""),
            )
        except Exception as e:
            log.debug(f"[SETTINGS] User profile sync skipped: {e}")

        # max_tokens, temperature, top_p, top_k, repeat_penalty are
        # per-inference params — handled at call time, no reload needed.
        # The keys below force a Llama instance rebuild because they
        # change how the model is constructed.
        reload_keys = {
            "provider", "model_path", "custom_model_path", "bundled_model_path",
            "ollama_host", "ollama_model", "n_ctx", "n_threads",
            "n_gpu_layers", "batch_size",
            "cache_type_k", "cache_type_v",
            "use_mmap", "use_mlock",
        }
        requires_reload = any(existing.get(k) != updates.get(k) for k in reload_keys)

        # On explicit user save with reload-required keys changed, kick
        # off a proactive reload in a background thread so the new
        # config is live before the user sends the next message — and
        # so the chat/generate pipeline is never blocked on the lock
        # at first-message time. silent saves (color picker, first-run
        # wiring) skip this — they shouldn't disturb the loaded model.
        try:
            if requires_reload and not silent and gguf_inference:
                changed_keys = sorted(
                    k for k in reload_keys
                    if existing.get(k) != updates.get(k)
                )
                log.debug(f"[SETTINGS] Reload-required keys changed: {changed_keys}")
                import threading as _settings_reload_thr

                def _do_reload():
                    try:
                        result = gguf_inference.reload_model(await_completion=True)
                        if result.get("ok"):
                            params = result.get("params") or {}
                            log.debug(
                                "[SETTINGS] Model reloaded — "
                                f"ctx={params.get('n_ctx')} "
                                f"gpu_layers={params.get('n_gpu_layers')} "
                                f"threads={params.get('n_threads')} "
                                f"batch={params.get('n_batch')}"
                            )
                        else:
                            log.debug(f"[SETTINGS] Reload failed: {result.get('error')}")
                    except Exception as exc:
                        log.debug(f"[SETTINGS] Reload thread error: {exc}")

                _settings_reload_thr.Thread(
                    target=_do_reload, name="settings-reload", daemon=True,
                ).start()
        except Exception as e:
            log.debug(f"[SETTINGS] Active reload skipped: {e}")

        if not silent:
            if requires_reload:
                msg = (
                    "Settings saved.\n\n"
                    "Runtime/model changes take effect on the next message\n"
                    "(the model reloads automatically with the new parameters)."
                )
            else:
                msg = "Settings saved.\n\nVisual, identity, and image-studio preferences updated."
            QMessageBox.information(
                self, "Settings Saved",
                msg
            )
            self.status_signal.emit("Settings saved")

    def toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.apply_theme()

    def apply_theme(self):
        if self.current_theme == "dark":
            _qss = ("""
                QMainWindow, QWidget {
                    background: #141821;
                    color: #e6ecf5;
                }
                QMenuBar {
                    background: #10141c;
                    color: #dce4f2;
                    border-bottom: 1px solid #232b38;
                }
                QMenuBar::item {
                    background: transparent;
                    padding: 6px 10px;
                }
                QMenuBar::item:selected {
                    background: #243043;
                    border-radius: 4px;
                }
                QMenu {
                    background: #18202c;
                    color: #e6ecf5;
                    border: 1px solid #2b3647;
                }
                QMenu::item:selected {
                    background: #273449;
                }
                QToolBar {
                    background: #0f131a;
                    border-bottom: 1px solid #232b38;
                    spacing: 6px;
                    padding: 6px 8px;
                }
                QToolButton, QPushButton {
                    background: #232b38;
                    color: #edf3fb;
                    border: 1px solid #334055;
                    padding: 8px 10px;
                    border-radius: 8px;
                }
                QToolButton:hover, QPushButton:hover {
                    background: #2c384d;
                    border-color: #4e6a91;
                }
                QToolButton:pressed, QPushButton:pressed {
                    background: #1c2431;
                }
                QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
                    background: #10151e;
                    color: #edf3fb;
                    border: 1px solid #2c384c;
                    border-radius: 8px;
                    padding: 6px;
                    selection-background-color: #35507a;
                }
                QComboBox QAbstractItemView, QListWidget {
                    background: #121925;
                }
                QGroupBox {
                    border: 1px solid #2b3647;
                    border-radius: 10px;
                    margin-top: 12px;
                    padding-top: 12px;
                    background: #181e29;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px;
                    color: #94a8c6;
                }
                QWidget {
                    font-size: 11px;
                }
                QLabel {
                    font-size: 11px;
                }
                QPushButton {
                    font-size: 11px;
                    padding: 3px 8px;
                }
                QGroupBox {
                    font-size: 11px;
                }
                QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                    font-size: 11px;
                }
                QMenuBar {
                    font-size: 11px;
                }
                QMenu {
                    font-size: 11px;
                }
                QToolBar QToolButton {
                    font-size: 11px;
                }
                QTabWidget::pane {
                    border: 1px solid #243043;
                    background: #141821;
                }
                QTabBar::tab {
                    background: #111723;
                    color: #9fb0c9;
                    padding: 3px 8px;
                    border: 1px solid #243043;
                    border-bottom: none;
                    border-top-left-radius: 5px;
                    border-top-right-radius: 5px;
                    margin-right: 1px;
                    font-size: 11px;
                    min-width: 0px;
                }
                QTabBar::tab:selected {
                    background: #1f2a3a;
                    color: #ffffff;
                }
                QSplitter::handle {
                    background: #202938;
                }
                QScrollBar:vertical {
                    background: #10151d;
                    width: 12px;
                    margin: 2px;
                }
                QScrollBar::handle:vertical {
                    background: #334055;
                    border-radius: 6px;
                    min-height: 26px;
                }
                QStatusBar {
                    background: #10141c;
                    color: #dce4f2;
                    border-top: 1px solid #232b38;
                }
            """)
            # The stylesheet's px font rules override the QApplication font,
            # which froze Ctrl+scroll zoom in dark theme (light theme has no
            # stylesheet, so it zoomed fine). Derive the size from the live
            # app font so zoom scales BOTH themes; the zoom filter re-applies
            # the theme after each font change.
            try:
                _app_font_pt = int(QApplication.instance().font().pointSize())
            except Exception:
                _app_font_pt = 0
            if _app_font_pt > 0:
                _qss = _qss.replace("font-size: 11px", f"font-size: {_app_font_pt}pt")
            self.setStyleSheet(_qss)
        else:
            self.setStyleSheet("")
