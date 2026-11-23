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
            log.debug("suppressed exception", exc_info=True)

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
                    log.debug("suppressed exception", exc_info=True)
                break

    def open_settings_tab(self):
        try:
            idx = self.tabs.indexOf(self._settings_root)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def open_agent_wizard(self):
        """Open the AgentSpec wizard — same format as Settings ▸ Agents ▸ Create."""
        dlg = AgentCreateDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._wizard_display_message(
                "Agent specification saved. If it is still disabled, enable it in "
                "**Settings → Agents**, then try a message that matches its triggers."
            )

    def _open_prefilled_agent_spec(self, prefill: dict):
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
            # All 3 answers collected — open the spec wizard prefilled for review
            self._agent_wizard_state = None
            answers = state["answers"]
            self._wizard_display_message(
                "Got all 3 answers — opening the agent specification editor for review."
            )
            prefill = AgentCreateDialog.prefill_from_legacy_wizard(*answers)
            QTimer.singleShot(
                0,
                lambda: self._open_prefilled_agent_spec(prefill),
            )

    def open_advanced_settings(self, tab="agents"):
        """The settings dialog at a page named by its key (AdvancedSettingsDialog.open_page)."""
        dlg = AdvancedSettingsDialog(parent=self, start_tab=tab)
        dlg.exec()

    def open_extensions_page(self, page: str = "installed") -> None:
        """The Extensions tab at one of its pages: where plugins are installed and managed."""
        ext = getattr(self, "_extensions_widget", None)
        if ext is None:
            return
        self._focus_tab_widget(ext)
        if hasattr(ext, "open_page"):
            ext.open_page(page)

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

    @staticmethod
    def _shown_cache_type(stored) -> str:
        """The settings entry for a stored KV cache type (gguf_sizes.cache_setting)."""
        from eli.core.gguf_sizes import cache_setting
        return cache_setting(stored)

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
            "Models",
            "Which models ELI thinks, sees, draws, speaks and listens with. Everything found in your "
            "model folders is offered: add a folder once and every list sees it. The voice applies at "
            "once; the rest with Save."
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

        # Custom card: every chat model found in the model folders, then the path it stands for
        form3 = self._section_card(vbox, "CUSTOM GGUF")
        self.custom_model_combo = QComboBox()
        self.custom_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.custom_model_combo.setToolTip("Every chat model in your model folders. Choosing one fills the path below.")
        self.custom_model_combo.activated.connect(self._custom_model_chosen)
        form3.addRow(self._field_label("Found"), self.custom_model_combo)
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

        # Vision: a model and the projector beside it, the pair chosen as one
        form5 = self._section_card(vbox, "VISION — DESCRIBING IMAGES AND THE SCREEN")
        self.vision_pair_combo = QComboBox()
        self.vision_pair_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.vision_pair_combo.activated.connect(lambda _i: self._vision_pair_chosen(fast=False))
        form5.addRow(self._field_label("Model"), self.vision_pair_combo)
        self.vision_fast_pair_combo = QComboBox()
        self.vision_fast_pair_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.vision_fast_pair_combo.activated.connect(lambda _i: self._vision_pair_chosen(fast=True))
        form5.addRow(self._field_label("Quick glance"), self.vision_fast_pair_combo)
        vision_btns = QHBoxLayout()
        vision_btns.setSpacing(8)
        browse_vision_btn = QPushButton("Browse model…")
        browse_vision_btn.clicked.connect(lambda: self.browse_vision_model(fast=False))
        vision_btns.addWidget(browse_vision_btn)
        browse_fast_btn = QPushButton("Browse quick-glance model…")
        browse_fast_btn.clicked.connect(lambda: self.browse_vision_model(fast=True))
        vision_btns.addWidget(browse_fast_btn)
        vision_btns.addStretch()
        form5.addRow("", vision_btns)
        self.vision_note = QLabel("")
        self.vision_note.setWordWrap(True)
        self.vision_note.setStyleSheet("color:#606880;font-size:11px;")
        form5.addRow("", self.vision_note)
        self._vision_pick = ("", "")
        self._vision_fast_pick = ("", "")

        # Image generation: the same setting the Image Studio and the Identity page use
        form6 = self._section_card(vbox, "IMAGE GENERATION")
        self.image_model_select = QComboBox()
        self.image_model_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.image_model_select.activated.connect(self._image_model_selected)
        form6.addRow(self._field_label("Model"), self.image_model_select)
        image_btns = QHBoxLayout()
        image_btns.setSpacing(8)
        image_browse_btn = QPushButton("Browse…")
        image_browse_btn.clicked.connect(self.browse_image_model_path)
        image_btns.addWidget(image_browse_btn)
        image_detect_btn = QPushButton("Detect")
        image_detect_btn.clicked.connect(self.refresh_image_model_sources)
        image_btns.addWidget(image_detect_btn)
        image_btns.addStretch()
        form6.addRow("", image_btns)

        # Voice: the same voice list as the Runtime page, applied at once
        form7 = self._section_card(vbox, "VOICE")
        self.models_voice_combo = QComboBox()
        self.models_voice_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.models_voice_combo.activated.connect(
            lambda _i: self._choose_voice(self.models_voice_combo.currentData() or self.models_voice_combo.currentText()))
        form7.addRow(self._field_label("Voice"), self.models_voice_combo)
        voice_btns = QHBoxLayout()
        voice_btns.setSpacing(8)
        voice_test_btn = QPushButton("▶ Test")
        voice_test_btn.clicked.connect(
            lambda: self.test_voice(self.models_voice_combo.currentData() or self.models_voice_combo.currentText()))
        voice_btns.addWidget(voice_test_btn)
        voice_more_btn = QPushButton("＋ More voices / accents…")
        voice_more_btn.clicked.connect(self.open_voice_library)
        voice_btns.addWidget(voice_more_btn)
        voice_btns.addStretch()
        form7.addRow("", voice_btns)
        voice_note = QLabel("To make a voice from a recording of someone, use the Runtime page.")
        voice_note.setStyleSheet("color:#606880;font-size:11px;")
        form7.addRow("", voice_note)

        # Speech to text
        form8 = self._section_card(vbox, "SPEECH TO TEXT")
        self.stt_model_combo = QComboBox()
        self.stt_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        form8.addRow(self._field_label("Model"), self.stt_model_combo)
        stt_note = QLabel("Takes effect at the next transcription. A model not on this computer downloads "
                          "on first use. ELI_WHISPER_MODEL in the environment overrides this.")
        stt_note.setWordWrap(True)
        stt_note.setStyleSheet("color:#606880;font-size:11px;")
        form8.addRow("", stt_note)

        # Model folders: the one list every scanner reads
        form9 = self._section_card(vbox, "MODEL FOLDERS")
        self.model_folders_list = QListWidget()
        self.model_folders_list.setMaximumHeight(120)
        form9.addRow(self._field_label("Scanned"), self.model_folders_list)
        folder_btns = QHBoxLayout()
        folder_btns.setSpacing(8)
        add_folder_btn = QPushButton("Add folder…")
        add_folder_btn.clicked.connect(self.add_model_folder_dialog)
        folder_btns.addWidget(add_folder_btn)
        remove_folder_btn = QPushButton("Remove")
        remove_folder_btn.clicked.connect(self.remove_selected_model_folder)
        folder_btns.addWidget(remove_folder_btn)
        rescan_btn = QPushButton("Rescan")
        rescan_btn.clicked.connect(self.refresh_model_choices)
        folder_btns.addWidget(rescan_btn)
        folder_btns.addStretch()
        form9.addRow("", folder_btns)

        # Memory embedder: shown, not changed here (memory's index is built for it)
        form10 = self._section_card(vbox, "MEMORY EMBEDDER")
        self.embedder_label = QLabel("")
        self.embedder_label.setWordWrap(True)
        self.embedder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form10.addRow(self._field_label("Model"), self.embedder_label)
        embed_note = QLabel("Memory's index is built for this embedder. To use another, set ELI_EMBED_MODEL_PATH "
                            "and rebuild the index (Advanced → Rebuild FAISS).")
        embed_note.setWordWrap(True)
        embed_note.setStyleSheet("color:#606880;font-size:11px;")
        form10.addRow("", embed_note)

        vbox.addStretch()
        self.refresh_model_choices()
        return page

    # ── the Models page's choices ─────────────────────────────────────────────────────────────
    def refresh_model_choices(self):
        """Fill every list on the Models page from the model folders, keeping what is chosen."""
        self._refresh_custom_model_combo()
        self._refresh_vision_combos()
        self.refresh_image_model_sources()
        self._reload_voice_selectors()
        self._refresh_stt_models()
        self._refresh_model_folders_list()
        try:
            from eli.memory.vector_store import resolve_embedder_path
            self.embedder_label.setText(resolve_embedder_path())
        except Exception as ex:
            log.debug(f"[GUI] embedder path unavailable: {ex}", exc_info=True)
            self.embedder_label.setText("not found")

    def _refresh_custom_model_combo(self):
        combo = getattr(self, "custom_model_combo", None)
        if combo is None:
            return
        try:
            from eli.core.model_folders import chat_models
            found = chat_models()
        except Exception as ex:
            log.debug(f"[GUI] chat model scan failed: {ex}", exc_info=True)
            found = {}
        current = self.model_path_input.text().strip() if hasattr(self, "model_path_input") else ""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— choose a model —", "")
        for name, path in found.items():
            try:
                from eli.core.gguf_sizes import model_bytes
                from eli.core.mem_units import bytes_to_gib
                label = f"{name}  ({bytes_to_gib(model_bytes(path)):.2f} GB)"
            except Exception:
                label = name
            combo.addItem(label, str(path))
        idx = combo.findData(current) if current else -1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _custom_model_chosen(self, _index: int):
        path = str(self.custom_model_combo.currentData() or "")
        if not path:
            return
        self.model_path_input.setText(path)
        idx = self.provider_combo.findData("custom_gguf")
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)

    def _vision_pairs(self):
        try:
            from eli.perception.vision import discovered_pairs, rescan
            rescan()
            return list(discovered_pairs())
        except Exception as ex:
            log.debug(f"[GUI] vision pair scan failed: {ex}", exc_info=True)
            return []

    def _refresh_vision_combos(self):
        pairs = self._vision_pairs()
        from eli.core.mem_units import bytes_to_gib
        for combo, auto_label, pick in (
                (getattr(self, "vision_pair_combo", None), "Auto — the largest pair found", self._vision_pick),
                (getattr(self, "vision_fast_pair_combo", None), "Auto — the smallest pair found", self._vision_fast_pick)):
            if combo is None:
                continue
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(auto_label, ("", ""))
            for base, proj, size in pairs:
                combo.addItem(f"{Path(base).name} + {Path(proj).name}  ({bytes_to_gib(size):.2f} GB)",
                              (str(base), str(proj)))
            if pick and pick[0]:
                idx = next((i for i in range(combo.count()) if (combo.itemData(i) or ("", ""))[0] == pick[0]), -1)
                if idx < 0:
                    combo.addItem(f"{Path(pick[0]).name}" + (f" + {Path(pick[1]).name}" if pick[1] else "  (no projector)"), pick)
                    idx = combo.count() - 1
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)
        if hasattr(self, "vision_note"):
            self.vision_note.setText(
                f"{len(pairs)} vision model(s) with a projector found in the model folders." if pairs else
                "No vision model with a projector found. A vision GGUF needs its mmproj file beside it; "
                "put both in a model folder, or add their folder below.")

    def _vision_pair_chosen(self, *, fast: bool):
        combo = self.vision_fast_pair_combo if fast else self.vision_pair_combo
        pick = tuple(combo.currentData() or ("", ""))
        if fast:
            self._vision_fast_pick = pick
        else:
            self._vision_pick = pick

    def browse_vision_model(self, *, fast: bool = False):
        """A vision GGUF chosen by file; the projector found beside it comes with it, and its folder
        joins the model folders."""
        path, _ = QFileDialog.getOpenFileName(self, "Choose a vision model", "", "GGUF Files (*.gguf);;All Files (*)")
        if not path:
            return
        proj = ""
        try:
            from eli.perception.vision import _discover_vl_pairs
            for base, projector, _size in _discover_vl_pairs(Path(path).parent):
                if Path(base).resolve() == Path(path).resolve():
                    proj = str(projector)
                    break
        except (ImportError, OSError, ValueError) as ex:
            log.debug(f"[GUI] projector lookup failed: {ex}", exc_info=True)
        pick = (path, proj)
        if fast:
            self._vision_fast_pick = pick
        else:
            self._vision_pick = pick
        try:
            from eli.core.model_folders import remember_folder_of
            remember_folder_of(path)
        except (ImportError, OSError, ValueError) as ex:
            log.debug(f"[GUI] folder not remembered: {ex}", exc_info=True)
        self._refresh_vision_combos()
        if not proj:
            self.vision_note.setText(f"No projector (mmproj) found beside {Path(path).name}; vision needs one to run.")

    def _image_model_selected(self, _index: int):
        path = str(self.image_model_select.currentData() or "")
        if hasattr(self, "image_model_path_input"):
            self.image_model_path_input.setText(path)
        if hasattr(self, "image_model_combo"):
            idx = self.image_model_combo.findData(path)
            if idx >= 0:
                self.image_model_combo.setCurrentIndex(idx)
            else:
                self.image_model_combo.setEditText(path)

    def _refresh_stt_models(self):
        combo = getattr(self, "stt_model_combo", None)
        if combo is None:
            return
        try:
            from eli.perception.local_whisper_stt import available_models
            models = available_models()
        except Exception as ex:
            log.debug(f"[GUI] speech model list failed: {ex}", exc_info=True)
            models = []
        current = combo.currentData() or getattr(self, "_stt_model_saved", "")
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— ELI's default —", "")
        for m in models:
            combo.addItem(f"{m['name']}   ({'on this computer' if m.get('on_disk') else 'downloads on first use'})",
                          m["name"])
        idx = combo.findData(current) if current else -1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_model_folders_list(self):
        widget = getattr(self, "model_folders_list", None)
        if widget is None:
            return
        widget.clear()
        try:
            from eli.core.model_folders import ENV, model_folders, named_model_folders
            import os as _os
            env_named = {str(Path(p).expanduser()) for p in (_os.environ.get(ENV) or "").split(_os.pathsep) if p.strip()}
            named = {str(p) for p in named_model_folders()}
            for folder in model_folders():
                text = str(folder)
                if text in env_named:
                    text += "   (named by the environment)"
                elif text in named:
                    text += "   (named by you)"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, str(folder))
                widget.addItem(item)
        except (ImportError, OSError, ValueError) as ex:
            log.debug(f"[GUI] model folders list failed: {ex}", exc_info=True)

    def add_model_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Add a model folder", str(Path.home()))
        if not folder:
            return
        try:
            from eli.core.model_folders import add_model_folder
            added = add_model_folder(folder)
        except Exception as ex:
            QMessageBox.warning(self, "Model folders", f"Could not add the folder:\n{ex}")
            return
        self.status_signal.emit(f"Model folder {'added' if added else 'already listed'}: {folder}")
        self.refresh_model_choices()

    def remove_selected_model_folder(self):
        item = self.model_folders_list.currentItem()
        if item is None:
            return
        folder = str(item.data(Qt.ItemDataRole.UserRole) or "")
        try:
            from eli.core.model_folders import remove_model_folder
            removed = remove_model_folder(folder)
        except Exception as ex:
            QMessageBox.warning(self, "Model folders", f"Could not remove the folder:\n{ex}")
            return
        if not removed:
            QMessageBox.information(self, "Model folders",
                                    "Only a folder you named here can be removed. ELI's own folders, the folders of "
                                    "the models in use, and folders named by ELI_MODEL_DIRS stay.")
        self.refresh_model_choices()

    def _choose_voice(self, voice_id: str):
        """Make this ELI's voice now (saved as the Runtime page's selector saves it)."""
        try:
            from eli.perception.tts_router import resolve_voice_id, set_active_voice
            set_active_voice(resolve_voice_id(str(voice_id or "")) or str(voice_id or ""))
            self._reload_voice_selectors()
        except (ImportError, OSError, ValueError, RuntimeError) as ex:
            log.debug(f"[TTS] voice choice failed: {ex}", exc_info=True)

    def test_voice(self, voice_id: str):
        try:
            from eli.perception.tts_router import speak
            speak("ELI voice test. Systems operational.", voice_name=str(voice_id or ""))
        except (ImportError, OSError, RuntimeError, ValueError) as e:
            log.debug(f"[TTS] Test error: {e}")

    def open_voice_library(self):
        """The upstream voice library; every voice list is filled again when it closes."""
        try:
            from eli.gui.widgets.voice_downloader import VoiceDownloadDialog
            dlg = VoiceDownloadDialog(self)
            dlg.voices_changed.connect(self._reload_voice_selectors)
            dlg.exec()
        except Exception as e:
            log.debug(f"[TTS] voice library dialog failed: {e}", exc_info=True)
            QMessageBox.warning(self, "Voice library", f"Could not open the voice library:\n{e}")

    def _reload_voice_selectors(self):
        """Repopulate every voice dropdown after the library installs a new voice,
        preserving the current selection so the user never loses their choice."""
        try:
            from eli.perception.tts_router import (
                list_voices, get_active_voice, voice_display_name,
            )
            voices = list(list_voices() or [])
            active = get_active_voice()
        except Exception as ex:
            log.debug(f"[GUI] voice reload failed: {ex}", exc_info=True)
            return
        for combo in (getattr(self, "voice_combo", None),
                      getattr(self, "_voice_selector", None),
                      getattr(self, "models_voice_combo", None)):
            if combo is None:
                continue
            try:
                keep_id = combo.currentData()
                if not keep_id:
                    keep_id = combo.currentText()
                combo.blockSignals(True)
                combo.clear()
                active_idx = 0
                for i, v in enumerate(voices):
                    combo.addItem(voice_display_name(v), v)
                    if v == (keep_id if keep_id in voices else active):
                        active_idx = i
                if combo.count():
                    combo.setCurrentIndex(active_idx)
                combo.setEnabled(bool(voices))
                combo.blockSignals(False)
            except Exception as ex:
                log.debug(f"[GUI] voice combo repopulate failed: {ex}", exc_info=True)

    @staticmethod
    def _cpu_threads_box() -> QSpinBox:
        """CPU threads for llama.cpp: 0 is auto (this machine's physical cores, measured fastest,
        eli/core/hardware_profile.recommend_cpu_threads), up to every thread it has. The box ran
        1-64 and showed 8: the saved auto 0 came up as 1, the window's Load asked llama.cpp for one
        thread and the next save wrote 1 over auto; a machine with more than 64 could not be set."""
        box = QSpinBox()
        box.setRange(0, max(1, os.cpu_count() or 1))
        box.setSpecialValueText("auto")
        box.setValue(0)
        try:
            from eli.core.hardware_profile import recommend_cpu_threads
            auto = f"auto is {int(recommend_cpu_threads())} on this machine (its physical cores)"
        except Exception:
            auto = "auto is this machine's physical cores"
        box.setToolTip(f"Threads llama.cpp runs on the CPU; {auto}. A number is kept as yours.")
        return box

    def _build_settings_runtime_page(self) -> QWidget:
        page, vbox = self._settings_page(
            "Runtime",
            "Control how the model is loaded and how much hardware it uses."
        )
        form = self._section_card(vbox, "INFERENCE ENGINE")

        self.n_ctx_input = QSpinBox()
        # 0 is auto; the upper end is llama.cpp's own limit, the model's trained length caps it at load
        self.n_ctx_input.setRange(0, 2_147_483_647)
        self.n_ctx_input.setSpecialValueText("auto")
        self.n_ctx_input.setValue(0)
        self.n_ctx_input.setSingleStep(2048)
        self.n_ctx_input.setToolTip(
            "Token context window. auto sizes it for the model and this machine's free memory; "
            "a number is kept for the model it was set for.")
        form.addRow(self._field_label("Context size"), self.n_ctx_input)

        self.n_threads_input = self._cpu_threads_box()
        form.addRow(self._field_label("CPU threads"), self.n_threads_input)

        self.n_gpu_layers_input = QSpinBox()
        self.n_gpu_layers_input.setRange(0, 9999)
        self.n_gpu_layers_input.setValue(99)
        self.n_gpu_layers_input.setToolTip("Loader parameter requested for GPU offload. Backend support is reported separately. (0 = CPU request, 99 = all-layer request)")
        form.addRow(self._field_label("GPU-layer parameter"), self.n_gpu_layers_input)

        self.batch_size_input = QSpinBox()
        self.batch_size_input.setRange(1, 2_147_483_647)   # llama.cpp limits it to the context
        from eli.core.runtime_settings import DEFAULTS as _setting_defaults
        self.batch_size_input.setValue(int(_setting_defaults["batch_size"]))   # the setting's own default
        self.batch_size_input.setSingleStep(64)
        self.batch_size_input.setToolTip("Prompt processing batch size — larger is faster but uses more VRAM")
        form.addRow(self._field_label("Batch size"), self.batch_size_input)

        # KV-cache quantization. q8_0 halves the cache and answers the same as fp16; q4_0 gives
        # wrong answers with this llama.cpp (measured), so it is not offered and a stored one
        # loads as q8_0. Empty value = no quantization (fp16).
        self.cache_type_k_combo = QComboBox()
        self.cache_type_k_combo.addItems(["", "q8_0", "f16"])
        self.cache_type_k_combo.setToolTip(
            "KV cache quantization for keys. q8_0 = nearly twice the context for the same "
            "VRAM, with the same answers. Empty = fp16 (no quantization)."
        )
        form.addRow(self._field_label("KV cache K"), self.cache_type_k_combo)

        self.cache_type_v_combo = QComboBox()
        self.cache_type_v_combo.addItems(["", "q8_0", "f16"])
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
                voice_display_name as _voice_display_name,
                resolve_voice_id as _resolve_voice_id,
            )
            _be = _tts_backends()
            _status_lines = [
                f"Kokoro (CPU):     {'✅' if _be.get('kokoro') else '❌ not installed'}"
                + ("" if _be.get("kokoro_ready") else "  (files fetched the first time ELI speaks)"),
                f"Qwen3-TTS (GPU):  {'✅ natural voices and cloning' if _be.get('neural_available') else '— not on this machine'}",
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
            _active_idx = 0
            for i, _v in enumerate(_voices):
                self._voice_selector.addItem(_voice_display_name(_v), _v)
                if _v == _active:
                    _active_idx = i
            if _voices:
                self._voice_selector.setCurrentIndex(_active_idx)

            _styles_note = QLabel(
                "Style presets (calm, robotic, …) = ELI's Kokoro voices + ffmpeg effects — "
                "not movie/TV character clones. Clone your own timbre: drop a short "
                "reference clip below (spoken by Qwen3-TTS on a GPU with room for it)."
            )
            _styles_note.setWordWrap(True)
            _styles_note.setStyleSheet("color:#8eaac8; font-size:11px;")
            tts_form.addRow(_styles_note)

            def _on_voice_changed(_name: str):
                try:
                    _vid = _resolve_voice_id(_name)
                    if getattr(self, "_voice_selector", None) is not None:
                        _data = self._voice_selector.currentData()
                        if _data:
                            _vid = str(_data)
                    _set_active_voice(_vid)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            self._voice_selector.currentTextChanged.connect(_on_voice_changed)

            _voice_row_widget = QWidget()
            _voice_row_layout = QHBoxLayout(_voice_row_widget)
            _voice_row_layout.setContentsMargins(0, 0, 0, 0)
            _voice_row_layout.setSpacing(8)
            _voice_lbl = QLabel("Voice:")
            _voice_lbl.setStyleSheet("color:#8eaac8; min-width:50px;")
            _voice_row_layout.addWidget(_voice_lbl)
            _voice_row_layout.addWidget(self._voice_selector, 1)
            tts_form.addRow(_voice_row_widget)

            # Test voice button
            _test_btn = QPushButton("▶ Test Voice")
            _test_btn.setStyleSheet(
                "QPushButton { background:#1e3a2a; color:#5dbb7a; border:1px solid #2a5a3a;"
                " border-radius:4px; padding:4px 10px; }"
                "QPushButton:hover { background:#254a30; }"
            )
            def _test_voice():
                self.test_voice(self._voice_selector.currentData() or self._voice_selector.currentText())
            _test_btn.clicked.connect(_test_voice)
            tts_form.addRow(_test_btn)

            # Voice library — every Kokoro voice (US and UK English, by grade). Repopulates every
            # voice combo on close so a freshly fetched voice is immediately selectable.
            _get_btn = QPushButton("＋ Voice library…")
            _get_btn.setStyleSheet(
                "QPushButton { background:#1e2a3a; color:#7aa7d0; border:1px solid #2a4a6a;"
                " border-radius:4px; padding:4px 10px; }"
                "QPushButton:hover { background:#25384a; }"
            )
            _get_btn.setToolTip(
                "Browse ELI's voices — American and British English, female and male — "
                "and fetch any that are not on this machine yet.")

            _get_btn.clicked.connect(self.open_voice_library)
            tts_form.addRow(_get_btn)

            # Create-your-own-voice — drop in a recording (wav/mp3/mp4) and ELI builds
            # a voice from it (Qwen3-TTS on a GPU with room, else Chatterbox, else XTTS).
            # Without any of them the voice is registered and ELI keeps its own voice.
            _VOICE_CLIP_EXTS = (".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".flac", ".aac", ".webm")

            def _create_voice_from_path(path: str):
                try:
                    import os as _os
                    if _os.path.splitext(path)[1].lower() not in _VOICE_CLIP_EXTS:
                        QMessageBox.warning(self, "Create voice",
                                             f"Not a recognised audio/video clip: {path}")
                        return
                    from eli.gui.qt_compat import QInputDialog
                    default_name = _os.path.splitext(_os.path.basename(path))[0]
                    name, ok = QInputDialog.getText(self, "Name this voice",
                                                    "Voice name:", text=default_name)
                    if not ok or not name.strip():
                        return
                    from eli.perception import tts_xtts
                    res = tts_xtts.add_clone(name.strip(), path)
                    if res.get("ok"):
                        try:
                            from eli.perception.tts_router import set_active_voice
                            set_active_voice(res.get("id") or ("clone:" + name.strip()))
                        except Exception:
                            log.debug("[TTS] set created voice active failed", exc_info=True)
                        self._reload_voice_selectors()
                        # The remediation has to match the runtime: a packaged build has no
                        # project root and no writable site-packages, so a pip line is no fix there.
                        extra = ""
                        if not res.get("synth_ready"):
                            try:
                                from eli.core.paths import is_frozen as _is_frozen
                                _packaged = bool(_is_frozen())
                            except Exception:
                                _packaged = False
                            if _packaged:
                                extra = (
                                    "\n\nNOTE: this build cannot synthesise cloned voices — the "
                                    "voice-cloning engine (Qwen3-TTS, for a GPU) is not included, so "
                                    f"'{name.strip()}' is saved but ELI will keep speaking in its "
                                    "own voice.\nTo use it, run ELI from the source install "
                                    "(README ▸ Install) with the voice-gpu extra on a machine with a GPU."
                                )
                            else:
                                extra = (
                                    "\n\nInstall the voice-cloning engine to make it sound like the "
                                    "recording (it runs on a GPU):\n  pip install -e \".[voice-gpu]\"  "
                                    "(from the ELI project root)"
                                )
                        QMessageBox.information(self, "Voice created",
                                               f"Created and selected the voice '{name.strip()}'.{extra}")
                    else:
                        QMessageBox.warning(self, "Couldn't create voice",
                                            str(res.get("error") or "unknown error"))
                except Exception as e:
                    log.debug(f"[TTS] create-voice failed: {e}", exc_info=True)
                    QMessageBox.warning(self, "Create voice", f"Could not create the voice:\n{e}")

            def _browse_voice_file():
                path, _ = QFileDialog.getOpenFileName(
                    self, "Choose a voice recording", "",
                    "Audio/Video (*.wav *.mp3 *.mp4 *.m4a *.ogg *.flac *.aac *.webm);;All files (*)")
                if path:
                    _create_voice_from_path(path)

            class VoiceDropZone(QFrame):
                """Dashed drop target for a voice recording — drag a clip in, or
                click to browse. Single point of entry into add_clone()."""
                _IDLE = ("QFrame { background:#241a33; border:2px dashed #4a2a6a;"
                          " border-radius:6px; }"
                          "QLabel { background:transparent; color:#b78ad0; }")
                _HOVER = ("QFrame { background:#2e2140; border:2px dashed #b78ad0;"
                          " border-radius:6px; }"
                          "QLabel { background:transparent; color:#d8b8f0; }")

                def __init__(self, on_path, on_browse, parent=None):
                    super().__init__(parent)
                    self._on_path = on_path
                    self._on_browse = on_browse
                    self.setAcceptDrops(True)
                    self.setFixedHeight(56)
                    self.setStyleSheet(self._IDLE)
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                    lay = QVBoxLayout(self)
                    lay.setContentsMargins(10, 4, 10, 4)
                    lbl = QLabel("🎙  Drop an audio/video clip here, or click to browse…")
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lay.addWidget(lbl)
                    self.setToolTip(
                        "Build a custom ELI voice from a short, clean recording (~6–20s) of "
                        "the voice you want — .wav, .mp3, .mp4, .m4a, .ogg, .flac, .aac, .webm. "
                        "Needs the Chatterbox voice engine to actually reproduce it "
                        "(pip install -e \".[chatterbox]\" from the ELI project root).")

                def _first_local_clip(self, event) -> str:
                    if not event.mimeData().hasUrls():
                        return ""
                    for url in event.mimeData().urls():
                        p = url.toLocalFile()
                        if p and p.lower().endswith(_VOICE_CLIP_EXTS):
                            return p
                    return ""

                def dragEnterEvent(self, event):
                    if self._first_local_clip(event):
                        self.setStyleSheet(self._HOVER)
                        event.acceptProposedAction()
                    else:
                        event.ignore()

                def dragLeaveEvent(self, event):
                    self.setStyleSheet(self._IDLE)

                def dropEvent(self, event):
                    self.setStyleSheet(self._IDLE)
                    path = self._first_local_clip(event)
                    if path:
                        event.acceptProposedAction()
                        self._on_path(path)

                def mousePressEvent(self, event):
                    self._on_browse()

            _drop_zone = VoiceDropZone(_create_voice_from_path, _browse_voice_file)
            tts_form.addRow(_drop_zone)

            # Record-now: capture a fresh reference from the mic — no pre-existing file.
            def _record_voice_now():
                secs = 12
                try:
                    import sounddevice as _sd, soundfile as _sf
                except Exception:
                    QMessageBox.warning(self, "Recording unavailable",
                                        "Microphone capture needs the audio extras "
                                        "(sounddevice + soundfile).")
                    return
                import tempfile as _tf
                prog = QProgressDialog(
                    f"Recording {secs}s — speak naturally now (read a couple of sentences)…",
                    "Cancel", 0, secs, self)
                prog.setWindowTitle("Recording your voice")
                prog.setWindowModality(Qt.WindowModality.WindowModal)
                prog.setMinimumDuration(0)
                holder = {"data": None, "err": None}

                def _cap():
                    try:
                        holder["data"] = _sd.rec(int(secs * 22050), samplerate=22050,
                                                 channels=1, dtype="float32")
                        _sd.wait()
                    except Exception as e:
                        holder["err"] = str(e)

                th = threading.Thread(target=_cap, daemon=True); th.start()
                import time as _time
                t0 = _time.time()
                while th.is_alive():
                    el = int(_time.time() - t0)
                    prog.setValue(min(secs, el))
                    QApplication.processEvents()
                    if prog.wasCanceled():
                        try:
                            import sounddevice as _sd2; _sd2.stop()
                        except Exception:
                            log.debug("[TTS] record stop failed", exc_info=True)
                        break
                    _time.sleep(0.1)
                th.join(timeout=1.0)
                prog.setValue(secs)
                if prog.wasCanceled():
                    return
                if holder["err"] or holder["data"] is None:
                    QMessageBox.warning(self, "Recording failed",
                                        str(holder["err"] or "no audio captured"))
                    return
                try:
                    fd, wav_path = _tf.mkstemp(suffix=".wav"); os.close(fd)
                    _sf.write(wav_path, holder["data"], 22050)
                    _create_voice_from_path(wav_path)
                finally:
                    try:
                        os.unlink(wav_path)
                    except Exception:
                        log.debug("[TTS] temp record cleanup failed", exc_info=True)

            _rec_btn = QPushButton("🔴  Record a voice now (12s from your mic)")
            _rec_btn.setStyleSheet(
                "QPushButton { background:#2a1e2a; color:#d08a9a; border:1px solid #6a2a3a;"
                " border-radius:4px; padding:5px 10px; }"
                "QPushButton:hover { background:#382530; }")
            _rec_btn.setToolTip("Speak for ~12 seconds and ELI builds a voice from your recording.")
            _rec_btn.clicked.connect(_record_voice_now)
            tts_form.addRow(_rec_btn)

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
        self.max_tokens_input.setRange(128, 2_147_483_647)  # a ceiling: each reply is sized to the window
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
        # box with no hint text, unlike every field around it. That reads as broken
        # rather than "intentionally blank", so give its internal line edit one.
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
            log.debug("suppressed exception", exc_info=True)
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
        # Read by the reply worker, which must not touch the widget.
        self._autosave_enabled = True
        self.auto_save_checkbox.toggled.connect(lambda on: setattr(self, "_autosave_enabled", bool(on)))
        self.auto_save_checkbox.setStyleSheet("color:#c8d0e0;")
        form.addRow("", self.auto_save_checkbox)

        self.log_to_file_checkbox = QCheckBox("Write session log to file")
        # the setting's own default (runtime_settings.DEFAULTS): the box said off while the log was on,
        # and a save then wrote it off
        from eli.core.runtime_settings import DEFAULTS as _settings_defaults
        self.log_to_file_checkbox.setChecked(bool(_settings_defaults.get("log_to_file")))
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
            "The <b>Agent Wizard</b> authors a full agent <b>specification</b> — "
            "objective, instruction, triggers, and success criteria — the same "
            "format used by ELI's built-in specialist agents on the bus.<br><br>"
            "Specs are saved under your ELI data directory, registered live on the "
            "agent bus, and reloaded automatically on startup. Enable/disable in "
            "<b>Settings → Agents</b>."
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
                lambda: self.open_advanced_settings("agents"),
                "Manage Agents",
                "#5e81ac",
            ),
            (
                "🧠  Cognition",
                "Tune knowledge-gathering & context limits: how many memory facts, KG\nfacts, rerank depth, and the synthesis prompt cap ELI uses per answer.",
                lambda: self.open_advanced_settings("cognition"),
                "Tune Cognition",
                "#b48ead",
            ),
            (
                "🔌  Plugins",
                "Install plugins (each one verified, scanned and asked about first), enable,\ndisable or remove them, in the Extensions tab.",
                lambda: self.open_extensions_page("installed"),
                "Manage Plugins",
                "#88c0d0",
            ),
            (
                "🔄  Self-Upgrade",
                "Run improvement cycles, rebuild the FAISS index, update the capability manifest, and refresh ELI's persona.",
                lambda: self.open_advanced_settings("upgrade"),
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
        self.n_threads_input.setValue(0)     # auto: the same count, kept as auto, not as a number of yours
        self.batch_size_input.setValue(optimal['batch_size'])
        self.temperature_input.setValue(optimal['temperature'])
        if optimal.get('max_tokens'):
            self.max_tokens_input.setValue(optimal['max_tokens'])
        self.status_signal.emit(
            f"Auto-detected: ctx={optimal['n_ctx'] or 'auto'}  gpu={optimal['n_gpu_layers']}  "
            f"threads={optimal['n_threads']}  batch={optimal['batch_size']}"
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
            # Off the main thread + pump the event loop: detect_hardware()'s uncached nvidia-smi/
            # lspci probes otherwise froze the window for a few seconds at the start of every load.
            hw = _run_pumping(_hp_detect, QApplication.instance())
            # The user's EXPLICITLY chosen ctx must anchor the recommendation, not be
            # overwritten by the auto target. Source of truth (highest first):
            # the startup dialog's "Direct context window" field (ELI_FORCE_CTX), else
            # the live ctx spinbox (persisted from settings.json n_ctx). recommend()
            # honours it and only trims it on a real VRAM constraint (logged).
            import os as _os_ctx
            _forced_ctx = (_os_ctx.environ.get("ELI_FORCE_CTX") or "").strip()
            try:
                _spin_ctx = int(self.n_ctx_input.value())
            except Exception:
                _spin_ctx = 0
            # The box counts as the operator's only when it was changed this session or was saved
            # for this model (startup dialog rule): a number left over from another model, or the
            # tuner's own figure from an older build, is not a choice.
            try:
                from eli.core.runtime_settings import load_settings as _rs_saved_ctx
                _saved = _rs_saved_ctx() or {}
            except Exception:
                _saved = {}
            _ctx_is_theirs = (
                _spin_ctx != int(_saved.get("n_ctx") or 0)
                or Path(str(_saved.get("n_ctx_model") or "")).name == Path(str(model_path)).name)
            if _forced_ctx.isdigit() and int(_forced_ctx) > 0:
                _user_pinned_ctx = int(_forced_ctx)
            elif _spin_ctx > 0 and _ctx_is_theirs:
                _user_pinned_ctx = _spin_ctx
            else:
                _user_pinned_ctx = None
            # The user's own GPU-layer choice, protected exactly like ctx below.
            # A tuning run made while the GPU is busy (a video decoding, a game)
            # computes a low layer count for THOSE conditions — persisting it as
            # the user's preference makes a transient state permanent. Observed
            # live: a pinned 99 was overwritten to 13 while a video played, and
            # every later boot then loaded at 13 with the GPU idle.
            try:
                _user_pinned_layers = int(self.n_gpu_layers_input.value())
            except Exception:
                _user_pinned_layers = 0
            try:
                _user_pinned_batch = int(self.batch_size_input.value())
            except Exception:
                _user_pinned_batch = 0
            from eli.core.mem_units import bytes_to_gib
            rec = _hp_recommend(hw, [{
                "name": model_file.name,
                "path": str(model_file),
                "size_bytes": size_bytes,
                "size_gb": bytes_to_gib(size_bytes),
            }], user_ctx=_user_pinned_ctx)

            # Runtime CUDA/backend may be unavailable even if VRAM probe reports
            # a GPU. Keep the measured layer fit for display; clamp batch for CPU load.
            if _gpu_support is False:
                _fit_layers = int(rec.n_gpu_layers or 0)
                rec.batch_size = min(int(getattr(rec, "batch_size", 128) or 128), 128)
                if _fit_layers > 0 and getattr(hw, "gpu_integrated", False):
                    self._hardware_tuning_log(
                        f"GPU backend inactive — {_fit_layers} layers fit integrated "
                        f"graphics ({hw.gpu_name}); load uses CPU until a GPU backend "
                        f"is active."
                    )
                elif _fit_layers > 0:
                    self._hardware_tuning_log(
                        f"GPU backend inactive — {_fit_layers} layers fit VRAM; "
                        f"load uses CPU until a GPU backend is active."
                    )
                else:
                    self._hardware_tuning_log(
                        "GPU offload unavailable at runtime -> CPU-safe tuning "
                        "(gpu_layers=0, batch<=128)."
                    )

            # KV-cache quantization: hardware fact — safe to update in the combo
            # (affects how VRAM is used, not a user performance preference).
            _ck = self._shown_cache_type(getattr(rec, "cache_type_k", ""))
            _cv = self._shown_cache_type(getattr(rec, "cache_type_v", ""))
            self.cache_type_k_combo.setCurrentIndex(max(0, self.cache_type_k_combo.findText(_ck)))
            self.cache_type_v_combo.setCurrentIndex(max(0, self.cache_type_v_combo.findText(_cv)))

            for line in list(getattr(rec, "reasoning", []) or []):
                self._hardware_tuning_log(str(line))

            # These are a RECOMMENDATION, not what will load. The comment here
            # used to say "AUTHORITATIVE ... the primary load parameters
            # (attempt 1)", which stopped being true when the operator's own
            # settings were moved to the front of the ladder: attempt 1 is now
            # `requested`, and the value that actually loads is whichever rung
            # `selected=` reports. Displaying a recommendation as authoritative
            # is how the panel came to read "ctx=6144 gpu_layers=31" while the
            # session was running at ctx=10384 gpu_layers=28 — three different
            # numbers on screen at once, none of them labelled.
            #
            # Show the recommendation AS a recommendation. What actually loads is
            # reported in the dock's log below it, which subscribes to the same
            # [GUI][LOAD] / [LOAD_PLAN] lines the terminal prints (see
            # HardwareTuningLogRelay) — one source of truth, not a second figure
            # computed for the panel.
            from eli.core.hardware_profile import describe_gpu_layers as _describe_gpu_rec
            summary = (
                f"HW Profile (recommended): ctx={int(rec.n_ctx)} "
                f"gpu_layers={_describe_gpu_rec(rec.n_gpu_layers)} "
                f"threads={int(rec.n_threads)} batch={int(rec.batch_size)} "
                f"kv={_ck or 'fp16'}"
            )
            try:
                from eli.cognition import gguf_inference as _ggi_live
                _live = _ggi_live.get_runtime_snapshot() or {}
                _eff = (_live.get("effective") or _live) if isinstance(_live, dict) else {}
                _lc, _lg = _eff.get("n_ctx"), _eff.get("n_gpu_layers")
                _loaded_plan = str(((_live.get("load_plan") or {}) if isinstance(_live, dict) else {}).get("text") or "")
                if not _live.get("loaded"):
                    pass                                          # settings are not a load
                elif _loaded_plan:
                    summary += f"  |  loaded: {_loaded_plan}"      # every card, experts in RAM, cache type
                elif _lc and _lg is not None:
                    summary += (f"  |  loaded: ctx={int(_lc)} gpu_layers={_describe_gpu_rec(_lg)}"
                                f" batch={int(_eff.get('n_batch') or 0)}")
            except Exception:
                log.debug("[GUI] live runtime snapshot unavailable for the tuning panel",
                          exc_info=True)
            self.system_recommendation_label.setText(summary)
            if dock is not None:
                dock.set_status("Tuning complete")
                dock.set_summary(summary)
            self.status_signal.emit(summary)

            # CANONICAL ctx = the user's chosen value, not the possibly VRAM-reduced rec.n_ctx
            # — writing the reduced value back would strand the user at a lower ctx next boot;
            # smart-fit already reduces to fit at load time.
            _canonical_ctx = int(_user_pinned_ctx) if _user_pinned_ctx else int(rec.n_ctx)
            # A pinned layer count is an absolute number scoped to the MODEL it was chosen
            # for, not portable: 7 layers was right for a 27B model on 8GB VRAM, but stranded
            # 25/32 layers on CPU for a 4GB model that fit whole — the pin carried no model
            # identity and silently followed the user across swaps. Same normalisation the
            # headless/server loader and the pin-matcher both use, so desktop/API/Windows
            # pins can't disagree about which layer count is live.
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
            if (_user_pinned_layers and _applies is None
                    and int(_user_pinned_layers) == int(rec.n_gpu_layers or 0)):
                # Same number either way: nothing to replace or announce. It printed
                # "gpu_layers=40 no longer applies - using the tuner's 40".
                _applies = int(_user_pinned_layers)
            if _user_pinned_layers and _applies is None:
                if _pin_model and _pin_model != _this_model:
                    _why = f"was pinned for {_pin_model}, not {_this_model}"
                elif _pin_ctx and _pin_ctx != _canonical_ctx:
                    _why = f"was measured at ctx={_pin_ctx}, not the current ctx={_canonical_ctx}"
                else:
                    _why = "no longer applies"
                self._hardware_tuning_log(
                    f"gpu_layers={_user_pinned_layers} {_why} - using "
                    f"the tuner's {_describe_gpu_rec(rec.n_gpu_layers)}, measured for this model at "
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
                self.n_ctx_input.setValue(int(_user_pinned_ctx or 0))
                self.n_gpu_layers_input.setValue(_canonical_layers)
                self.batch_size_input.setValue(_canonical_batch)
            except Exception as _spin_err:
                self._hardware_tuning_log(f"Warning: spinbox apply failed: {_spin_err}")

            # Persist the calculated values as the canonical runtime settings AND
            # the hw_profile_* mirror, so both the load path and any settings
            # readers see the recalculated, model-specific profile.
            try:
                from eli.core.runtime_settings import load_settings as _rs_load, save_settings as _rs_save
                _s = dict(_rs_load() or {})
                # the operator's own ctx, or auto; the tuner's figure goes in hw_profile_n_ctx only
                _s["n_ctx"] = int(_user_pinned_ctx or 0)
                _s["n_gpu_layers"] = _canonical_layers
                # Only a value the operator pinned counts as theirs; the tuner's own number must not
                # come back next launch as a pin.
                _s["n_gpu_layers_source"] = "user" if _user_pinned_layers else "tuner"
                # Record the model and ctx this pin was measured at, so a swap or ctx change can invalidate it.
                _s["n_gpu_layers_model"] = _this_model
                _s["n_gpu_layers_ctx"] = _canonical_ctx
                _s["batch_size"] = _canonical_batch   # user's choice, preserved
                # Threads stay the operator's: 0 (auto) is the count the tuner measures, so its
                # figure is not written as a number of theirs (the box and every loader read 0 as it).
                _s["hw_profile_n_ctx"] = int(rec.n_ctx)   # effective (VRAM-reduced) value
                _s["hw_profile_n_gpu_layers"] = int(rec.n_gpu_layers)
                _s["hw_profile_batch_size"] = int(rec.batch_size)
                _s["cache_type_k"] = _ck
                _s["cache_type_v"] = _cv
                _rs_save(_s)
                # Say what was applied and what was NOT. The previous line claimed
                # the whole profile had been applied as canonical, directly under a
                # printed "n_ctx set 10384 → 4096" — and n_ctx is the one value this
                # block deliberately does not take from the profile (see
                # _canonical_ctx above). Reading the panel, the tuner's ctx looked
                # like the live setting; it never was.
                _applied_desc = (f"threads={int(_s.get('n_threads') or 0) or 'auto'}"
                                 + (f" ({int(rec.n_threads)})" if not int(_s.get('n_threads') or 0)
                                    and int(rec.n_threads) > 0 else ""))
                _not_applied = []
                if int(rec.n_ctx) != int(_canonical_ctx):
                    _not_applied.append(f"n_ctx (yours: {_canonical_ctx}, tuner: {int(rec.n_ctx)})")
                if int(rec.n_gpu_layers) != int(_canonical_layers):
                    _not_applied.append(
                        f"n_gpu_layers (yours: {_describe_gpu_rec(_canonical_layers)}, "
                        f"tuner: {_describe_gpu_rec(rec.n_gpu_layers)})")
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

            try:
                from eli.core.hardware_profile import apply_recommendation as _hp_apply
                _hp_apply(rec)
            except Exception as _json_err:
                log.debug("[SETTINGS] runtime_hardware_profile.json sync failed", exc_info=True)

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

        # Persist the voice-engine choice BEFORE hardware tuning / GPU allocation
        # below, so a neural voice's VRAM is accounted for up front (and pinned to
        # GPU), or Piper keeps the VRAM for the model on a lean box.
        try:
            _tts_engine = dlg.selected_tts_engine()
            _tts_updates = {"tts_engine": _tts_engine}
            _tts_voice = dlg.selected_tts_voice()
            if _tts_voice:
                _tts_updates["tts_voice"] = _tts_voice
            from eli.core.runtime_settings import save_settings as _rs_save_tts
            _rs_save_tts(_tts_updates)
            if _tts_engine == "natural":
                os.environ["ELI_XTTS_DEVICE"] = "cuda"  # user chose it up-front → prefer GPU
                try:
                    from eli.perception.tts_router import set_active_voice as _sav
                    _sav(_tts_voice)   # the natural engine's default voice (dlg.selected_tts_voice)
                except Exception:
                    log.debug("[STARTUP] set natural voice active failed", exc_info=True)
        except Exception as _te:
            log.debug(f"[STARTUP] tts engine persist failed: {_te}", exc_info=True)

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

            # The dialog's Target batch is the request; the Settings-tab spinbox held a stale value
            # that the tuner treated as a pinned choice, so copy the dialog's value into it.
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
                        log.debug("suppressed exception", exc_info=True)
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
            log.debug("suppressed exception", exc_info=True)
        self.status_signal.emit("🔄 Loading model...")
        # Send stays open while the model loads: a command that needs no model runs at once and
        # anything else waits for the model (ConversationMixin._take_message). Set before the
        # thread starts, so a message typed the moment Load is pressed waits too.
        self._model_loading = True

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
                        self.status_signal.emit(self.model_manager.status_line())
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
                            log.debug("suppressed exception", exc_info=True)
                    try:
                        if provider != "ollama":
                            _requested_gpu = int(getattr(self.model_manager, "requested_n_gpu_layers", 0) or 0)
                            _effective_gpu = int(getattr(self.model_manager, "n_gpu_layers", 0) or 0)
                            _gpu_supported = getattr(self.model_manager, "gpu_offload_supported", None)
                            if _requested_gpu > 0 and (_gpu_supported is False or _effective_gpu <= 0):
                                from eli.core.hardware_profile import (
                                    detect_hardware as _gpu_hw,
                                    gpu_offload_unavailable_message as _gpu_msg,
                                )
                                _hw_now = _gpu_hw()
                                self.status_signal.emit(
                                    _gpu_msg(
                                        gpu_vendor=str(_hw_now.gpu_vendor or ""),
                                        gpu_integrated=bool(_hw_now.gpu_integrated),
                                        vulkan_available=bool(_hw_now.vulkan_available),
                                    )
                                )
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
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
                self.status_signal.emit("Take next message")   # what waited for the model goes now

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
            self._remember_model_folder(file_path)

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
            self._remember_model_folder(file_path)

    def _remember_model_folder(self, model_path: str):
        """A model chosen from a folder ELI did not know: the folder joins the model folders, so the
        models beside it are offered next time, and every list is filled again."""
        try:
            from eli.core.model_folders import remember_folder_of
            if remember_folder_of(model_path):
                self.refresh_model_choices()
        except (ImportError, OSError, ValueError) as ex:
            log.debug(f"[GUI] model folder not remembered: {ex}", exc_info=True)

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
            # 0 is auto; whether a saved number is still yours is decided per model when tuning
            self.n_ctx_input.setValue(int(s.get("n_ctx") or 0))
            self.n_threads_input.setValue(int(s.get("n_threads") or 0))
            self.n_gpu_layers_input.setValue(max(0, int(s.get("n_gpu_layers") or 0)))   # -1 (every layer) is auto
            self.max_tokens_input.setValue(int(s.get("max_tokens", 4096)))
            self.temperature_input.setValue(float(s.get("temperature", 0.7)))
            _batch = s.get("batch_size") or s.get("n_batch")
            if _batch:
                self.batch_size_input.setValue(int(_batch))
            # KV cache quantization combos
            try:
                _k = self._shown_cache_type(s.get("cache_type_k", ""))
                _v = self._shown_cache_type(s.get("cache_type_v", ""))
                _kidx = self.cache_type_k_combo.findText(_k)
                if _kidx >= 0:
                    self.cache_type_k_combo.setCurrentIndex(_kidx)
                _vidx = self.cache_type_v_combo.findText(_v)
                if _vidx >= 0:
                    self.cache_type_v_combo.setCurrentIndex(_vidx)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        except Exception as e:
            print(f"⚠️ Failed to apply numeric settings to widgets: {e}")

        # Provider + paths
        try:
            provider = s.get("provider", "bundled_gguf")
            idx = self.provider_combo.findData(provider)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            if provider == "custom_gguf":
                model_path = s.get("custom_model_path") or s.get("model_path") or DEFAULT_MODEL_PATH
            elif provider == "bundled_gguf":
                model_path = s.get("bundled_model_path") or s.get("model_path") or DEFAULT_MODEL_PATH
            else:
                model_path = s.get("model_path") or DEFAULT_MODEL_PATH
            self.model_path_input.setText(model_path or "")
            bundled_path = s.get("bundled_model_path", "")
            if bundled_path:
                self._pending_bundled_model_path = bundled_path
            self.ollama_host_input.setText(s.get("ollama_host", "http://localhost:11434"))
            self.ollama_model_combo.setEditText(s.get("ollama_model", ""))
        except Exception as e:
            print(f"⚠️ Failed to apply provider/path settings to widgets: {e}")
        # The Models page: vision pairs, the speech model, the chat model list
        try:
            self._vision_pick = (str(s.get("vision_model_path") or ""), str(s.get("vision_mmproj_path") or ""))
            self._vision_fast_pick = (str(s.get("vision_fast_model_path") or ""),
                                      str(s.get("vision_fast_mmproj_path") or ""))
            self._stt_model_saved = str(s.get("stt_model") or "")
            self._refresh_vision_combos()
            self._refresh_stt_models()
            self._refresh_custom_model_combo()
        except Exception as e:
            log.debug(f"[GUI] Models page settings not applied: {e}", exc_info=True)

        # Mic device selection
        try:
            _mic = s.get("mic_device")
            if _mic:
                self._saved_mic_device = tuple(_mic) if isinstance(_mic, list) else _mic
                self._on_mic_device_changed(_save=False)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

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
            log.debug("suppressed exception", exc_info=True)

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
            log.debug("suppressed exception", exc_info=True)

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
            log.debug("suppressed exception", exc_info=True)

        # GUI-local flags + theme
        try:
            self.auto_save_checkbox.setChecked(bool(s.get("auto_save", True)))
            from eli.core.runtime_settings import DEFAULTS as _settings_defaults
            self.log_to_file_checkbox.setChecked(bool(s.get("log_to_file", _settings_defaults.get("log_to_file"))))
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
            try:
                # A stored value that isn't a name ("listed", written by a since-fixed
                # misroute) is not shown as one.
                from eli.kernel.state import _clean_name as _cn_gui
                _gui_user_name = _cn_gui(_gui_user_name)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            if not _gui_user_name:
                try:
                    from eli.kernel.state import get_user_name as _gun
                    _gui_user_name = _gun() or ""
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            self.user_name_input.setText(_gui_user_name)
            if hasattr(self, "communication_style_combo"):
                _cs = str(s.get("communication_style", "") or "")
                try:
                    from eli.kernel.state import get_communication_style as _gcs
                    _cs = _gcs() or _cs
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
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
        if hasattr(self, "_sc_load_settings"):
            try:
                self._sc_load_settings()
            except Exception:
                log.debug("screen settings load failed", exc_info=True)

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
            # Store the canonical active model path for the selected provider.
            # Without this, custom_model_path / bundled_model_path can diverge
            # from model_path and get_model_path() keeps the stale primary key.
            "model_path": model_path,
            "custom_model_path": (
                self.model_path_input.text().strip()
                if provider == "custom_gguf"
                else str(existing.get("custom_model_path") or self.model_path_input.text() or "")
            ),
            "bundled_model_path": bundled_path or str(existing.get("bundled_model_path") or ""),
            # Store the canonical form, so "localhost:11434" or a bare IP typed
            # here is persisted as a URL every consumer can actually use.
            "ollama_host": self.ollama_manager._normalize_host(
                self.ollama_host_input.text().strip()),
            "ollama_model": self.ollama_model_combo.currentText().strip(),
            "n_ctx": int(self.n_ctx_input.value()),
            "n_ctx_model": (Path(str(self.resolve_selected_model_path() or "")).name
                            if int(self.n_ctx_input.value()) > 0 else ""),
            "n_threads": int(self.n_threads_input.value()),
            "n_gpu_layers": int(self.n_gpu_layers_input.value()),
            "n_gpu_layers_source": (
                "user" if int(self.n_gpu_layers_input.value()) != int(existing.get("n_gpu_layers") or 0)
                else str(existing.get("n_gpu_layers_source") or "")),
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
            "vision_model_path": str(getattr(self, "_vision_pick", ("", ""))[0] or ""),
            "vision_mmproj_path": str(getattr(self, "_vision_pick", ("", ""))[1] or ""),
            "vision_fast_model_path": str(getattr(self, "_vision_fast_pick", ("", ""))[0] or ""),
            "vision_fast_mmproj_path": str(getattr(self, "_vision_fast_pick", ("", ""))[1] or ""),
            "stt_model": str(self.stt_model_combo.currentData() or "") if hasattr(self, "stt_model_combo") else "",
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
        if hasattr(self, "_sc_depth_combo"):
            updates.update({
                "screen_analysis_depth": self._sc_depth_combo.currentData() or "standard",
                "screen_analysis_use_memory": bool(self._sc_use_memory_cb.isChecked()),
                "screen_analysis_use_research": bool(self._sc_use_research_cb.isChecked()),
                "ui_ground_backend": self._sc_ground_combo.currentData() or "none",
                "computer_use_backend": self._sc_agent_combo.currentData() or "none",
                "ambient_vision_enabled": bool(self._sc_ambient_cb.isChecked()),
                "ambient_vision_interval": int(self._sc_interval_spin.value()),
            })

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
                            model_name = (
                                Path(str(params.get("model_path") or "")).name
                                or str(params.get("model_name") or "")
                                or "model"
                            )
                            ctx = params.get("n_ctx")
                            gpu = params.get("n_gpu_layers")
                            batch = params.get("n_batch")
                            log.debug(
                                "[SETTINGS] Model reloaded — "
                                f"model={model_name} "
                                f"ctx={ctx} "
                                f"gpu_layers={gpu} "
                                f"threads={params.get('n_threads')} "
                                f"batch={batch}"
                            )
                            # Refresh the min-chat model strip (same contract as
                            # the initial load_model status path).
                            try:
                                self.status_signal.emit(
                                    f"🟢 Model ready: {model_name} "
                                    f"(ctx={ctx} gpu={gpu} batch={batch})"
                                )
                            except Exception:
                                log.debug("[SETTINGS] model label emit failed", exc_info=True)
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
