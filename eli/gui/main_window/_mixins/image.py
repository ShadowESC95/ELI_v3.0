"""ImageMixin — 20 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403
from eli.gui.main_window._common import _backend_context
from eli.gui.main_window.adapters import _GUIEngineAdapter


class ImageMixin:
    def _resolve_image_seed(self) -> int:
        """Seed for the next generation. Random each time unless the user locked
        the seed — otherwise identical prompts produce the identical image."""
        import random as _rnd
        lock = getattr(self, "image_seed_lock", None)
        if lock is not None and lock.isChecked() and hasattr(self, "image_seed_input"):
            return int(self.image_seed_input.value())
        return _rnd.randint(1, 999999)

    def open_image_studio_tab(self):
        try:
            idx = self.tabs.indexOf(self.image_tab_widget)
            if idx >= 0:
                self.tabs.setCurrentIndex(idx)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def browse_image_project_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Image Reference Folder",
            self.image_project_input.text().strip() if hasattr(self, "image_project_input") else str(PROJECT_ROOT),
        )
        if folder:
            if hasattr(self, "image_project_input"):
                self.image_project_input.setText(folder)
            if hasattr(self, "image_default_project_input"):
                self.image_default_project_input.setText(folder)

    def browse_image_model_path(self):
        start = ""
        if hasattr(self, "image_model_combo"):
            start = self.image_model_combo.currentText().strip()
        if not start and hasattr(self, "image_model_path_input"):
            start = self.image_model_path_input.text().strip()

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Diffusion Model File",
            start or str(PROJECT_ROOT / "models" / "image"),
            "Model Files (*.safetensors *.ckpt);;All Files (*)",
        )
        selected = file_path
        if not selected:
            selected = QFileDialog.getExistingDirectory(
                self,
                "Select Diffusion Model Folder",
                start or str(PROJECT_ROOT / "models" / "image"),
            )
        if not selected:
            return

        if hasattr(self, "image_model_combo"):
            self.image_model_combo.setEditText(selected)
        if hasattr(self, "image_model_path_input"):
            self.image_model_path_input.setText(selected)
        try:
            from eli.core.model_folders import remember_folder_of
            remember_folder_of(selected)
        except (ImportError, OSError, ValueError) as e:
            log.debug(f"[IMAGE] folder not remembered: {e}", exc_info=True)
        self.refresh_image_model_sources()

    def refresh_image_model_sources(self):
        paths = []
        try:
            from eli.tools.image_engine import discover_local_image_models
            paths = [str(p) for p in discover_local_image_models()]
        except Exception as e:
            log.debug(f"[IMAGE] model scan failed: {e}")

        if hasattr(self, "image_model_combo"):
            current = self.image_model_combo.currentText().strip()
            self.image_model_combo.blockSignals(True)
            self.image_model_combo.clear()
            for path in paths:
                label = Path(path).name if Path(path).name else path
                self.image_model_combo.addItem(label, path)
            if current:
                idx = self.image_model_combo.findData(current)
                if idx >= 0:
                    self.image_model_combo.setCurrentIndex(idx)
                else:
                    self.image_model_combo.setEditText(current)
            self.image_model_combo.blockSignals(False)

        if hasattr(self, "image_model_select"):
            chosen = (self.image_model_path_input.text().strip() if hasattr(self, "image_model_path_input")
                      else str(self.image_model_select.currentData() or ""))
            self.image_model_select.blockSignals(True)
            self.image_model_select.clear()
            self.image_model_select.addItem("— none (procedural rendering) —", "")
            for path in paths:
                self.image_model_select.addItem(Path(path).name or path, path)
            if chosen and self.image_model_select.findData(chosen) < 0:
                self.image_model_select.addItem(Path(chosen).name or chosen, chosen)
            idx = self.image_model_select.findData(chosen) if chosen else 0
            self.image_model_select.setCurrentIndex(idx if idx >= 0 else 0)
            self.image_model_select.blockSignals(False)

        if hasattr(self, "image_backend_note"):
            if paths:
                self.image_backend_note.setText(
                    f"Detected {len(paths)} local diffusion model candidate(s). "
                    "Choose one to enable high-fidelity 3D-style rendering."
                )
            else:
                self.image_backend_note.setText(
                    "No local diffusion checkpoint detected yet. Drop one into models/image/ or paste a full local path to unlock photoreal rendering."
                )

    def _selected_image_model_path(self) -> str:
        if hasattr(self, "image_model_combo"):
            data = self.image_model_combo.currentData()
            if data:
                return str(data).strip()
            text = self.image_model_combo.currentText().strip()
            if text:
                return text
        if hasattr(self, "image_model_path_input"):
            return self.image_model_path_input.text().strip()
        return ""

    def _apply_image_quality_preset(self, preset: str):
        mapping = {
            "draft": (20, 5.5),
            "balanced": (32, 6.8),
            "ultra": (44, 7.8),
            "extreme": (60, 8.8),
        }
        steps, guidance = mapping.get(str(preset).strip().lower(), (36, 7.2))
        if hasattr(self, "image_steps_input"):
            self.image_steps_input.setValue(int(steps))
        if hasattr(self, "image_guidance_input"):
            self.image_guidance_input.setValue(float(guidance))

    def _apply_image_default_quality_preset(self, preset: str):
        mapping = {
            "draft": (20, 5.5),
            "balanced": (32, 6.8),
            "ultra": (44, 7.8),
            "extreme": (60, 8.8),
        }
        steps, guidance = mapping.get(str(preset).strip().lower(), (36, 7.2))
        if hasattr(self, "image_steps_default_input"):
            self.image_steps_default_input.setValue(int(steps))
        if hasattr(self, "image_guidance_default_input"):
            self.image_guidance_default_input.setValue(float(guidance))

    def refresh_image_presets(self):
        try:
            from eli.tools.image_engine import discover_presets
            presets = discover_presets()
        except Exception as e:
            presets = []
            log.debug(f"[IMAGE] preset scan failed: {e}")
        if hasattr(self, "image_preset_combo"):
            current = self.image_preset_combo.currentText()
            self.image_preset_combo.blockSignals(True)
            self.image_preset_combo.clear()
            self.image_preset_combo.addItem("")
            for preset in presets:
                self.image_preset_combo.addItem(preset)
            idx = self.image_preset_combo.findText(current)
            if idx >= 0:
                self.image_preset_combo.setCurrentIndex(idx)
            self.image_preset_combo.blockSignals(False)

    def refresh_image_gallery(self):
        try:
            from eli.tools.image_engine import list_recent_outputs
            outputs = list_recent_outputs(limit=30)
        except Exception as e:
            outputs = []
            log.debug(f"[IMAGE] output scan failed: {e}")
        if not hasattr(self, "image_results_list"):
            return
        self.image_results_list.clear()
        for path in outputs:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.image_results_list.addItem(item)
        if outputs:
            self.image_results_list.setCurrentRow(0)
            self._current_image_path = str(outputs[0])
            if hasattr(self, "image_eli_context_label"):
                self.image_eli_context_label.setText(f"ELI will discuss: {outputs[0].name}")
                self.image_eli_context_label.setStyleSheet(
                    "color: #66dd88; font-size: 9px; font-style: italic; padding: 0 2px;"
                )

    def _focus_generated_image_result(self, result) -> None:
        target = ""
        if getattr(result, "saved_paths", None):
            target = str(result.saved_paths[0])
        elif getattr(result, "contact_sheet", None):
            target = str(result.contact_sheet)
        if not target:
            return

        self.open_image_studio_tab()
        matched_row = -1
        for i in range(self.image_results_list.count()):
            item = self.image_results_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == target:
                matched_row = i
                break
        if matched_row < 0:
            item = QListWidgetItem(Path(target).name)
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.image_results_list.insertItem(0, item)
            matched_row = 0
        if matched_row >= 0:
            self.image_results_list.setCurrentRow(matched_row)
            self.image_results_list.scrollToItem(self.image_results_list.item(matched_row))
        loaded = self.image_preview.set_image_path(target)
        self._current_image_path = target
        if hasattr(self, "image_eli_context_label"):
            from pathlib import Path as _P2
            self.image_eli_context_label.setText(f"ELI will discuss: {_P2(target).name}")
            self.image_eli_context_label.setStyleSheet(
                "color: #66dd88; font-size: 9px; font-style: italic; padding: 0 2px;"
            )
        if loaded:
            self.image_preview.update()
            QApplication.processEvents()
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _show_selected_image_result(self):
        items = self.image_results_list.selectedItems()
        if not items:
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if path:
            self.image_preview.set_image_path(path)
            self._current_image_path = path
            if hasattr(self, "image_eli_context_label"):
                from pathlib import Path as _P
                self.image_eli_context_label.setText(f"ELI will discuss: {_P(path).name}")
                self.image_eli_context_label.setStyleSheet(
                    "color: #66dd88; font-size: 9px; font-style: italic; padding: 0 2px;"
                )

    def open_selected_image_output(self):
        items = self.image_results_list.selectedItems()
        if not items:
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_image_output_folder(self):
        try:
            from eli.tools.image_engine import output_dir
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir())))
        except Exception as e:
            QMessageBox.warning(self, "Open Folder", f"Unable to open image output folder: {e}")

    def _ask_eli_about_image(self):
        """Ask ELI about the currently previewed image — thread-safe."""
        question = self.image_eli_input.text().strip()
        if not question:
            question = "Describe this image in detail."
        img_path = getattr(self, "_current_image_path", None)
        if not img_path:
            self.image_eli_response.setPlainText(
                "Select an image from the list above first."
            )
            return

        self.image_eli_input.clear()
        # Update via signal (already on main thread here, direct call is fine)
        self.image_eli_response.setPlainText("Asking ELI…")

        from pathlib import Path as _P
        img_name = _P(img_path).name
        # Give the user visible confirmation of which image is being discussed
        combined = (
            f"[Image: {img_path}]\n"
            f"The image is '{img_name}' (path: {img_path}).\n"
            f"{question}"
        )

        def _worker():
            try:
                backend = self._text_backend_ready(notify=False)
                if backend is None:
                    self._image_chat_sig.emit("Text backend not loaded.")
                    return
                context = self._resolve_dropped_attachments(combined, [])
                _ce = getattr(self, "_cognitive_engine", None)
                if _ce is not None:
                    try:
                        reply = _ce.process(
                            context,
                            conversation_history=list(self.conversation_history[-6:]),
                        )
                        reply = _eli_gui_visible_text(reply)
                    except Exception as _e:
                        reply = f"[CE error: {_e}]"
                else:
                    adapter = _GUIEngineAdapter(
                        backend=backend,
                        memory=self._central_memory,
                        max_tokens=-1,
                        temperature=self.temperature_input.value(),
                        n_ctx=_backend_context(backend),
                        inference_lock=self.__class__._inference_lock,
                        cognitive_engine=None,
                    )
                    reply = adapter.generate(context)
                self._image_chat_sig.emit(reply.strip())
            except Exception as e:
                self._image_chat_sig.emit(f"Error: {e}")

        import threading as _thr
        _thr.Thread(target=_worker, daemon=True, name="eli-img-chat").start()

    def use_recent_context_for_image_prompt(self):
        recent = [
            str(m.get("content", "")).strip()
            for m in reversed(self.conversation_history)
            if m.get("role") == "user" and str(m.get("content", "")).strip()
        ][:2]
        if not recent:
            QMessageBox.information(self, "No Context", "No recent user messages are available yet.")
            return
        recent.reverse()
        self.image_prompt_input.setPlainText("\n".join(recent))

    def _image_settings_snapshot(self) -> Dict[str, Any]:
        data = {}
        try:
            from eli.core.runtime_settings import load_settings as _rs_load
            data = _rs_load() or {}
        except Exception:
            data = {}
        if hasattr(self, "user_name_input"):
            data["user_name"] = self.user_name_input.text().strip()
        if hasattr(self, "image_style_profile_combo"):
            data["image_style_profile"] = self.image_style_profile_combo.currentText().strip()
        if hasattr(self, "image_palette_profile_combo"):
            data["image_palette_profile"] = self.image_palette_profile_combo.currentText().strip()
        if hasattr(self, "image_backend_default_combo"):
            data["image_backend"] = self.image_backend_default_combo.currentText().strip()
        elif hasattr(self, "image_backend_combo"):
            data["image_backend"] = self.image_backend_combo.currentText().strip()
        if hasattr(self, "image_model_path_input"):
            data["image_model_path"] = self.image_model_path_input.text().strip() or self._selected_image_model_path()
        elif hasattr(self, "image_model_combo"):
            data["image_model_path"] = self._selected_image_model_path()
        if hasattr(self, "image_device_default_combo"):
            data["image_device"] = self.image_device_default_combo.currentText().strip()
        elif hasattr(self, "image_device_combo"):
            data["image_device"] = self.image_device_combo.currentText().strip()
        if hasattr(self, "image_quality_default_combo"):
            data["image_quality_preset"] = self.image_quality_default_combo.currentText().strip()
        elif hasattr(self, "image_quality_preset_combo"):
            data["image_quality_preset"] = self.image_quality_preset_combo.currentText().strip()
        if hasattr(self, "image_steps_default_input"):
            data["image_steps"] = int(self.image_steps_default_input.value())
        elif hasattr(self, "image_steps_input"):
            data["image_steps"] = int(self.image_steps_input.value())
        if hasattr(self, "image_guidance_default_input"):
            data["image_guidance"] = float(self.image_guidance_default_input.value())
        elif hasattr(self, "image_guidance_input"):
            data["image_guidance"] = float(self.image_guidance_input.value())
        if hasattr(self, "image_negative_default_input"):
            data["image_negative_prompt"] = self.image_negative_default_input.toPlainText().strip()
        elif hasattr(self, "image_negative_input"):
            data["image_negative_prompt"] = self.image_negative_input.toPlainText().strip()
        if hasattr(self, "image_profile_notes_input"):
            data["image_profile_notes"] = self.image_profile_notes_input.toPlainText().strip()
        if hasattr(self, "image_auto_personalize_checkbox"):
            data["image_auto_personalize"] = bool(self.image_auto_personalize_checkbox.isChecked())
        if hasattr(self, "image_auto_open_checkbox"):
            data["image_auto_open"] = bool(self.image_auto_open_checkbox.isChecked())
        if hasattr(self, "image_use_chat_context_checkbox"):
            data["image_use_chat_context"] = bool(self.image_use_chat_context_checkbox.isChecked())
        if hasattr(self, "image_use_proactive_context_checkbox"):
            data["image_use_proactive_context"] = bool(self.image_use_proactive_context_checkbox.isChecked())
        if hasattr(self, "image_default_project_input"):
            data["image_default_project_path"] = self.image_default_project_input.text().strip()
        return data

    def generate_images_from_studio(self):
        prompt = self.image_prompt_input.toPlainText().strip()
        if not prompt and not self.image_chat_context_checkbox.isChecked():
            QMessageBox.information(self, "Prompt Required", "Enter an image prompt or enable recent chat context.")
            return
        if self.image_backend_combo.currentText().strip() == "diffusion" and not self._selected_image_model_path():
            QMessageBox.warning(
                self,
                "Diffusion Model Required",
                "Diffusion backend is selected, but no local model path is configured. Paste or browse a local SDXL / Flux / diffusion checkpoint first.",
            )
            return

        self.image_generate_btn.setEnabled(False)
        self.image_generate_btn.setText("Generating...")
        self.image_summary_display.setPlainText("Generating images with the local image engine...")
        self.status_signal.emit("🖼️ Image generation started")

        def worker():
            try:
                from eli.tools.image_engine import ImageGenerationRequest, generate_images

                settings = self._image_settings_snapshot()
                proactive_ground = {}
                if self.image_proactive_context_checkbox.isChecked():
                    proactive_ground = self._build_proactive_ground_truth(query=prompt)

                req = ImageGenerationRequest(
                    prompt=prompt,
                    project=self.image_project_input.text().strip(),
                    preset=self.image_preset_combo.currentText().strip(),
                    scene_type=self.image_scene_combo.currentText().strip(),
                    style=self.image_style_combo.currentText().strip(),
                    palette=self.image_palette_combo.currentText().strip(),
                    backend=self.image_backend_combo.currentText().strip(),
                    model=self._selected_image_model_path(),
                    device=self.image_device_combo.currentText().strip(),
                    steps=int(self.image_steps_input.value()),
                    guidance=float(self.image_guidance_input.value()),
                    negative=self.image_negative_input.toPlainText().strip(),
                    count=int(self.image_count_input.value()),
                    width=int(self.image_width_input.value()),
                    height=int(self.image_height_input.value()),
                    seed=self._resolve_image_seed(),
                    sheet=int(self.image_count_input.value()) > 1,
                    use_chat_context=bool(self.image_chat_context_checkbox.isChecked()),
                    use_proactive_context=bool(self.image_proactive_context_checkbox.isChecked()),
                    auto_personalize=bool(self.image_personalize_checkbox.isChecked()),
                )
                result = generate_images(
                    req,
                    settings,
                    proactive_ground=proactive_ground,
                    conversation_history=self.conversation_history if req.use_chat_context else [],
                )
                self.image_generation_done_signal.emit(result, settings)
            except Exception as e:
                self.image_generation_failed_signal.emit(str(e))

        threading.Thread(target=worker, daemon=True, name="image-studio-generate").start()

    def _image_generation_done(self, result, settings: Dict[str, Any]):
        self.image_generate_btn.setEnabled(True)
        self.image_generate_btn.setText("Generate Images")
        self.status_signal.emit(f"🖼️ Generated {len(result.saved_paths)} image(s)")

        self.image_prompt_context_display.setPlainText(
            "\n".join(result.personalization_notes) if result.personalization_notes else "No extra personalization applied."
        )
        summary_lines = [
            f"Generated {len(result.saved_paths)} image(s)",
            f"Output folder: {result.out_dir}",
            "",
            "Applied prompt:",
            result.applied_prompt,
        ]
        if result.contact_sheet:
            summary_lines.extend(["", f"Contact sheet: {result.contact_sheet}"])
        self.image_summary_display.setPlainText("\n".join(summary_lines))

        self.refresh_image_gallery()
        self._focus_generated_image_result(result)

        try:
            if self.memory_system:
                self.memory_system.store(
                    f"Generated {len(result.saved_paths)} images from prompt: {result.applied_prompt[:240]}",
                    tags=["image_generation", self.image_style_combo.currentText().strip(), self.image_scene_combo.currentText().strip()],
                    kind="creative",
                    source="image_studio",
                )
        except Exception as e:
            log.debug(f"[IMAGE] memory log failed: {e}")

        try:
            if self._proactive_daemon:
                self._proactive_daemon.suggestion_queue.put((
                    "image_generation",
                    {
                        "suggestion": f"Generated {len(result.saved_paths)} personalised images",
                        "command": result.saved_paths[0] if result.saved_paths else "",
                    },
                ))
        except Exception as e:
            log.debug(f"[IMAGE] proactive queue update failed: {e}")

        try:
            note = (
                f"<b>🖼 Image Studio</b><br>"
                f"Generated {len(result.saved_paths)} image(s). "
                f"Style bias: {settings.get('image_style_profile', 'auto')} · "
                f"Palette bias: {settings.get('image_palette_profile', 'auto')}."
            )
            self.suggestions_display.append(note)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _image_generation_failed(self, error_text: str):
        self.image_generate_btn.setEnabled(True)
        self.image_generate_btn.setText("Generate Images")
        self.image_summary_display.setPlainText(f"Image generation failed:\n{error_text}")
        self.status_signal.emit("❌ Image generation failed")
