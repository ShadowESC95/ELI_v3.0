"""TabsMixin — 21 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403
from eli.gui.main_window.widgets import _CapabilityList, _QABoard, _ZoomableImagePreview, _ZoomableSettingsView


class TabsMixin:
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setUsesScrollButtons(True)
        main_layout.addWidget(self.tabs)
        self.create_chat_tab()
        self.create_proactive_tab()
        self.create_image_tab()
        self.create_quick_actions_tab()
        self.create_screen_control_tab()
        self.create_files_tab()
        self.create_labs_tab()
        self.create_coding_tab()
        self.create_tasks_tab()
        self.create_report_builder_tab()
        # Orchestration + Test & Review are developer/diagnostic tools — they live as
        # sub-tabs under Labs (LabsTab), not as prominent top-level tabs.
        # The Experimental tab is the read-only workbench over `experimental/`: it
        # inventories prototype kits and opens folders, never runs them. (An earlier
        # tab of the same name carried gaze control; that moved to Settings ▸ Gaze.)
        self.create_experimental_tab()
        self.create_eli_world_tab()
        self.create_audit_tab()
        # Extensions sits immediately left of Settings because that is where it came
        # from: plugins, MCP servers and the marketplace were sub-tabs of the modal
        # Advanced Settings dialog, three clicks deep in something most people never
        # open. Installing someone else's code is a first-class act, not a preference.
        self.create_extensions_tab()
        self.create_settings_tab()
        self.create_top_toolbar()
        self.status_bar = self.statusBar()
        self.status_label = QLabel("🔴 Model not loaded")
        self.status_bar.addWidget(self.status_label)
        # Confidence / grounding metadata badge — updated after each response
        self._confidence_meta_label = QLabel("")
        self._confidence_meta_label.setStyleSheet(
            "color:#7a9cbf;font-size:10px;padding:0 6px;"
        )
        self.status_bar.addWidget(self._confidence_meta_label)
        # Proactive daemon status indicator (right side of status bar)
        self._proactive_status_label = QLabel()
        self.status_bar.addPermanentWidget(self._proactive_status_label)
        self._update_proactive_status_label()
        self.create_menu_bar()

    def create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        new_conv = QAction("New Conversation", self)
        new_conv.setShortcut("Ctrl+N")
        new_conv.triggered.connect(self.new_conversation)
        file_menu.addAction(new_conv)
        save_conv = QAction("Save Conversation", self)
        save_conv.setShortcut("Ctrl+S")
        save_conv.triggered.connect(self.save_conversation)
        file_menu.addAction(save_conv)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        model_menu = menubar.addMenu("&Model")
        load_model = QAction("Load Model...", self)
        load_model.triggered.connect(self.load_model_dialog)
        model_menu.addAction(load_model)
        startup_picker = QAction("Startup Model Picker…", self)
        startup_picker.triggered.connect(self.prompt_load_model)
        model_menu.addAction(startup_picker)
        unload_model = QAction("Unload Model", self)
        unload_model.triggered.connect(self.unload_model)
        model_menu.addAction(unload_model)
        view_menu = menubar.addMenu("&View")
        open_settings = QAction("Open Settings", self)
        open_settings.setShortcut("Ctrl+,")
        open_settings.triggered.connect(self.open_settings_tab)
        view_menu.addAction(open_settings)
        open_images = QAction("Open Image Studio", self)
        open_images.setShortcut("Ctrl+Shift+I")
        open_images.triggered.connect(self.open_image_studio_tab)
        view_menu.addAction(open_images)
        view_menu.addSeparator()
        zoom_in = QAction("Zoom In", self)
        zoom_in.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        zoom_in.triggered.connect(lambda: self.adjust_active_zoom(1))
        view_menu.addAction(zoom_in)
        zoom_out = QAction("Zoom Out", self)
        zoom_out.setShortcuts([QKeySequence("Ctrl+-"), QKeySequence("Ctrl+_")])
        zoom_out.triggered.connect(lambda: self.adjust_active_zoom(-1))
        view_menu.addAction(zoom_out)
        zoom_reset = QAction("Reset Zoom", self)
        zoom_reset.setShortcut("Ctrl+0")
        zoom_reset.triggered.connect(self.reset_active_zoom)
        view_menu.addAction(zoom_reset)
        view_menu.addSeparator()
        toggle_theme = QAction("Toggle Theme", self)
        toggle_theme.setShortcut("Ctrl+T")
        toggle_theme.triggered.connect(self.toggle_theme)
        view_menu.addAction(toggle_theme)
        # Proactive dock toggle — the dock is created hidden; expose it
        # explicitly here so users can find it.
        view_menu.addSeparator()
        toggle_proactive = QAction("Proactive Dock", self)
        toggle_proactive.setCheckable(True)
        toggle_proactive.setShortcut("Ctrl+Shift+P")
        toggle_proactive.triggered.connect(self._toggle_proactive_dock)
        self._toggle_proactive_action = toggle_proactive
        view_menu.addAction(toggle_proactive)
        toggle_hw = QAction("Hardware Tuning Dock", self)
        toggle_hw.setCheckable(True)
        toggle_hw.setShortcut("Ctrl+Shift+H")
        toggle_hw.triggered.connect(self._toggle_hardware_tuning_dock)
        self._toggle_hardware_dock_action = toggle_hw
        view_menu.addAction(toggle_hw)
        toggle_op = QAction("Operator Console", self)
        toggle_op.setCheckable(True)
        toggle_op.setShortcut("Ctrl+Shift+O")
        toggle_op.triggered.connect(self._toggle_operator_console_dock)
        self._toggle_operator_console_action = toggle_op
        view_menu.addAction(toggle_op)
        self._add_bundles_menu(menubar)
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _add_bundles_menu(self, menubar):
        """Separate ELI applications (ELI Legal, ELI VR, …) beside ELI v3, each opened in a window of
        its own. Shown whenever one exists, installed or not: hidden until something had registered,
        the menu gave no way in to an ELI VR sitting on the desktop."""
        self._bundles_menu = menubar.addMenu("&Bundles")
        self._fill_bundles_menu()

    def _fill_bundles_menu(self):
        """Every official bundle with what it takes here: open it, enter its key, get it, or wait."""
        from eli.bundles import offers

        menu = self._bundles_menu
        menu.clear()
        try:
            listed = offers()
        except Exception:
            log.warning("the list of bundles could not be read", exc_info=True)
            listed = []
        for offer in listed:
            if offer.state == "ready":
                action = QAction(f"Open {offer.label} in its own window", self)
                action.triggered.connect(lambda _checked=False, b=offer.id: self._open_bundle(b))
            elif offer.state == "needs_key":
                action = QAction(f"{offer.label}: enter a licence key…", self)
                action.triggered.connect(lambda _checked=False, b=offer.id: self._open_bundle(b))
            elif offer.state == "not_installed":
                action = QAction(f"Get {offer.label}…", self)
                action.triggered.connect(lambda _checked=False, o=offer: self._get_bundle(o))
            else:
                action = QAction(f"{offer.label}: coming", self)
                action.setEnabled(False)
            action.setToolTip(offer.detail or (offer.bundle.summary if offer.bundle else ""))
            menu.addAction(action)
        menu.menuAction().setVisible(bool(menu.actions()))
        if any(o.state == "ready" for o in listed):
            menu.addSeparator()
            ask = QAction("Ask at start-up which to open", self)
            ask.triggered.connect(self._ask_bundle_at_startup)
            menu.addAction(ask)

    def _get_bundle(self, offer):
        """A bundle not installed here: where to download it, or point to a copy already downloaded."""
        from eli.bundles import bundle_apps, register_program

        box = QMessageBox(self)
        box.setWindowTitle(offer.label)
        box.setText(f"{offer.label} is not installed on this machine.")
        find = box.addButton("Find a downloaded copy…", QMessageBox.ButtonRole.AcceptRole)
        page = box.addButton("Open the download page", QMessageBox.ButtonRole.ActionRole) if offer.releases else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if page is not None and box.clickedButton() is page:
            QDesktopServices.openUrl(QUrl(offer.releases))
            return
        if box.clickedButton() is not find:
            return
        names = " ".join((bundle_apps().get(offer.id) or {}).get("files") or [])
        picked, _ = QFileDialog.getOpenFileName(
            self, f"Find {offer.label}", str(Path.home()), f"{offer.label} ({names});;All files (*)")
        if not picked:
            return
        register_program(offer.id, Path(picked))
        self._fill_bundles_menu()
        self._open_bundle(offer.id)

    def _open_bundle(self, bundle_id):
        try:
            from eli.bundles import spawn

            try:
                spawn(bundle_id)
            except PermissionError as refused:
                # An official bundle is a separate product: offer to take its key here,
                # since a customer on Windows or macOS has no terminal for --activate-key.
                if self._activate_bundle_key(bundle_id, str(refused)):
                    spawn(bundle_id)
        except Exception as exc:
            QMessageBox.warning(self, "Bundle", f"Could not open {bundle_id}: {exc}")

    def _activate_bundle_key(self, bundle_id, reason):
        from eli.bundles import discover
        from eli.gui.license_dialog import prompt_for_product_key
        from eli.licensing.activation import activate_license_key

        bundle = discover().get(bundle_id)
        label = bundle.title if bundle is not None else bundle_id
        key = prompt_for_product_key(label=label, reason=reason)
        if not key:
            return False
        activate_license_key(key)
        return True

    def _ask_bundle_at_startup(self):
        try:
            from eli.core.runtime_settings import save_settings

            save_settings({"startup_target": "ask"})
            QMessageBox.information(self, "Bundles", "ELI will ask which to open the next time it starts.")
        except Exception as exc:
            QMessageBox.warning(self, "Bundles", str(exc))

    def create_top_toolbar(self):
        toolbar = QToolBar("View")
        toolbar.setObjectName("workspaceToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        detect_action = QAction("Auto Detect", self)
        detect_action.triggered.connect(self.detect_optimal_settings)
        toolbar.addAction(detect_action)

        theme_action = QAction("Theme", self)
        theme_action.triggered.connect(self.toggle_theme)
        toolbar.addAction(theme_action)

        # Live Ollama model switcher — pick a model your local Ollama serves and ELI
        # switches to it immediately. No GPU/hardware tuning runs (that's GGUF-only;
        # Ollama manages its own VRAM), and it queries your configured ollama_host so
        # a non-default host/port works. Degrades cleanly if the widget can't load.
        try:
            from eli.gui.widgets.ollama_model_selector import OllamaModelSelector
            self.ollama_selector = OllamaModelSelector(self)
            self.ollama_selector.model_changed.connect(self._on_toolbar_ollama_model_changed)
            toolbar.addWidget(self.ollama_selector)
        except Exception:
            log.debug("Ollama toolbar selector unavailable", exc_info=True)

    def _on_toolbar_ollama_model_changed(self, model: str):
        """Toolbar Ollama picker → switch the provider to Ollama and load the chosen
        model. Deliberately runs NO GPU/hardware tuning (that is GGUF-only; Ollama
        sizes its own VRAM) — load_model()'s dispatcher routes provider='ollama'
        straight to the Ollama backend. Only fires on a real user selection (the
        widget blocks signals while it auto-refreshes its list)."""
        model = (model or "").strip()
        if not model:
            return
        try:
            idx = self.provider_combo.findData("ollama")
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)   # enables the Ollama controls
            self.ollama_model_combo.setEditText(model)
            self.save_settings(silent=True)
            self.load_model()
        except Exception:
            log.debug("toolbar Ollama switch failed", exc_info=True)

    def _build_habits_panel(self) -> QWidget:
        """Habits management panel used inside the proactive workspace."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QLabel("⏰ Habits Scheduler")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        layout.addWidget(header)

        # Toolbar
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_habit_list)
        toolbar.addWidget(refresh_btn)

        add_btn = QPushButton("➕ Add Habit")
        add_btn.clicked.connect(self.add_habit_rule)
        toolbar.addWidget(add_btn)

        edit_btn = QPushButton("✏️ Edit Selected")
        edit_btn.clicked.connect(self.edit_habit_rule)
        toolbar.addWidget(edit_btn)

        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(self.delete_habit_rule)
        toolbar.addWidget(delete_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Explain the approval model: ELI proposes habits switched OFF; the user
        # approves one by ticking Enabled. Manual adds are on immediately.
        hint = QLabel("💡 Habits ELI suggests start OFF (Status: Suggested). Tick "
                      "“Enabled” to approve & activate one. Use Add/Edit to manage "
                      "them yourself.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#88909c;font-size:10px;padding:2px 6px;")
        layout.addWidget(hint)

        # Table of habits
        self.habit_table = QTableWidget()
        self.habit_table.setColumnCount(7)
        self.habit_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Command", "Time", "Days", "Status", "Enabled"])
        self.habit_table.horizontalHeader().setStretchLastSection(True)
        # Set selection behavior (Qt version safe)
        select_rows = getattr(
            getattr(QAbstractItemView, "SelectionBehavior", QAbstractItemView),
            "SelectRows",
        )
        self.habit_table.setSelectionBehavior(select_rows)
        self.habit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.habit_table)

        # Info label
        self.habit_info = QLabel("")
        layout.addWidget(self.habit_info)

        self.refresh_habit_list()
        return widget

    def create_habits_tab(self):
        widget = self._build_habits_panel()
        self.tabs.addTab(widget, "⏰ Habits")

    def create_image_tab(self):
        self.image_tab_widget = QWidget()
        root_layout = QVBoxLayout(self.image_tab_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        workspace = QWidget()
        workspace.setObjectName("imageStudioWorkspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header_row = QHBoxLayout()

        header = QLabel("🖼️ Image Studio")
        header.setStyleSheet("font-size:18px; font-weight:bold; padding:10px;")
        header_row.addWidget(header)
        header_row.addStretch()

        workspace_zoom_out = QPushButton("A-")
        workspace_zoom_out.setToolTip("Zoom out the full Images workspace (Ctrl+-)")
        workspace_zoom_out.clicked.connect(lambda: self._image_zoom_view.zoom_out())
        header_row.addWidget(workspace_zoom_out)

        workspace_zoom_reset = QPushButton("100%")
        workspace_zoom_reset.setToolTip("Reset Images workspace zoom (Ctrl+0)")
        workspace_zoom_reset.clicked.connect(lambda: self._image_zoom_view.zoom_reset())
        header_row.addWidget(workspace_zoom_reset)

        workspace_zoom_in = QPushButton("A+")
        workspace_zoom_in.setToolTip("Zoom in the full Images workspace (Ctrl+=)")
        workspace_zoom_in.clicked.connect(lambda: self._image_zoom_view.zoom_in())
        header_row.addWidget(workspace_zoom_in)
        layout.addLayout(header_row)

        sub = QLabel(
            "Generate local images with either the lightweight procedural engine or a real diffusion model. Photoreal and 3D-style results require a local diffusion checkpoint, ideally under models/image/. Use Ctrl+scroll or Ctrl+plus/minus to zoom this whole workspace."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#7d8ba2;padding:0 10px 10px 10px;")
        layout.addWidget(sub)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        prompt_group = QGroupBox("Prompt")
        prompt_layout = QVBoxLayout(prompt_group)
        self.image_prompt_input = QTextEdit()
        self.image_prompt_input.setPlaceholderText("Describe the image you want ELI to generate...")
        self.image_prompt_input.setMinimumHeight(120)
        prompt_layout.addWidget(self.image_prompt_input)

        prompt_btn_row = QHBoxLayout()
        recent_btn = QPushButton("Use Recent Context")
        recent_btn.clicked.connect(self.use_recent_context_for_image_prompt)
        prompt_btn_row.addWidget(recent_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.image_prompt_input.clear)
        prompt_btn_row.addWidget(clear_btn)
        prompt_btn_row.addStretch()
        prompt_layout.addLayout(prompt_btn_row)
        left_layout.addWidget(prompt_group)

        options_group = QGroupBox("Generation Options")
        options_layout = QFormLayout(options_group)
        self.image_preset_combo = QComboBox()
        options_layout.addRow("Preset", self.image_preset_combo)

        self.image_project_input = QLineEdit()
        self.image_project_input.setPlaceholderText("Optional folder with image/text references")
        project_row = QHBoxLayout()
        project_row.addWidget(self.image_project_input)
        project_btn = QPushButton("Browse…")
        project_btn.clicked.connect(self.browse_image_project_folder)
        project_row.addWidget(project_btn)
        options_layout.addRow("Project", project_row)

        self.image_scene_combo = QComboBox()
        self.image_scene_combo.addItems(["auto", "portrait", "landscape", "poster", "abstract", "emblem", "product", "cityscape", "space"])
        options_layout.addRow("Scene", self.image_scene_combo)

        self.image_style_combo = QComboBox()
        self.image_style_combo.addItems(["auto", "balanced", "cinematic", "minimal", "luxury", "neon", "fantasy"])
        options_layout.addRow("Style", self.image_style_combo)

        self.image_palette_combo = QComboBox()
        self.image_palette_combo.addItems(["auto", "blue_dawn", "crimson_sunset", "emerald_aurora", "golden_storm", "monochrome_luxury", "neon_noir", "rose_steel", "solar_glass", "violet_dusk"])
        options_layout.addRow("Palette", self.image_palette_combo)

        self.image_backend_combo = QComboBox()
        self.image_backend_combo.addItems(["auto", "diffusion", "procedural"])
        options_layout.addRow("Backend", self.image_backend_combo)

        model_row = QHBoxLayout()
        self.image_model_combo = QComboBox()
        self.image_model_combo.setEditable(True)
        self.image_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.image_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.image_model_combo.setMinimumContentsLength(28)
        model_row.addWidget(self.image_model_combo, stretch=1)
        refresh_models_btn = QPushButton("Refresh")
        refresh_models_btn.clicked.connect(self.refresh_image_model_sources)
        model_row.addWidget(refresh_models_btn)
        browse_model_btn = QPushButton("Path…")
        browse_model_btn.clicked.connect(self.browse_image_model_path)
        model_row.addWidget(browse_model_btn)
        options_layout.addRow("Model", model_row)

        self.image_device_combo = QComboBox()
        self.image_device_combo.addItems(["auto", "cuda", "cpu"])
        options_layout.addRow("Device", self.image_device_combo)

        self.image_quality_preset_combo = QComboBox()
        self.image_quality_preset_combo.addItems(["draft", "balanced", "ultra", "extreme"])
        self.image_quality_preset_combo.currentTextChanged.connect(self._apply_image_quality_preset)
        options_layout.addRow("Quality", self.image_quality_preset_combo)

        render_row = QHBoxLayout()
        self.image_steps_input = QSpinBox()
        self.image_steps_input.setRange(8, 120)
        self.image_steps_input.setValue(36)
        self.image_guidance_input = QDoubleSpinBox()
        self.image_guidance_input.setRange(1.0, 20.0)
        self.image_guidance_input.setSingleStep(0.1)
        self.image_guidance_input.setValue(7.2)
        render_row.addWidget(QLabel("Steps"))
        render_row.addWidget(self.image_steps_input)
        render_row.addSpacing(8)
        render_row.addWidget(QLabel("CFG"))
        render_row.addWidget(self.image_guidance_input)
        options_layout.addRow("Render", render_row)

        self.image_negative_input = QTextEdit()
        self.image_negative_input.setMaximumHeight(72)
        self.image_negative_input.setPlaceholderText("Optional negative prompt: cartoon, flat, blurry, low quality, malformed anatomy...")
        options_layout.addRow("Negative", self.image_negative_input)

        self.image_backend_note = QLabel("No local diffusion checkpoint detected yet. Drop one into models/image/ or paste a full local path to unlock photoreal rendering.")
        self.image_backend_note.setWordWrap(True)
        self.image_backend_note.setStyleSheet("color:#7d8ba2;font-size:11px;")
        options_layout.addRow("", self.image_backend_note)

        size_row = QHBoxLayout()
        self.image_width_input = QSpinBox()
        self.image_width_input.setRange(256, 4096)
        self.image_width_input.setSingleStep(64)
        self.image_width_input.setValue(1400)
        self.image_height_input = QSpinBox()
        self.image_height_input.setRange(256, 4096)
        self.image_height_input.setSingleStep(64)
        self.image_height_input.setValue(900)
        size_row.addWidget(self.image_width_input)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.image_height_input)
        options_layout.addRow("Size", size_row)

        batch_row = QHBoxLayout()
        self.image_count_input = QSpinBox()
        self.image_count_input.setRange(1, 24)
        self.image_count_input.setValue(1)
        self.image_seed_input = QSpinBox()
        self.image_seed_input.setRange(1, 999999)
        self.image_seed_input.setValue(77)
        # Lock OFF by default → each generation uses a fresh random seed, so
        # repeated prompts don't produce the identical image (user-reported, 2026-06-06:
        # "just getting repeated images"). Tick to pin the seed and reproduce one.
        self.image_seed_lock = QCheckBox("🔒 Lock seed")
        self.image_seed_lock.setChecked(False)
        self.image_seed_lock.setToolTip("Off = a new random seed each time (varied images).\n"
                                        "On = reuse the seed below to reproduce the same image.")
        batch_row.addWidget(self.image_count_input)
        batch_row.addWidget(QLabel("Seed"))
        batch_row.addWidget(self.image_seed_input)
        batch_row.addWidget(self.image_seed_lock)
        options_layout.addRow("Batch", batch_row)

        self.image_personalize_checkbox = QCheckBox("Personalise with saved user/image identity")
        self.image_personalize_checkbox.setChecked(True)
        options_layout.addRow("", self.image_personalize_checkbox)

        self.image_chat_context_checkbox = QCheckBox("Blend recent chat context into the prompt")
        self.image_chat_context_checkbox.setChecked(True)
        options_layout.addRow("", self.image_chat_context_checkbox)

        self.image_proactive_context_checkbox = QCheckBox("Blend proactive patterns/habits into the prompt")
        self.image_proactive_context_checkbox.setChecked(True)
        options_layout.addRow("", self.image_proactive_context_checkbox)

        self.image_prompt_context_display = QTextEdit()
        self.image_prompt_context_display.setReadOnly(True)
        self.image_prompt_context_display.setMaximumHeight(130)
        self.image_prompt_context_display.setPlaceholderText("Applied prompt context and personalisation notes will appear here.")
        options_layout.addRow("Context", self.image_prompt_context_display)

        left_layout.addWidget(options_group)

        generate_row = QHBoxLayout()
        self.image_generate_btn = QPushButton("Generate Images")
        self.image_generate_btn.setMinimumHeight(40)
        self.image_generate_btn.clicked.connect(self.generate_images_from_studio)
        generate_row.addWidget(self.image_generate_btn)
        self.image_open_output_btn = QPushButton("Open Output Folder")
        self.image_open_output_btn.clicked.connect(self.open_image_output_folder)
        generate_row.addWidget(self.image_open_output_btn)
        left_layout.addLayout(generate_row)
        left_layout.addStretch()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.image_preview = _ZoomableImagePreview()

        preview_head = QHBoxLayout()
        preview_title = QLabel("Preview")
        preview_title.setStyleSheet("font-weight:bold; font-size:14px;")
        preview_head.addWidget(preview_title)
        preview_head.addStretch()
        zoom_out = QPushButton("−")
        zoom_out.clicked.connect(lambda: self.image_preview.zoom_out())
        preview_head.addWidget(zoom_out)
        zoom_reset = QPushButton("100%")
        zoom_reset.clicked.connect(self.image_preview.zoom_reset)
        preview_head.addWidget(zoom_reset)
        zoom_in = QPushButton("+")
        zoom_in.clicked.connect(lambda: self.image_preview.zoom_in())
        preview_head.addWidget(zoom_in)
        right_layout.addLayout(preview_head)

        right_layout.addWidget(self.image_preview, stretch=5)

        self.image_results_list = QListWidget()
        self.image_results_list.itemSelectionChanged.connect(self._show_selected_image_result)
        self.image_results_list.itemDoubleClicked.connect(lambda _item: self.open_selected_image_output())
        right_layout.addWidget(self.image_results_list, stretch=2)

        self.image_summary_display = QTextEdit()
        self.image_summary_display.setReadOnly(True)
        self.image_summary_display.setMaximumHeight(170)
        self.image_summary_display.setPlaceholderText("Generation summary, applied prompt, and output paths will appear here.")
        right_layout.addWidget(self.image_summary_display)

        # ── ELI Image Chat ──────────────────────────────────────────────────
        img_chat_group = QGroupBox("Ask ELI About This Image")
        img_chat_layout = QVBoxLayout(img_chat_group)
        img_chat_layout.setSpacing(4)

        # Label showing which image ELI will talk about
        self.image_eli_context_label = QLabel("No image selected — generate or click one in the list above.")
        self.image_eli_context_label.setStyleSheet(
            "color: #88aacc; font-size: 9px; font-style: italic; padding: 0 2px;"
        )
        self.image_eli_context_label.setWordWrap(True)
        img_chat_layout.addWidget(self.image_eli_context_label)

        self.image_eli_response = QTextEdit()
        self.image_eli_response.setReadOnly(True)
        self.image_eli_response.setMaximumHeight(100)
        self.image_eli_response.setPlaceholderText("ELI's answer will appear here…")
        img_chat_layout.addWidget(self.image_eli_response)

        img_chat_row = QHBoxLayout()
        self.image_eli_input = QLineEdit()
        self.image_eli_input.setPlaceholderText("Describe it, critique it, suggest edits…")
        self.image_eli_input.returnPressed.connect(self._ask_eli_about_image)
        img_chat_row.addWidget(self.image_eli_input, stretch=1)
        ask_img_btn = QPushButton("Ask ELI")
        ask_img_btn.setFixedWidth(90)
        ask_img_btn.clicked.connect(self._ask_eli_about_image)
        img_chat_row.addWidget(ask_img_btn)
        img_chat_layout.addLayout(img_chat_row)

        right_layout.addWidget(img_chat_group)
        self._current_image_path = None
        # ── end ELI Image Chat ──────────────────────────────────────────────

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([520, 760])
        layout.addWidget(splitter)

        self._image_zoom_view = _ZoomableSettingsView(workspace)
        root_layout.addWidget(self._image_zoom_view)
        self.tabs.addTab(self.image_tab_widget, "🖼️ Images")
        self.refresh_image_presets()
        self.refresh_image_model_sources()
        self.refresh_image_gallery()

    def _build_self_improve_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        header = QLabel('🔧 Self-Improvement Engine')
        header.setStyleSheet('font-size:18px; font-weight:bold; padding:10px;')
        layout.addWidget(header)
        fail_group = QGroupBox('Failure Analysis')
        fail_layout = QVBoxLayout(fail_group)
        self.failures_display = QTextEdit()
        self.failures_display.setReadOnly(True)
        self.failures_display.setMaximumHeight(200)
        fail_layout.addWidget(self.failures_display)
        analyze_btn = QPushButton('🔍 Analyze Failures')
        analyze_btn.clicked.connect(self._si_analyze_failures)
        fail_layout.addWidget(analyze_btn)
        layout.addWidget(fail_group)
        imp_group = QGroupBox('Improvement Log')
        imp_layout = QVBoxLayout(imp_group)
        self.improvements_display = QTextEdit()
        self.improvements_display.setReadOnly(True)
        imp_layout.addWidget(self.improvements_display)
        run_btn = QPushButton('⚡ Run Improvement Cycle')
        run_btn.clicked.connect(self._si_run_cycle)
        imp_layout.addWidget(run_btn)
        layout.addWidget(imp_group)
        return widget

    def create_self_improve_tab(self):
        widget = self._build_self_improve_panel()
        self.tabs.addTab(widget, '🔧 Self-Improve')

    def _build_memory_panel(self) -> QWidget:
        memory_widget = QWidget()
        layout = QVBoxLayout(memory_widget)
        stats_group = QGroupBox("Memory Statistics")
        stats_layout = QHBoxLayout(stats_group)
        self.memory_stats_label = QLabel("Loading...")
        stats_layout.addWidget(self.memory_stats_label)
        refresh_stats_btn = QPushButton("Refresh Stats")
        refresh_stats_btn.clicked.connect(self.refresh_memory_stats)
        stats_layout.addWidget(refresh_stats_btn)
        layout.addWidget(stats_group)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        search_group = QGroupBox("Search Memory")
        search_layout = QVBoxLayout(search_group)
        self.memory_search_input = QLineEdit()
        self.memory_search_input.setPlaceholderText("Enter search query...")
        self.memory_search_input.returnPressed.connect(self.search_memory)
        search_layout.addWidget(self.memory_search_input)
        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self.search_memory)
        search_layout.addWidget(search_btn)
        left_layout.addWidget(search_group)
        store_group = QGroupBox("Store New Memory")
        store_layout = QVBoxLayout(store_group)
        self.memory_store_input = QTextEdit()
        self.memory_store_input.setPlaceholderText("Enter memory to store...")
        self.memory_store_input.setMaximumHeight(100)
        store_layout.addWidget(self.memory_store_input)
        self.memory_tags_input = QLineEdit()
        self.memory_tags_input.setPlaceholderText("Tags (comma-separated, optional)")
        store_layout.addWidget(self.memory_tags_input)
        store_btn = QPushButton("💾 Store Memory")
        store_btn.clicked.connect(self.store_memory)
        store_layout.addWidget(store_btn)
        left_layout.addWidget(store_group)
        left_layout.addStretch()
        splitter.addWidget(left_widget)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        results_label = QLabel("Memory Results")
        results_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(results_label)
        self.memory_results_display = QTextEdit()
        self.memory_results_display.setReadOnly(True)
        self.memory_results_display.setPlaceholderText("Search results will appear here...")
        right_layout.addWidget(self.memory_results_display)
        recent_btn = QPushButton("📋 Show Recent Memories")
        recent_btn.clicked.connect(self.show_recent_memories)
        right_layout.addWidget(recent_btn)
        splitter.addWidget(right_widget)
        splitter.setSizes([400, 600])
        layout.addWidget(splitter)
        QTimer.singleShot(500, self.refresh_memory_stats)
        return memory_widget

    def create_proactive_tab(self):
        proactive_widget = QWidget()
        layout = QVBoxLayout(proactive_widget)

        # Header row with title and pause/resume button
        hdr_row = QHBoxLayout()
        header = QLabel("🎯 Proactive Insights")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        hdr_row.addWidget(header)
        hdr_row.addStretch()
        self._proactive_toggle_btn = QPushButton("⏸ Pause Proactive")
        self._proactive_toggle_btn.setStyleSheet(
            "background-color: #2a6030; color: #e8ffe8; border-radius: 4px; padding: 4px 12px;"
        )
        self._proactive_toggle_btn.setToolTip("Pause or resume the background proactive daemon")
        self._proactive_toggle_btn.clicked.connect(self._toggle_proactive_daemon)
        hdr_row.addWidget(self._proactive_toggle_btn)
        layout.addLayout(hdr_row)

        proactive_tabs = QTabWidget()
        suggestions_widget = QWidget()
        suggestions_layout = QVBoxLayout(suggestions_widget)
        self.suggestions_display = QTextEdit()
        self.suggestions_display.setReadOnly(True)
        self.suggestions_display.setPlaceholderText("AI-generated suggestions will appear here...")
        suggestions_layout.addWidget(self.suggestions_display)
        generate_suggestions_btn = QPushButton("🔮 Generate Suggestions")
        generate_suggestions_btn.clicked.connect(self.generate_suggestions)
        suggestions_layout.addWidget(generate_suggestions_btn)
        proactive_tabs.addTab(suggestions_widget, "💡 Suggestions")
        summaries_widget = QWidget()
        summaries_layout = QVBoxLayout(summaries_widget)
        self.summaries_display = QTextEdit()
        self.summaries_display.setReadOnly(True)
        self.summaries_display.setPlaceholderText("Conversation summaries will appear here...")
        summaries_layout.addWidget(self.summaries_display)
        generate_summary_btn = QPushButton("📝 Summarize Conversation")
        generate_summary_btn.clicked.connect(self.generate_summary)
        summaries_layout.addWidget(generate_summary_btn)
        proactive_tabs.addTab(summaries_widget, "📝 Summaries")
        insights_widget = QWidget()
        insights_layout = QVBoxLayout(insights_widget)
        self.insights_display = QTextEdit()
        self.insights_display.setReadOnly(True)
        self.insights_display.setPlaceholderText("Contextual insights will appear here...")
        insights_layout.addWidget(self.insights_display)
        analyze_btn = QPushButton("🔬 Analyze Context")
        analyze_btn.clicked.connect(self.analyze_context)
        insights_layout.addWidget(analyze_btn)
        proactive_tabs.addTab(insights_widget, "🔬 Insights")
        proactive_tabs.addTab(self._build_habits_panel(), "⏰ Habits")
        proactive_tabs.addTab(self._build_self_improve_panel(), "🔧 Self-Improve")
        proactive_tabs.addTab(self._build_memory_panel(), "🧠 Memory")
        layout.addWidget(proactive_tabs)
        self.tabs.addTab(proactive_widget, "🎯 Proactive")

    def create_quick_actions_tab(self):
        widget = QWidget()
        root = QVBoxLayout(widget)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # Header
        hdr = QLabel("⚡ Quick Actions")
        hdr.setStyleSheet("font-size:15px;font-weight:bold;padding:2px 0 4px 0;")
        root.addWidget(hdr)

        sub = QLabel(
            "Drag any capability from the list on the left onto the board — "
            "or double-click to pin it instantly."
        )
        sub.setStyleSheet("font-size:10px;color:#8899aa;padding-bottom:6px;")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # Main splitter: list | board
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        # ── LEFT: capability list ─────────────────────────────────────────
        left = QWidget()
        left.setMaximumWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(4)

        search_lbl = QLabel("Search capabilities")
        search_lbl.setStyleSheet("font-size:9px;color:#6677aa;")
        ll.addWidget(search_lbl)

        self._qa_search = QLineEdit()
        self._qa_search.setPlaceholderText("🔍 filter…")
        self._qa_search.setStyleSheet(
            "background:#1a1d23;border:1px solid #3b4060;"
            "border-radius:4px;padding:4px 8px;color:#c8d0e0;font-size:11px;"
        )
        self._qa_search.textChanged.connect(self._qa_filter)
        ll.addWidget(self._qa_search)

        # Category filter row — single-press to scope the list to a group.
        cat_row = QHBoxLayout()
        cat_row.setSpacing(2)
        cat_row.setContentsMargins(0, 2, 0, 2)
        self._qa_active_category = "All"
        self._qa_category_buttons: dict[str, QPushButton] = {}
        for label, emoji in (("All", "★"), ("Window", "🪟"), ("Media", "🎵"),
                             ("System", "⚙"), ("Stats", "📊"), ("Tools", "🛠")):
            b = QPushButton(f"{emoji} {label}")
            b.setCheckable(True)
            b.setChecked(label == "All")
            b.setStyleSheet(
                "QPushButton{background:#14171f;color:#8899aa;"
                "border:1px solid #2a2d3a;border-radius:4px;"
                "padding:3px 6px;font-size:9px;}"
                "QPushButton:checked{background:#2e3a5a;color:#88c0d0;"
                "border:1px solid #5a6c8e;}"
                "QPushButton:hover{background:#1e2230;}"
            )
            b.clicked.connect(lambda _checked, n=label: self._qa_set_category(n))
            self._qa_category_buttons[label] = b
            cat_row.addWidget(b)
        cat_row.addStretch(1)
        ll.addLayout(cat_row)

        self._qa_cap_list = _CapabilityList()
        self._qa_cap_list.setDragEnabled(True)
        self._qa_cap_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._qa_cap_list.setStyleSheet(
            "QListWidget{background:#14171f;border:1px solid #2a2d3a;"
            "border-radius:4px;color:#c8d0e0;font-size:10px;}"
            "QListWidget::item{padding:4px 8px;border-bottom:1px solid #1e2230;}"
            "QListWidget::item:hover{background:#1e2230;}"
            "QListWidget::item:selected{background:#2e3a5a;color:#88c0d0;}"
        )
        self._qa_cap_list.itemDoubleClicked.connect(
            lambda it: self._qa_board.add_card(
                it.data(Qt.ItemDataRole.UserRole) or it.text().split()[0]
            ) and self._qa_save_buttons()
        )
        ll.addWidget(self._qa_cap_list, 1)

        # Refresh + count label
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh List")
        refresh_btn.setStyleSheet(
            "QPushButton{background:#1e2230;color:#88c0d0;border:1px solid #3b4060;"
            "border-radius:4px;padding:4px 8px;font-size:10px;}"
            "QPushButton:hover{background:#2e3a5a;}"
        )
        refresh_btn.clicked.connect(self._qa_refresh_capabilities)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        self._qa_count_lbl = QLabel("")
        self._qa_count_lbl.setStyleSheet("font-size:9px;color:#6677aa;")
        btn_row.addWidget(self._qa_count_lbl)
        ll.addLayout(btn_row)

        splitter.addWidget(left)

        # ── RIGHT: button board ───────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(6)

        board_hdr = QHBoxLayout()
        board_lbl = QLabel("Button Board  —  drop here")
        board_lbl.setStyleSheet("font-size:10px;color:#8899aa;")
        board_hdr.addWidget(board_lbl)
        board_hdr.addStretch()

        add_all_btn = QPushButton("➕ Pin All Visible")
        add_all_btn.setToolTip("Pin every currently visible (filtered) capability")
        add_all_btn.setStyleSheet(
            "QPushButton{background:#1e2230;color:#a3be8c;border:1px solid #3d6b56;"
            "border-radius:4px;padding:3px 8px;font-size:10px;}"
            "QPushButton:hover{background:#2d4a3e;}"
        )
        add_all_btn.clicked.connect(self._qa_pin_visible)
        board_hdr.addWidget(add_all_btn)

        clear_btn = QPushButton("🗑️ Clear Board")
        clear_btn.setStyleSheet(
            "QPushButton{background:#1e2230;color:#bf616a;border:1px solid #5e3030;"
            "border-radius:4px;padding:3px 8px;font-size:10px;}"
            "QPushButton:hover{background:#3b1f1f;}"
        )
        clear_btn.clicked.connect(self._qa_clear_board)
        board_hdr.addWidget(clear_btn)
        rl.addLayout(board_hdr)

        # Drop-zone hint bar (shown when board is empty)
        self._qa_drop_hint = QLabel(
            "  ⬆  Drag a capability here  —  or double-click one in the list"
        )
        self._qa_drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qa_drop_hint.setStyleSheet(
            "color:#3b4060;font-size:13px;border:2px dashed #2a2d3a;"
            "border-radius:8px;padding:20px;background:#10121a;"
        )
        rl.addWidget(self._qa_drop_hint)

        # Scroll area wrapping the board
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{background:#10121a;border:2px solid #2a2d3a;border-radius:6px;}"
        )
        self._qa_board = _QABoard(self._qa_run_action)
        self._qa_board.setStyleSheet("background:#10121a;")
        self._qa_board.changed.connect(self._qa_on_board_changed)
        self._qa_board.changed.connect(self._qa_save_buttons)
        scroll.setWidget(self._qa_board)
        rl.addWidget(scroll, 1)

        # Result display
        result_lbl = QLabel("Action Output:")
        result_lbl.setStyleSheet("font-size:9px;color:#6677aa;margin-top:4px;")
        rl.addWidget(result_lbl)

        self._qa_result = QTextEdit()
        self._qa_result.setReadOnly(True)
        self._qa_result.setMaximumHeight(100)
        self._qa_result.setStyleSheet(
            "background:#0d0f14;color:#a3be8c;font-size:10px;"
            "border:1px solid #2a2d3a;border-radius:4px;"
        )
        self._qa_result.setPlaceholderText("Action results appear here…")
        rl.addWidget(self._qa_result)

        splitter.addWidget(right)
        splitter.setSizes([210, 580])
        root.addWidget(splitter, 1)

        self.tabs.addTab(widget, "⚡ Quick Actions")

        # Wire the thread-safe result signal
        self._qa_result_sig.connect(
            self._qa_result.setPlainText, Qt.ConnectionType.QueuedConnection
        )

        # Load capabilities and saved buttons
        self._qa_refresh_capabilities()
        self._qa_load_buttons()

    def create_screen_control_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        header = QLabel("🖥️  Screen Vision & OCR")
        header.setStyleSheet("font-size:16px; font-weight:bold; padding:8px 4px 4px 4px;")
        layout.addWidget(header)

        sub = QLabel("Capture the screen, then “Ask ELI” to look at it with local vision "
                     "(Moondream/Qwen-VL) + OCR. OCR is optional — it adds exact text as "
                     "ground truth.")
        sub.setStyleSheet("font-size:11px; color:#8899aa; padding-bottom:6px;")
        layout.addWidget(sub)

        # ── Screen analysis settings (all local — no URLs) ─────────────────
        cfg_box = QGroupBox("Screen analysis settings")
        cfg_box.setStyleSheet(
            "QGroupBox { color:#8eaac8; font-weight:bold; border:1px solid #2a2d3a; "
            "border-radius:6px; margin-top:8px; padding-top:8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left:10px; padding:0 4px; }"
        )
        cfg_form = QFormLayout(cfg_box)

        self._sc_depth_combo = QComboBox()
        self._sc_depth_combo.addItem("Quick glance", "quick")
        self._sc_depth_combo.addItem("Standard", "standard")
        self._sc_depth_combo.addItem("Deep / full audit", "deep")
        self._sc_depth_combo.setToolTip(
            "Quick = fast Moondream glance. Standard = VL + OCR fusion. "
            "Deep = thorough audit with full OCR + text-model fusion."
        )
        cfg_form.addRow("Analysis depth", self._sc_depth_combo)

        self._sc_use_memory_cb = QCheckBox("Include prior screen memories when relevant")
        self._sc_use_memory_cb.setStyleSheet("color:#c8d0e0;")
        cfg_form.addRow("", self._sc_use_memory_cb)

        self._sc_use_research_cb = QCheckBox("Relate to my research/projects when asked")
        self._sc_use_research_cb.setStyleSheet("color:#c8d0e0;")
        cfg_form.addRow("", self._sc_use_research_cb)

        self._sc_ground_combo = QComboBox()
        for label, val in [
            ("Off — AT-SPI + OCR only", "none"),
            ("Auto — local VL when available", "auto"),
            ("Local GGUF (Phi-Ground / OS-Atlas / UGround)", "local_gguf"),
        ]:
            self._sc_ground_combo.addItem(label, val)
        self._sc_ground_combo.setToolTip(
            "Precision click for icons/non-text UI. 100% local GGUF — "
            "drop a grounding model into models/ or pick Auto."
        )
        cfg_form.addRow("Precision UI click", self._sc_ground_combo)

        self._sc_agent_combo = QComboBox()
        for label, val in [
            ("Off", "none"),
            ("Auto — UI-TARS-style GGUF if present", "auto"),
            ("Local GGUF (UI-TARS / computer-use)", "local_gguf"),
        ]:
            self._sc_agent_combo.addItem(label, val)
        self._sc_agent_combo.setToolTip(
            "Multi-step desktop agent (local only). Requires a computer-use GGUF in models/."
        )
        cfg_form.addRow("Computer-use agent", self._sc_agent_combo)

        self._sc_ambient_cb = QCheckBox("Ambient screen watch (periodic local glances → memory)")
        self._sc_ambient_cb.setStyleSheet("color:#c8d0e0;")
        cfg_form.addRow("", self._sc_ambient_cb)

        self._sc_interval_spin = QSpinBox()
        self._sc_interval_spin.setRange(60, 3600)
        self._sc_interval_spin.setSingleStep(60)
        self._sc_interval_spin.setSuffix(" s")
        cfg_form.addRow("Glance interval", self._sc_interval_spin)

        _sc_save_btn = QPushButton("💾 Save screen settings")
        _sc_save_btn.clicked.connect(self._sc_save_settings)
        cfg_form.addRow("", _sc_save_btn)

        layout.addWidget(cfg_box)
        self._sc_load_settings()

        # ── Screenshot + OCR row ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._sc_capture_btn = QPushButton("📸 Capture Full Screen")
        self._sc_capture_btn.clicked.connect(self._sc_capture)
        btn_row.addWidget(self._sc_capture_btn)

        self._sc_ocr_btn = QPushButton("🔍 Run OCR")
        self._sc_ocr_btn.setEnabled(False)
        self._sc_ocr_btn.clicked.connect(self._sc_run_ocr)
        btn_row.addWidget(self._sc_ocr_btn)

        self._sc_analyse_btn = QPushButton("🤖 Ask ELI")
        self._sc_analyse_btn.setEnabled(False)
        self._sc_analyse_btn.clicked.connect(self._sc_ask_eli)
        btn_row.addWidget(self._sc_analyse_btn)

        self._sc_deep_btn = QPushButton("🔬 Deep audit")
        self._sc_deep_btn.setEnabled(False)
        self._sc_deep_btn.setToolTip("Full local screen audit — VL + OCR + text-model fusion")
        self._sc_deep_btn.clicked.connect(lambda: self._sc_ask_eli(deep=True))
        btn_row.addWidget(self._sc_deep_btn)

        self._sc_clear_btn = QPushButton("🗑️ Clear")
        self._sc_clear_btn.clicked.connect(self._sc_clear)
        btn_row.addWidget(self._sc_clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Screenshot preview ────────────────────────────────────────────
        preview_label = QLabel("Screenshot Preview:")
        preview_label.setStyleSheet("font-size:10px; color:#8899aa; margin-top:4px;")
        layout.addWidget(preview_label)

        self._sc_preview = QLabel()
        self._sc_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sc_preview.setStyleSheet("background:#12141a; border:1px solid #2a2d3a; border-radius:4px;")
        self._sc_preview.setMinimumHeight(160)
        self._sc_preview.setText("(no screenshot yet)")
        layout.addWidget(self._sc_preview)

        # ── OCR output ────────────────────────────────────────────────────
        ocr_label = QLabel("Extracted Text (OCR):")
        ocr_label.setStyleSheet("font-size:10px; color:#8899aa; margin-top:6px;")
        layout.addWidget(ocr_label)

        self._sc_ocr_text = QTextEdit()
        self._sc_ocr_text.setReadOnly(False)
        self._sc_ocr_text.setPlaceholderText("OCR text will appear here — you can also type or paste text to analyse.")
        self._sc_ocr_text.setStyleSheet("background:#1a1d23; color:#c8d0e0; font-size:11px; border-radius:4px;")
        self._sc_ocr_text.setMaximumHeight(140)
        layout.addWidget(self._sc_ocr_text)

        # ── ELI response ──────────────────────────────────────────────────
        resp_label = QLabel("ELI Analysis:")
        resp_label.setStyleSheet("font-size:10px; color:#8899aa; margin-top:6px;")
        layout.addWidget(resp_label)

        self._sc_response = QTextEdit()
        self._sc_response.setReadOnly(True)
        self._sc_response.setPlaceholderText("ELI's analysis of the screen content will appear here.")
        self._sc_response.setStyleSheet("background:#1a1d23; color:#c8d0e0; font-size:11px; border-radius:4px;")
        layout.addWidget(self._sc_response)

        self._sc_screenshot_path: str = ""

        self.tabs.addTab(widget, "🖥️ Screen")

    def _sc_load_settings(self):
        try:
            from eli.core.runtime_settings import load_settings as _ls
            s = _ls() or {}
        except Exception:
            s = {}
        depth = str(s.get("screen_analysis_depth", "standard") or "standard")
        idx = self._sc_depth_combo.findData(depth)
        if idx >= 0:
            self._sc_depth_combo.setCurrentIndex(idx)
        self._sc_use_memory_cb.setChecked(bool(s.get("screen_analysis_use_memory", True)))
        self._sc_use_research_cb.setChecked(bool(s.get("screen_analysis_use_research", True)))
        gidx = self._sc_ground_combo.findData(str(s.get("ui_ground_backend", "none") or "none"))
        if gidx >= 0:
            self._sc_ground_combo.setCurrentIndex(gidx)
        aidx = self._sc_agent_combo.findData(str(s.get("computer_use_backend", "none") or "none"))
        if aidx >= 0:
            self._sc_agent_combo.setCurrentIndex(aidx)
        self._sc_ambient_cb.setChecked(bool(s.get("ambient_vision_enabled", False)))
        try:
            self._sc_interval_spin.setValue(int(s.get("ambient_vision_interval", 300) or 300))
        except Exception:
            log.debug("invalid ambient_vision_interval in settings; using spin default", exc_info=True)

    def _sc_save_settings(self):
        try:
            from eli.core.runtime_settings import save_settings as _ss
            _ss({
                "screen_analysis_depth": self._sc_depth_combo.currentData() or "standard",
                "screen_analysis_use_memory": bool(self._sc_use_memory_cb.isChecked()),
                "screen_analysis_use_research": bool(self._sc_use_research_cb.isChecked()),
                "ui_ground_backend": self._sc_ground_combo.currentData() or "none",
                "computer_use_backend": self._sc_agent_combo.currentData() or "none",
                "ambient_vision_enabled": bool(self._sc_ambient_cb.isChecked()),
                "ambient_vision_interval": int(self._sc_interval_spin.value()),
            })
            try:
                from eli.perception.ambient_vision import set_ambient_vision
                set_ambient_vision(bool(self._sc_ambient_cb.isChecked()))
            except Exception:
                log.debug("ambient vision sync failed", exc_info=True)
            if hasattr(self, "ambient_vision_btn"):
                on = bool(self._sc_ambient_cb.isChecked())
                self.ambient_vision_btn.blockSignals(True)
                self.ambient_vision_btn.setChecked(on)
                self.ambient_vision_btn.setText("👁 Watch: ON" if on else "👁 Watch: OFF")
                self.ambient_vision_btn.blockSignals(False)
            QMessageBox.information(self, "Screen settings", "Saved — all local, no network required.")
        except Exception as e:
            QMessageBox.warning(self, "Screen settings", f"Could not save: {e}")

    def create_ide_tab(self):
        ide_widget = QWidget()
        layout = QVBoxLayout(ide_widget)
        header = QLabel("⌨️  Integrated Development Environment")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        layout.addWidget(header)
        toolbar = QHBoxLayout()
        self.current_file_label = QLabel("No file open")
        toolbar.addWidget(self.current_file_label)
        toolbar.addStretch()
        new_file_btn = QPushButton("📄 New")
        new_file_btn.clicked.connect(self.ide_new_file)
        toolbar.addWidget(new_file_btn)
        open_file_btn = QPushButton("📂 Open")
        open_file_btn.clicked.connect(self.ide_open_file)
        toolbar.addWidget(open_file_btn)
        save_file_btn = QPushButton("💾 Save")
        save_file_btn.clicked.connect(self.ide_save_file)
        toolbar.addWidget(save_file_btn)
        save_as_btn = QPushButton("💾 Save As")
        save_as_btn.clicked.connect(self.ide_save_as)
        toolbar.addWidget(save_as_btn)
        run_btn = QPushButton("▶️ Run")
        run_btn.clicked.connect(self.ide_run_code)
        toolbar.addWidget(run_btn)
        layout.addLayout(toolbar)
        if QSCI_AVAILABLE:
            self.code_editor = QsciScintilla()
            self.code_editor.setLexer(QsciLexerPython(self.code_editor))
            self.code_editor.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
            self.code_editor.setMarginWidth(0, "00000")
            self.code_editor.setTabWidth(4)
            self.code_editor.setIndentationsUseTabs(False)
            self.code_editor.setAutoIndent(True)
        else:
            # Native full editor (line numbers, highlighting, auto-indent) —
            # QScintilla has no PySide6 build, so this IS the shipped editor.
            from eli.gui.code_editor import PyCodeEditor
            self.code_editor = PyCodeEditor()
        layout.addWidget(self.code_editor, stretch=7)
        console_group = QGroupBox("Console Output")
        console_layout = QVBoxLayout(console_group)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setMaximumHeight(200)
        console_layout.addWidget(self.console_output)
        clear_console_btn = QPushButton("Clear Console")
        clear_console_btn.clicked.connect(lambda: self.console_output.clear())
        console_layout.addWidget(clear_console_btn)
        layout.addWidget(console_group, stretch=3)
        self._ide_widget = ide_widget
        self.tabs.addTab(ide_widget, "⌨️  IDE")
        self.current_file_path = None

    def create_eli_world_tab(self):
        """Create Eli's World tab: local embodied autonomy/world-state HMI."""
        try:
            from eli.gui.tabs.eli_world_tab import EliWorldTab
            self._eli_world_widget = EliWorldTab(parent=self)
            self.tabs.addTab(self._eli_world_widget, "🌍 Eli's World")
            log.debug("[EliWorld] tab loaded")
        except Exception as _eli_world_err:
            log.debug(f"[EliWorld] failed to load: {_eli_world_err}")
            try:
                fallback = QWidget()
                QVBoxLayout(fallback).addWidget(QLabel(f"Eli's World unavailable: {_eli_world_err}"))
                self.tabs.addTab(fallback, "🌍 Eli's World")
            except Exception as _fallback_err:
                log.debug(f"[EliWorld] fallback tab failed: {_fallback_err}")

    def create_labs_tab(self):
        try:
            from eli.gui.labs_tab import LabsTab
            self._labs_widget = LabsTab(parent_window=self)
            self.tabs.addTab(self._labs_widget, "⚗️  Labs")
        except Exception as _labs_err:
            log.debug(f"[Labs] failed to load: {_labs_err}")
            fallback = QWidget()
            QVBoxLayout(fallback).addWidget(QLabel(f"Labs tab unavailable: {_labs_err}"))
            self.tabs.addTab(fallback, "⚗️  Labs")

    def create_coding_tab(self):
        try:
            from eli.gui.coding_tab import CodingTab
            self._coding_widget = CodingTab(parent_window=self)
            self.tabs.addTab(self._coding_widget, "🧩  Coding")
        except Exception as _coding_err:
            log.debug(f"[Coding] failed to load: {_coding_err}")
            fallback = QWidget()
            QVBoxLayout(fallback).addWidget(QLabel(f"Coding tab unavailable: {_coding_err}"))
            self.tabs.addTab(fallback, "🧩  Coding")

    def create_tasks_tab(self):
        """Advanced / scheduled / overnight background tasks (shared BackgroundTasks)."""
        try:
            from eli.gui.tabs.tasks_tab import TasksTab
            self._tasks_widget = TasksTab(parent_window=self)
            self.tabs.addTab(self._tasks_widget, "🗓️  Tasks")
        except Exception as _tasks_err:
            log.debug(f"[Tasks] failed to load: {_tasks_err}")
            fallback = QWidget()
            QVBoxLayout(fallback).addWidget(QLabel(f"Tasks tab unavailable: {_tasks_err}"))
            self.tabs.addTab(fallback, "🗓️  Tasks")

    def create_experimental_tab(self):
        """Create safe experimental prototype inventory tab."""
        try:
            from eli.gui.tabs.experimental_tab import ExperimentalTab
            self._experimental_widget = ExperimentalTab(parent=self)
            self.tabs.addTab(self._experimental_widget, "🧪 Experimental")
            log.debug("[Experimental] tab loaded")
        except Exception as _experimental_err:
            log.debug(f"[Experimental] failed to load: {_experimental_err}")
            try:
                fallback = QWidget()
                QVBoxLayout(fallback).addWidget(QLabel(f"Experimental tab unavailable: {_experimental_err}"))
                self.tabs.addTab(fallback, "🧪 Experimental")
            except Exception as _fallback_err:
                log.debug(f"[Experimental] fallback tab failed: {_fallback_err}")

    def create_extensions_tab(self):
        """Plugins, MCP servers and the marketplace as one top-level tab."""
        try:
            from eli.gui.tabs.extensions_tab import ExtensionsTab
            self._extensions_widget = ExtensionsTab(parent_window=self)
            self.tabs.addTab(self._extensions_widget, "🔌 Extensions")
            log.debug("[Extensions] tab loaded")
        except Exception as _ext_err:
            log.debug(f"[Extensions] failed to load: {_ext_err}")
            try:
                fallback = QWidget()
                QVBoxLayout(fallback).addWidget(
                    QLabel(f"Extensions tab unavailable: {_ext_err}"))
                self.tabs.addTab(fallback, "🔌 Extensions")
            except Exception as _fallback_err:
                log.debug(f"[Extensions] fallback tab failed: {_fallback_err}")

    def create_report_builder_tab(self):
        """Report Builder as a top-level main tab (promoted out of Labs). Grounded,
        evidence-first document generation with export to pdf/docx/lualatex/etc."""
        try:
            from eli.gui.labs_tab import _ReportTab
            self._report_builder_widget = _ReportTab(eli_callback=self._engine_ask)
            self.tabs.addTab(self._report_builder_widget, "📄 Report Builder")
        except Exception as e:
            fallback = QLabel(f"Report Builder unavailable: {e}")
            fallback.setWordWrap(True)
            self.tabs.addTab(fallback, "📄 Report Builder")

    def create_files_tab(self):
        files_widget = QWidget()
        layout = QVBoxLayout(files_widget)
        header = QLabel("📁 File Browser")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        layout.addWidget(header)
        toolbar = QHBoxLayout()
        home_btn = QPushButton("🏠 Home")
        home_btn.clicked.connect(lambda: self.browse_directory(str(Path.home())))
        toolbar.addWidget(home_btn)
        project_btn = QPushButton("📦 Project Root")
        project_btn.clicked.connect(self.browse_project_root)
        toolbar.addWidget(project_btn)
        toolbar.addStretch()
        self.path_label = QLabel("Current: ~")
        toolbar.addWidget(self.path_label)
        layout.addLayout(toolbar)
        self.file_tree = QTreeView()
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("")
        self.file_tree.setModel(self.file_model)
        self.file_tree.setRootIndex(self.file_model.index(str(Path.home())))
        self.file_tree.doubleClicked.connect(self.on_file_double_click)
        for i in range(1, 4):
            self.file_tree.hideColumn(i)
        layout.addWidget(self.file_tree)

        # ── Convert document (any selected file → chosen format) ──────────────
        # Backed by CONVERT_DOCUMENT (pandoc + lualatex engine, LibreOffice
        # fallback). Select a file in the tree, pick a format, click Convert.
        conv_row = QHBoxLayout()
        conv_row.addWidget(QLabel("Convert selected →"))
        self.convert_format_combo = QComboBox()
        # label → CONVERT_DOCUMENT format token
        self._convert_formats = [
            ("PDF", "pdf"),
            ("PDF (LuaLaTeX)", "lualatex"),
            ("Word (.docx)", "docx"),
            ("Word 97 (.doc)", "doc"),
            ("OpenDocument (.odt)", "odt"),
            ("Rich Text (.rtf)", "rtf"),
            ("HTML", "html"),
            ("Markdown", "md"),
            ("LaTeX (.tex)", "tex"),
            ("EPUB", "epub"),
            ("Plain text (.txt)", "txt"),
        ]
        for label, _tok in self._convert_formats:
            self.convert_format_combo.addItem(label)
        conv_row.addWidget(self.convert_format_combo)
        convert_btn = QPushButton("🔄 Convert")
        convert_btn.setToolTip("Convert the file selected above to the chosen format "
                               "(any document type — lualatex, pdf, doc, docx, …).")
        convert_btn.clicked.connect(self._convert_selected_file)
        conv_row.addWidget(convert_btn)
        conv_row.addStretch()
        layout.addLayout(conv_row)
        self.convert_status_label = QLabel("")
        self.convert_status_label.setWordWrap(True)
        self.convert_status_label.setStyleSheet("color:#7a9cbf;font-size:11px;")
        layout.addWidget(self.convert_status_label)

        self.tabs.addTab(files_widget, "📁 Files")

    def create_settings_tab(self):
        root = QWidget()
        self._settings_root = root
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── body: sidebar + content stack ─────────────────────────────────────
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setHandleWidth(1)

        # ── LEFT sidebar ──────────────────────────────────────────────────────
        self._settings_nav = QListWidget()
        self._settings_nav.setFixedWidth(138)
        self._settings_nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._settings_nav.setFrameShape(QFrame.Shape.NoFrame)
        self._settings_nav.setSpacing(1)
        self._settings_nav.setStyleSheet("""
            QListWidget {
                background: #1e2027;
                border: none;
                padding: 8px 0;
            }
            QListWidget::item {
                color: #8b9ab0;
                font-size: 11px;
                font-weight: 500;
                padding: 7px 14px;
                border-radius: 0;
                border-left: 3px solid transparent;
            }
            QListWidget::item:hover {
                color: #cdd6e8;
                background: #262b35;
            }
            QListWidget::item:selected {
                color: #ffffff;
                background: #2a3040;
                border-left: 3px solid #5e81ac;
                font-weight: 700;
            }
        """)
        for label, icon in self._SETTINGS_NAV:
            item = QListWidgetItem(f"  {icon}  {label}")
            self._settings_nav.addItem(item)
        body.addWidget(self._settings_nav)

        # ── RIGHT content stack (wrapped in zoomable view) ────────────────────
        self._settings_stack = QStackedWidget()
        self._settings_stack.setStyleSheet(
            "QStackedWidget { background: #1a1d23; border: none; }"
        )
        self._settings_stack.addWidget(self._build_settings_model_page())
        self._settings_stack.addWidget(self._build_settings_runtime_page())
        self._settings_stack.addWidget(self._build_settings_generation_page())
        self._settings_stack.addWidget(self._build_settings_reasoning_page())
        self._settings_stack.addWidget(self._build_settings_identity_page())
        self._settings_stack.addWidget(self._build_settings_audio_page())
        self._settings_stack.addWidget(self._build_settings_app_page())
        self._settings_stack.addWidget(self._build_settings_agents_page())
        self._settings_stack.addWidget(self._build_settings_gaze_page())
        self._settings_stack.addWidget(self._build_settings_server_page())
        self._settings_stack.addWidget(self._build_settings_advanced_page())

        self._settings_zoom_view = _ZoomableSettingsView(self._settings_stack)
        body.addWidget(self._settings_zoom_view)

        body.setSizes([138, 9999])
        body.setCollapsible(0, False)
        body.setCollapsible(1, False)
        root_layout.addWidget(body, stretch=1)

        # ── FOOTER: always-visible save bar ───────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(42)
        footer.setStyleSheet(
            "QWidget { background: #12141a; border-top: 1px solid #2a2d36; }"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 6, 14, 6)

        save_btn = QPushButton("💾  Save")
        save_btn.setFixedHeight(28)
        save_btn.setStyleSheet(
            "QPushButton{background:#5e81ac;color:#fff;font-weight:bold;"
            "border:none;border-radius:5px;padding:0 14px;font-size:11px;}"
            "QPushButton:hover{background:#81a1c1;}"
        )
        save_btn.clicked.connect(self.save_settings)
        footer_layout.addWidget(save_btn)

        detect_btn = QPushButton("🔍  Auto-Detect")
        detect_btn.setFixedHeight(28)
        detect_btn.setStyleSheet(
            "QPushButton{background:#3b4252;color:#d8dee9;font-weight:500;"
            "border:none;border-radius:5px;padding:0 14px;font-size:11px;}"
            "QPushButton:hover{background:#4c566a;}"
        )
        detect_btn.clicked.connect(self.detect_optimal_settings)
        footer_layout.addWidget(detect_btn)

        zoom_out_btn = QPushButton("A-")
        zoom_out_btn.setFixedHeight(28)
        zoom_out_btn.setToolTip("Zoom out settings (Ctrl+-)")
        zoom_out_btn.setStyleSheet(
            "QPushButton{background:#2e3440;color:#d8dee9;font-weight:600;"
            "border:none;border-radius:5px;padding:0 10px;font-size:10px;}"
            "QPushButton:hover{background:#3b4252;color:#ffffff;}"
        )
        zoom_out_btn.clicked.connect(self._settings_zoom_view.zoom_out)
        footer_layout.addWidget(zoom_out_btn)

        zoom_reset_btn = QPushButton("100%")
        zoom_reset_btn.setFixedHeight(28)
        zoom_reset_btn.setToolTip("Reset settings zoom (Ctrl+0)")
        zoom_reset_btn.setStyleSheet(
            "QPushButton{background:#2e3440;color:#d8dee9;font-weight:600;"
            "border:none;border-radius:5px;padding:0 10px;font-size:10px;}"
            "QPushButton:hover{background:#3b4252;color:#ffffff;}"
        )
        zoom_reset_btn.clicked.connect(
            lambda: self._settings_zoom_view.zoom_reset()
        )
        footer_layout.addWidget(zoom_reset_btn)

        zoom_in_btn = QPushButton("A+")
        zoom_in_btn.setFixedHeight(28)
        zoom_in_btn.setToolTip("Zoom in settings (Ctrl+=)")
        zoom_in_btn.setStyleSheet(
            "QPushButton{background:#2e3440;color:#d8dee9;font-weight:600;"
            "border:none;border-radius:5px;padding:0 10px;font-size:10px;}"
            "QPushButton:hover{background:#3b4252;color:#ffffff;}"
        )
        zoom_in_btn.clicked.connect(self._settings_zoom_view.zoom_in)
        footer_layout.addWidget(zoom_in_btn)

        footer_layout.addStretch()
        root_layout.addWidget(footer)

        # wire nav → stack
        self._settings_nav.currentRowChanged.connect(
            self._settings_stack.setCurrentIndex
        )
        self._settings_nav.setCurrentRow(0)

        self.tabs.addTab(root, "⚙️ Settings")

        # Register the plugin consent dialog. Until this runs, permissions.check()
        # denies everything — correct for headless/API processes, and why a GUI must
        # opt in explicitly rather than the default being "allow".
        try:
            from eli.gui.panels.permission_dialog import install_consent_ui
            self._consent_bridge = install_consent_ui(self)
        except Exception as _perm_err:
            log.debug(f"[GUI] plugin consent UI unavailable: {_perm_err}")

    def refresh_audit_tab(self):
        try:
            from eli.runtime import orchestrator_audit_ledger as _oal
        except Exception as exc:
            self.audit_integrity_label.setText(f"⚠️ Audit ledger unavailable: {exc}")
            return
        try:
            v = _oal.verify_chain()
            if v.get("ok"):
                self.audit_integrity_label.setText(
                    f"✅ Chain intact — {v.get('checked', 0)} checked, "
                    f"{v.get('chained', 0)} chained, keyed={v.get('keyed', False)}"
                )
            else:
                fb = v.get("first_break") or {}
                self.audit_integrity_label.setText(
                    f"🛑 Chain broken at row {fb.get('id')}: {fb.get('reason')}"
                )
        except Exception as exc:
            self.audit_integrity_label.setText(f"⚠️ Verify failed: {exc}")

        try:
            rows = _oal.recent_turns(limit=200)
        except Exception as exc:
            log.debug(f"[GUI] audit recent_turns failed: {exc}")
            rows = []
        self.audit_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            try:
                ts_str = datetime.fromtimestamp(float(row.get("ts") or 0)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_str = ""
            conf = row.get("confidence")
            conf_str = f"{float(conf):.2f}" if conf is not None else ""
            elapsed = row.get("elapsed_ms")
            elapsed_str = f"{float(elapsed):.0f}" if elapsed is not None else ""
            values = [
                ts_str, str(row.get("request_id") or ""), str(row.get("source") or ""),
                str(row.get("parent_request_id") or ""), str(row.get("action") or ""),
                str(row.get("agents_used") or ""),
                conf_str, elapsed_str, "✅" if row.get("ok") else "❌", str(row.get("outcome") or ""),
            ]
            for col, val in enumerate(values):
                self.audit_table.setItem(i, col, QTableWidgetItem(val))

    def create_audit_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QLabel("🔒 Orchestrator Audit")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 6px;")
        layout.addWidget(header)

        hint = QLabel("Tamper-evident, metadata-only record of every turn's real outcome — "
                      "one row per request, including commands, background tasks and "
                      "followthrough runs (Parent links a run to the request that caused it). "
                      "No prompt or response content. HMAC-chained; editing, deleting or "
                      "reordering a row breaks the chain and Verify Chain reports it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#88909c;font-size:10px;padding:2px 6px;")
        layout.addWidget(hint)

        self.audit_integrity_label = QLabel("Not yet checked.")
        self.audit_integrity_label.setStyleSheet("padding:4px 6px;")
        layout.addWidget(self.audit_integrity_label)

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_audit_tab)
        toolbar.addWidget(refresh_btn)
        verify_btn = QPushButton("✅ Verify Chain")
        verify_btn.clicked.connect(self.refresh_audit_tab)
        toolbar.addWidget(verify_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(10)
        self.audit_table.setHorizontalHeaderLabels(
            ["Time", "Request", "Source", "Parent", "Action", "Agents", "Confidence",
             "Elapsed(ms)", "OK", "Outcome"])
        self.audit_table.horizontalHeader().setStretchLastSection(True)
        select_rows = getattr(
            getattr(QAbstractItemView, "SelectionBehavior", QAbstractItemView), "SelectRows")
        self.audit_table.setSelectionBehavior(select_rows)
        self.audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.audit_table)

        self.refresh_audit_tab()
        self._audit_tab_timer = QTimer(self)
        self._audit_tab_timer.setInterval(15_000)
        self._audit_tab_timer.timeout.connect(self.refresh_audit_tab)
        self._audit_tab_timer.start()

        self.tabs.addTab(widget, "🔒 Audit")
