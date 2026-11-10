"""ScreenCaptureMixin — 7 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class ScreenCaptureMixin:
    def _sc_capture(self):
        """Take a screenshot in a worker thread, show preview on main thread.

        The capture is os_controller.take_screenshot's, the one that knows every OS and desktop and
        which way worked here; a failure shows what each way it tried did. A second Linux-only chain
        (mss, scrot, gnome-screenshot, import) used to run after it into a temp file that was never
        removed, leaving copies of the screen in the temp folder."""
        self._sc_capture_btn.setEnabled(False)
        self._sc_capture_btn.setText("Capturing…")

        def worker():
            try:
                from eli.perception.os_controller import take_screenshot

                captured = take_screenshot("full", keep=False) or {}
                cap_path = str(captured.get("path") or "")
                if captured.get("ok") and cap_path:
                    self._sc_screenshot_path = cap_path
                    self._sc_capture_sig.emit(cap_path)
                    return
                self._sc_capture_sig.emit(f"ERROR:{captured.get('error') or captured.get('response') or ''}")
            except Exception as exc:
                self._sc_capture_sig.emit(f"ERROR:{exc}")

        # Connect once
        try:
            self._sc_capture_sig.disconnect()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self._sc_capture_sig.connect(self._sc_on_capture, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=worker, daemon=True).start()

    def _sc_on_capture(self, path: str):
        self._sc_capture_btn.setEnabled(True)
        self._sc_capture_btn.setText("📸 Capture Full Screen")
        if not path or path.startswith("ERROR:"):
            self._sc_preview.setText(f"❌ Capture failed: {path[len('ERROR:'):] if path else ''}")
            return
        pix = QPixmap(path)
        if not pix.isNull():
            scaled = pix.scaled(self._sc_preview.width() or 640,
                                 160,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            self._sc_preview.setPixmap(scaled)
        else:
            self._sc_preview.setText(f"Screenshot saved: {path}")
        self._sc_ocr_btn.setEnabled(True)
        # A screenshot alone is enough for vision analysis (no OCR needed first).
        self._sc_analyse_btn.setEnabled(True)
        if hasattr(self, "_sc_deep_btn"):
            self._sc_deep_btn.setEnabled(True)

    def _sc_run_ocr(self):
        if not self._sc_screenshot_path:
            self._sc_ocr_text.setPlainText("No screenshot captured yet.")
            return
        self._sc_ocr_btn.setEnabled(False)
        self._sc_ocr_btn.setText("Running OCR…")
        path = self._sc_screenshot_path

        def worker():
            import subprocess, shutil
            text = ""

            # ── 1. tesseract CLI — fast, no Python binding needed ──────────
            tess = shutil.which("tesseract")
            if tess:
                try:
                    r = subprocess.run(
                        [tess, path, "stdout", "--psm", "11", "-l", "eng"],
                        capture_output=True, text=True, timeout=25,
                    )
                    text = r.stdout.strip()
                except subprocess.TimeoutExpired:
                    text = "(tesseract timed out)"
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            # ── 2. pytesseract binding (also calls tesseract binary) ────────
            if not text:
                try:
                    import pytesseract
                    from PIL import Image
                    img = Image.open(path)
                    # Downscale for speed if > 2 MP
                    w, h = img.size
                    if w * h > 2_000_000:
                        scale = (2_000_000 / (w * h)) ** 0.5
                        img = img.resize((int(w * scale), int(h * scale)))
                    text = pytesseract.image_to_string(img, config="--psm 11").strip()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            # ── 3. Not installed ────────────────────────────────────────────
            if not text:
                text = (
                    "⚠️  No OCR engine found.\n\n"
                    "Install tesseract (fast, free, offline):\n"
                    "  Ubuntu/Debian:  sudo apt install tesseract-ocr\n"
                    "  macOS:          brew install tesseract\n"
                    "  Windows:        choco install tesseract\n\n"
                    "Then restart ELI and try again."
                )

            self._sc_ocr_done_sig.emit(text)

        threading.Thread(target=worker, daemon=True).start()

    def _sc_ocr_done(self, text: str):
        self._sc_ocr_btn.setEnabled(True)
        self._sc_ocr_btn.setText("🔍 Run OCR")
        self._sc_ocr_text.setPlainText(text.strip() or "(no text detected)")
        self._sc_analyse_btn.setEnabled(bool(text.strip()))

    def _sc_ask_eli(self, deep: bool = False):
        backend = self._text_backend_ready(notify=False)
        if backend is None:
            self._sc_response.setPlainText("⚠️ Load a model first.")
            return
        ocr_text = self._sc_ocr_text.toPlainText().strip()
        ss_path = getattr(self, "_sc_screenshot_path", "") or ""
        import os as _os_sc
        _has_shot = bool(ss_path and _os_sc.path.exists(ss_path))
        if not _has_shot and not ocr_text:
            self._sc_response.setPlainText("⚠️ Capture the screen first (or paste text to analyse).")
            return
        self._sc_analyse_btn.setEnabled(False)
        self._sc_analyse_btn.setText("Analysing…")
        if hasattr(self, "_sc_deep_btn"):
            self._sc_deep_btn.setEnabled(False)
        self._sc_response.setPlainText(
            "🤖 ELI is performing a deep local screen audit…" if deep
            else ("🤖 ELI is looking at the screen…" if _has_shot
                  else "🤖 ELI is analysing the text…"))

        def worker():
            try:
                _ocr = ocr_text
                if _ocr.startswith("⚠") or "No OCR engine" in _ocr:
                    _ocr = ""
                if _has_shot:
                    from eli.execution.executor_enhanced import execute as _vexec
                    from eli.perception.screen_analysis import build_screen_analysis_prompt
                    depth = "deep" if deep else str(
                        getattr(self, "_sc_depth_combo", None) and self._sc_depth_combo.currentData() or "standard"
                    )
                    _prompt = build_screen_analysis_prompt("", depth=depth)
                    if _ocr:
                        _prompt += f"\n\nOCR text already extracted (use as ground truth):\n{_ocr[:2500]}"
                    res = _vexec("ANALYZE_IMAGE", {
                        "path": ss_path,
                        "prompt": _prompt,
                        "prefer_fast": depth == "quick",
                    }) or {}
                    resp = str(res.get("content") or res.get("response") or "").strip()
                    if res.get("ok") is False:
                        # the analysis's own reason (no vision model, no projector...), not a stock line
                        resp = f"❌ {str(res.get('error') or resp or 'the image analysis failed').strip()}"
                    elif not resp:
                        resp = "I captured the screen but couldn't produce a description."
                else:
                    # No screenshot — analyse the pasted/typed text with the text model.
                    prompt = (
                        "You are ELI, a helpful AI assistant. The following text was extracted "
                        "from the user's screen. Analyse it and give a helpful summary, "
                        "identifying any important information, errors, or actions to take.\n\n"
                        f"SCREEN TEXT:\n{_ocr[:3000]}\n\n"
                        "Analysis:"
                    )
                    resp = backend.generate(prompt=prompt, max_tokens=512, temperature=0.6)
                self._sc_eli_done_sig.emit(resp)
            except Exception as exc:
                self._sc_eli_done_sig.emit(f"❌ Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _sc_eli_done(self, text: str):
        self._sc_analyse_btn.setEnabled(True)
        self._sc_analyse_btn.setText("🤖 Ask ELI")
        if hasattr(self, "_sc_deep_btn"):
            self._sc_deep_btn.setEnabled(True)
        self._sc_response.setPlainText(text)

    def _sc_clear(self):
        self._sc_preview.setText("(no screenshot yet)")
        self._sc_ocr_text.clear()
        self._sc_response.clear()
        self._sc_screenshot_path = ""
        self._sc_ocr_btn.setEnabled(False)
        self._sc_analyse_btn.setEnabled(False)
