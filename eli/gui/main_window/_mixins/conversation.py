"""ConversationMixin — 25 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403
from eli.gui.main_window._common import _backend_context
from eli.gui.main_window.adapters import _GUIEngineAdapter


class ConversationMixin:
    def _speak_response(self, text: str):
        import re as _re, threading, unicodedata
        if not text or not isinstance(text, str): return
        printable = sum(1 for c in text if unicodedata.category(c)[0] != 'C')
        if printable / max(len(text), 1) < 0.8: return
        try:
            from eli.cognition.reasoning_modes import apply_final_reasoning_contract as _rm_final
            text = _rm_final(text)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        clean = _re.sub(r'[*_`#>|\[\]~]', '', text)
        clean = _re.sub(r'\s+', ' ', clean).strip()
        try:
            max_chars = int(os.environ.get("ELI_TTS_MAX_RESPONSE_CHARS", "12000") or "12000")
        except Exception:
            max_chars = 12000
        if max_chars > 0 and len(clean) > max_chars:
            clean = clean[:max_chars].rsplit(" ", 1)[0].rstrip() or clean[:max_chars].rstrip()
        if not clean: return
        if self._voice_worker is not None:
            self._voice_worker.speak(clean)
            return
        def _run():
            try:
                from eli.perception.tts_router import speak as _tts_speak, get_active_voice
                _tts_speak(clean, voice_name=get_active_voice())
            except Exception as e:
                log.debug(f'[TTS] {e}')
        threading.Thread(target=_run, daemon=True).start()

    def _interrupt_speech(self):
        """Interrupt any in-progress TTS via streaming VoiceWorker."""
        if self._voice_worker is not None:
            self._voice_worker.interrupt()

    def _speak_last_response(self):
        self._speak_response(getattr(self, '_last_eli_response', ''))

    def _on_auto_speak_toggled(self, checked: bool):
        self._tts_auto = checked
        try: self.auto_speak_btn.setText('🔊 Auto-Speak: ON' if checked else '🔇 Auto-Speak: OFF')
        except Exception: pass

    def _on_voice_changed(self, name: str):
        if not name or name == "(no voices)":
            return
        try:
            from eli.perception.tts_router import set_active_voice, resolve_voice_id
            vid = resolve_voice_id(name)
            if getattr(self, "voice_combo", None) is not None:
                data = self.voice_combo.currentData()
                if data:
                    vid = str(data)
            set_active_voice(vid)
            # Mirror to the Settings tab selector if present
            sel = getattr(self, "_voice_selector", None)
            if sel is not None:
                for i in range(sel.count()):
                    if sel.itemData(i) == vid:
                        sel.blockSignals(True)
                        sel.setCurrentIndex(i)
                        sel.blockSignals(False)
                        break
        except Exception as ex:
            log.debug(f"[GUI] voice change failed: {ex}")

    def _on_stt_transcript(self, text: str):
        text = text.strip()
        if not text:
            return
        log.debug(f"[STT→GUI] {text}")
        # Existing-router/executor fast path for deterministic voice commands.
        # This avoids CognitiveEngine/LLM latency for safe actions.
        try:
            _voice_text = text  # function parameter — no need to search locals

            from eli.execution import router_enhanced as _eli_voice_router
            from eli.execution import executor_enhanced as _eli_voice_executor

            # Stage-0 instant dispatch: exact-match quick commands bypass the
            # full router pipeline entirely. Saves ~30-60ms per command and
            # ensures media controls fire as fast as possible.
            _INSTANT_DISPATCH = {
                "pause":          ("PAUSE_MEDIA",    {}),
                "play":           ("PLAY_MEDIA",     {}),
                "resume":         ("PLAY_MEDIA",     {}),
                "stop":           ("STOP_MEDIA",     {}),
                "next":           ("NEXT_MEDIA",     {}),
                "next song":      ("NEXT_MEDIA",     {}),
                "next track":     ("NEXT_MEDIA",     {}),
                "skip":           ("NEXT_MEDIA",     {}),
                "previous":       ("PREVIOUS_MEDIA", {}),
                "previous song":  ("PREVIOUS_MEDIA", {}),
                "previous track": ("PREVIOUS_MEDIA", {}),
                "back":           ("PREVIOUS_MEDIA", {}),
                "volume up":      ("VOLUME", {"direction": "up",   "delta": 15}),
                "volume down":    ("VOLUME", {"direction": "down", "delta": 15}),
                "louder":         ("VOLUME", {"direction": "up",   "delta": 15}),
                "quieter":        ("VOLUME", {"direction": "down", "delta": 15}),
                "mute":           ("VOLUME", {"direction": "mute"}),
                "unmute":         ("VOLUME", {"direction": "unmute"}),
            }
            _vt_lower = _voice_text.lower().strip()
            import re as _re_vol
            _vol_pct = _re_vol.fullmatch(
                r"(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?",
                _vt_lower.replace(" percent", "%").replace(" per cent", "%"),
            )
            if _vol_pct:
                _pct = max(0, min(100, int(_vol_pct.group(1))))
                _route = {
                    "action": "VOLUME",
                    "args": {"level": _pct, "percent": _pct, "mode": "absolute"},
                    "confidence": 0.999,
                    "meta": {"matched_by": "gui.instant_dispatch.volume_absolute"},
                }
            elif _vt_lower in _INSTANT_DISPATCH:
                _action, _args = _INSTANT_DISPATCH[_vt_lower]
                _route = {"action": _action, "args": _args,
                          "confidence": 1.0,
                          "meta": {"matched_by": "gui.instant_dispatch"}}
            else:
                _route = _eli_voice_router.route(_voice_text)
                _action = str((_route or {}).get("action") or "").upper()
                _args = (_route or {}).get("args") or {}

            _direct_actions = {
                "NOOP",
                "TIME", "DATE",
                "PAUSE_MEDIA", "PLAY_MEDIA", "STOP_MEDIA",
                "NEXT_MEDIA", "PREVIOUS_MEDIA",
                "MEDIA_CONTROL",
                "VOLUME", "VOLUME_UP", "VOLUME_DOWN",
                "SCREENSHOT", "LIST_DIR", "READ_FILE",
                "GET_WEATHER", "SET_TIMER", "SET_ALARM",
                "PROACTIVE_STATUS", "HABIT_STATUS", "LIST_CAPABILITIES",
                # ELI_GUI_TILE_DIRECT_EXEC_FIX_20260505
                "TILE_WINDOWS", "MINIMISE_ALL", "RESTORE_WINDOWS",
                "OPEN_APP", "CLOSE_APP",
                # Remediation confirm/cancel must bypass GGUF synthesis
                "CONFIRM_PENDING_REMEDIATION", "CANCEL_PENDING_REMEDIATION",
                "CHECK_TARGET_STATUS", "EXPLAIN_LAST_FAILURE",
                # News: pre-formatted headlines; LLM path causes CUDA OOM (10k+ token prompt)
                "NEWS_FETCH",
            }

            if _action in _direct_actions:
                _user_voice_text = str(_voice_text or text or "").strip()
                _session_id = "gui-direct"
                _user_id = "local-user"
                try:
                    _ce = getattr(self, "_cognitive_engine", None)
                    _session_id = str(getattr(_ce, "session_id", "") or _session_id)
                    _user_id = str(getattr(_ce, "user_id", "") or _user_id)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                try:
                    if _user_voice_text:
                        _uc = getattr(self, '_user_text_color', '#a3be8c')
                        if hasattr(self, "chat_display"):
                            self.chat_display.append(
                                f'\n<b><span style="color:{_uc};">🧑 You [{now_hms()}]:</span></b>'
                                f'<br>{_user_voice_text}<br>'
                            )
                        if hasattr(self, "conversation_history"):
                            self.conversation_history.append({'role': 'user', 'content': _user_voice_text})
                        try:
                            if getattr(self, "_central_memory", None):
                                self._central_memory.add_conversation_turn(
                                    "user", _user_voice_text, session_id=_session_id, user_id=_user_id)
                        except Exception as _mem_user_e:
                            log.debug(f"[GUI_DIRECT_EXEC][MEM_USER_FAIL] {_mem_user_e}")
                        log.debug(f"[GUI_DIRECT_EXEC][USER_APPEND] {_user_voice_text}")
                except Exception as _user_ui_e:
                    log.debug(f"[GUI_DIRECT_EXEC][USER_APPEND_FAIL] {_user_ui_e}")

                log.debug(f"[GUI_DIRECT_EXEC] route={_route}")
                _direct_t0 = time.perf_counter()
                _res = _eli_voice_executor.execute_action(_action, _args)
                _ok = bool((_res or {}).get("ok", True)) if isinstance(_res, dict) else True

                if isinstance(_res, dict):
                    _reply = str(_res.get("response") or _res.get("content") or _res.get("message") or _res)
                else:
                    _reply = str(_res)

                log.debug(f"[GUI_DIRECT_EXEC] {_reply}")
                # This path skips CognitiveEngine.process(), so it writes its own
                # audit row. NOOP is the fragment guard, not an action.
                if _action != "NOOP":
                    try:
                        from eli.runtime import orchestrator_audit_ledger as _oal
                        _why = "ok"
                        if not _ok:
                            _why = str((_res.get("error") if isinstance(_res, dict) else "") or "failed")
                        _oal.record_direct_action(
                            action=_action, ok=_ok, outcome=_why,
                            source="voice_direct", session_id=_session_id, user_id=_user_id,
                            elapsed_ms=(time.perf_counter() - _direct_t0) * 1000.0)
                    except Exception:
                        log.debug("[GUI_DIRECT_EXEC] audit row failed", exc_info=True)
                # NOOP (fragment guard) responses must not enter conversation
                # history, be spoken, or be displayed — they are internal
                # routing events.  Storing the fragment-guard JSON as an
                # assistant turn poisons subsequent LLM context, causing the
                # model to mimic the JSON format in real responses.
                _is_noop = (_action == "NOOP")
                try:
                    if getattr(self, "_central_memory", None):
                        if _reply and not _is_noop:
                            self._central_memory.add_conversation_turn(
                                "assistant", _reply, session_id=_session_id, user_id=_user_id)
                        self._central_memory.log_learning_event(
                            "gui_direct_command",
                            input_text=_user_voice_text,
                            output_text=_reply,
                            action=_action,
                            outcome="ok" if _ok else "failed",
                            reward=1.0 if _ok else -1.0,
                            metadata={
                                "args": _args,
                                "matched_by": (_route.get("meta") or {}).get("matched_by") if isinstance(_route, dict) else "",
                                "source": "gui_direct_exec",
                            },
                        )
                        self._central_memory.log_habit_event(
                            "command_result",
                            {
                                "action": _action,
                                "args": _args,
                                "ok": _ok,
                                "source": "gui_direct_exec",
                            },
                        )
                        if _action == "OPEN_APP" and _ok:
                            _app_name = str(
                                _args.get("name") or _args.get("target") or _args.get("app") or ""
                            ).strip()
                            if _app_name:
                                _cmd = (_res.get("cmd") or _res.get("command") or _app_name) if isinstance(_res, dict) else _app_name
                                _method = (_res.get("method") if isinstance(_res, dict) else "") or "gui_direct_exec"
                                self._central_memory.store_app_cmd(_app_name, _cmd, _method)
                                self._central_memory.log_habit_event(
                                    "app_launch",
                                    {"app": _app_name, "cmd": _cmd, "method": _method, "success": True},
                                )
                        elif _action in {"LIST_DIR", "READ_FILE"} and _ok:
                            _path = str(_args.get("path") or "").strip()
                            if _path:
                                self._central_memory.log_habit_event(
                                    _action.lower(),
                                    {"path": _path, "success": True, "source": "gui_direct_exec"},
                                )
                except Exception as _mem_result_e:
                    log.debug(f"[GUI_DIRECT_EXEC][MEM_RESULT_FAIL] {_mem_result_e}")
                # Honour the Auto-Speak toggle: only emit TTS when the user
                # has it enabled. Voice-initiated commands also opt in via
                # the _last_input_was_voice flag — direct-exec replies to
                # text typed in the chat box should stay silent unless
                # auto-speak is on.
                _tts_auto_on = bool(getattr(self, "_tts_auto", False))
                _speak_text = str(_reply or "").strip()
                # NOOP: never speak
                if _is_noop:
                    _speak_text = ""
                # NEWS_FETCH: a synthesised briefing (prose) is spoken IN FULL.
                # Only the legacy raw bullet list ("• [Source] Title") is
                # condensed to "Source: Title" lines for speech.
                elif _action == "NEWS_FETCH" and _speak_text:
                    _is_briefing = False
                    try:
                        _is_briefing = (
                            isinstance(_res, dict)
                            and (_res.get("meta") or {}).get("response_mode") == "news_briefing"
                        )
                    except Exception:
                        _is_briefing = False
                    if not _is_briefing and "• [" in _speak_text:
                        try:
                            import re as _re_n
                            _hdr_m = _re_n.search(r"\((\d+\s+fetched[^)]*)\)", _speak_text, _re_n.I)
                            _hdr = _hdr_m.group(1) if _hdr_m else ""
                            _hl = _re_n.findall(r"•\s+\[([^\]]+)\](?:\s+\([^)]+\))?\s+(.+)", _speak_text)
                            _parts = []
                            if _hdr:
                                _parts.append(_hdr.replace(",", ","))
                            for src, title in _hl:
                                _parts.append(f"{src}: {title.strip().rstrip('…')}")
                            if _parts:
                                _speak_text = ". ".join(_parts)
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                    # else: synthesised briefing — leave _speak_text full
                # For remediation previews, drop the bash script body before TTS
                # so the mic doesn't pick up hundreds of chars of shell code.
                elif _action == "CONFIRM_PENDING_REMEDIATION" and _speak_text:
                    for _cut_marker in ("Exact command", "```", "## Command", "Command(s):", "Commands:"):
                        _cut_idx = _speak_text.find(_cut_marker)
                        if _cut_idx > 0:
                            _speak_text = _speak_text[:_cut_idx].strip() + " — confirm repair?"
                            break
                # GUI append first so text is visible before TTS begins.
                # NOOP is silent — no chat display.
                if not _is_noop:
                    try:
                        if hasattr(self, "append_assistant_message"):
                            self.append_assistant_message(_reply)
                        elif hasattr(self, "add_assistant_message"):
                            self.add_assistant_message(_reply)
                        elif hasattr(self, "_append_assistant_message"):
                            self._append_assistant_message(_reply)
                        elif hasattr(self, "chat_display"):
                            self.chat_display.append(f"🤖 ELI:\n{_reply}")
                        # Flush Qt paint queue so text renders before TTS blocks the thread
                        try:
                            QApplication.processEvents()
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                    except Exception as _ui_e:
                        log.debug(f"[GUI_DIRECT_EXEC][UI_APPEND_FAIL] {_ui_e}")

                if _tts_auto_on and _speak_text:
                    try:
                        from eli.perception.tts_router import speak_text as _eli_gui_direct_speak
                        _eli_gui_direct_speak(_speak_text)
                        log.debug(f"[GUI_DIRECT_EXEC][TTS] {_speak_text}")
                    except Exception as _tts_e:
                        log.debug(f"[GUI_DIRECT_EXEC][TTS_FAIL] {_tts_e}")
                elif _speak_text:
                    log.debug(f"[GUI_DIRECT_EXEC][TTS_SKIPPED] auto-speak off")

                return
        except Exception as _direct_e:
            log.debug(f"[GUI_DIRECT_EXEC][FALLBACK] {type(_direct_e).__name__}: {_direct_e}")
        # Avoid queueing open-ended chat through the 35B path while a turn is still
        # generating — voice media commands should stay instant; chat can wait.
        try:
            from eli.cognition.inference_broker import foreground_recently_active
            if foreground_recently_active(window=8.0):
                _short = len(str(text or "").split()) <= 6
                if _short:
                    log.debug("[STT→GUI] deferred — inference busy (short utterance dropped)")
                    return
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self.chat_input.setPlainText(text)
        self.send_message()

    def _populate_mic_device_combo(self, restore: bool = False):
        """Populate mic selector with ALSA devices + PulseAudio/PipeWire BT sources."""
        prev = self.mic_device_combo.currentData() if restore else None
        self.mic_device_combo.blockSignals(True)
        self.mic_device_combo.clear()

        # Default / system choice
        self.mic_device_combo.addItem("System default", ("alsa", None))

        # ALSA devices via SpeechRecognition
        #
        # list_microphone_names() builds a PyAudio instance, and PortAudio probes
        # every ALSA plugin and the JACK server on the way — ~28 lines of
        # "unable to open slave" / "jack server is not running" / JackShmReadWritePtr
        # straight from C. This populates a Settings dropdown during GUI
        # construction, so that wall of red was the first thing a user saw on a
        # completely healthy launch. audio_stt and mic_resolver already wrap their
        # own PyAudio calls for exactly this; this one was missed, and it is the
        # only remaining unguarded caller.
        try:
            import speech_recognition as _sr
            from eli.utils.native_io import quiet_native_stderr
            with quiet_native_stderr():
                _mic_names = list(_sr.Microphone.list_microphone_names())
            for idx, name in enumerate(_mic_names):
                if any(skip in name.lower() for skip in ("monitor", "iec958", "spdif", "a52",
                                                          "speex", "upmix", "vdownmix", "samplerate",
                                                          "lavrate", "hdmi", "nvidia")):
                    continue
                self.mic_device_combo.addItem(f"{name}", ("alsa", idx))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # PulseAudio / PipeWire sources (catches BT headsets)
        try:
            import subprocess as _sp
            if shutil.which("pactl"):
                _out = _sp.check_output(["pactl", "list", "sources", "short"],
                                        text=True, timeout=3)
                for line in _out.strip().splitlines():
                    parts = line.split("\t")
                    if len(parts) < 2:
                        continue
                    src_name = parts[1].strip()
                    # Skip monitors and virtual sinks
                    if ".monitor" in src_name:
                        continue
                    label = src_name
                    # Make BT sources human-readable
                    if src_name.startswith("bluez_input"):
                        label = f"Bluetooth: {src_name.split('.')[1].replace('_', ':')}"
                    elif "alsa_input" in src_name:
                        continue  # already covered by ALSA list above
                    self.mic_device_combo.addItem(label, ("pulse", src_name))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        self.mic_device_combo.blockSignals(False)

        # Restore previous selection or load from settings
        saved = prev or getattr(self, "_saved_mic_device", None)
        if saved:
            for i in range(self.mic_device_combo.count()):
                if self.mic_device_combo.itemData(i) == saved:
                    self.mic_device_combo.setCurrentIndex(i)
                    break

    def _apply_stt_sensitivity(self):
        """Push energy threshold / dynamic flag into the live recognizer and env."""
        import os as _os
        dynamic = self.dynamic_energy_checkbox.isChecked()
        threshold = self.energy_threshold_input.value()
        _os.environ["ELI_STT_DYNAMIC_ENERGY"] = "1" if dynamic else "0"
        _os.environ["ELI_STT_ENERGY_THRESHOLD"] = str(threshold)
        # Apply to live recognizer if STT is running
        try:
            from eli.perception.audio_stt import _get_recognizer
            rec = _get_recognizer()
            if rec is not None:
                rec.dynamic_energy_threshold = dynamic
                if not dynamic:
                    rec.energy_threshold = float(threshold)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        if getattr(self, "_first_run_complete", False):
            self.save_settings(silent=True)

    def _on_mic_device_changed(self, _save: bool = True):
        """Apply mic device selection to env vars immediately."""
        import os as _os
        data = self.mic_device_combo.currentData()
        if not data:
            return
        kind, value = data
        if kind == "alsa":
            if value is None:
                _os.environ.pop("ELI_MIC_DEVICE_INDEX", None)
                _os.environ.pop("PULSE_SOURCE", None)
            else:
                _os.environ["ELI_MIC_DEVICE_INDEX"] = str(value)
                _os.environ.pop("PULSE_SOURCE", None)
        elif kind == "pulse":
            _os.environ["ELI_MIC_DEVICE_INDEX"] = "14"
            _os.environ["PULSE_SOURCE"] = value
        self._saved_mic_device = data
        # Don't save during load_settings — caller sets _save=False to avoid
        # triggering a model reload cycle on startup.
        if _save and getattr(self, "_first_run_complete", False):
            self.save_settings(silent=True)

    def _apply_direct_chat_env(self):
        """Sync ELI_STT_ALLOW_DIRECT_CHAT env var from the checkbox — takes effect immediately."""
        import os as _os
        val = "1" if self.allow_direct_chat_checkbox.isChecked() else "0"
        _os.environ["ELI_STT_ALLOW_DIRECT_CHAT"] = val
        # Keep wake word button in sync with the settings-page checkbox
        if hasattr(self, "wake_word_btn"):
            self.wake_word_btn.blockSignals(True)
            self.wake_word_btn.setChecked(not self.allow_direct_chat_checkbox.isChecked())
            self.wake_word_btn.setText("Wake: ON" if self.wake_word_btn.isChecked() else "Wake: OFF")
            self.wake_word_btn.blockSignals(False)
        self.save_settings(silent=True)

    def _stt_toggle(self, checked: bool):
        if checked:
            self.stt_btn.setText("🔴 Mic: ON")
            def _cb(text):
                text = (text or "").strip()
                if text:
                    log.debug(f"[STT] emitting: {text}")
                    self.stt_transcript.emit(text)
            try:
                from eli.perception.audio_stt import start_audio_listening, stop_audio_listening
                start_audio_listening(callback=_cb)
                self._stt_stop_ref = stop_audio_listening
                log.debug("[STT] listening started — say: computer, <command>")
            except Exception as e:
                log.debug(f"[STT] start failed: {e}")
                self.stt_btn.setChecked(False)
                self.stt_btn.setText("🎤 Mic: OFF")
        else:
            self.stt_btn.setText("🎤 Mic: OFF")
            try:
                fn = getattr(self, "_stt_stop_ref", None)
                if fn:
                    fn()
                log.debug("[STT] listening stopped")
            except Exception as e:
                log.debug(f"[STT] stop failed: {e}")

    def _extract_image_prompt_from_chat(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        patterns = [
            r"^(?:please\s+)?(?:create|generate|make|draw|paint|render|design)\s+(?:me\s+)?(?:an?\s+)?image(?:\s+of|\s+showing|\s+with|\s+for)?\s+(.+)$",
            r"^(?:please\s+)?(?:create|generate|make|draw|paint|render|design)\s+(?:an?\s+)?(?:portrait|logo|poster|wallpaper|icon|illustration|artwork|cover)\s+(?:of|for)?\s+(.+)$",
            r"^(?:an?\s+)?image\s+of\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, raw, re.I)
            if match:
                prompt = match.group(1).strip(" .")
                prompt = re.sub(r"^(?:an?\s+image\s+of\s+)", "", prompt, flags=re.I)
                if prompt:
                    return prompt
        return ""

    def _start_chat_image_request(self, prompt: str, *, source_text: str = ""):
        if not prompt:
            return
        backend_choice = self.image_backend_combo.currentText().strip() if hasattr(self, "image_backend_combo") else "auto"
        if backend_choice == "diffusion" and not self._selected_image_model_path():
            self.chat_response_signal.emit(
                "❌ Diffusion backend selected, but no image model path is configured. Set one in Image Studio or Settings → Identity."
            )
            return
        if hasattr(self, "image_generate_btn") and not self.image_generate_btn.isEnabled():
            self.chat_response_signal.emit("🖼️ Image generation is already running. Wait for it to finish, then send the next prompt.")
            return

        self._chat_image_prompt = prompt
        self._chat_image_source_text = source_text or prompt
        self.open_image_studio_tab()
        if hasattr(self, "image_prompt_input"):
            self.image_prompt_input.setPlainText(prompt)
        if hasattr(self, "image_prompt_context_display"):
            self.image_prompt_context_display.setPlainText("Chat request received. Generating a subject-focused image now…")
        if hasattr(self, "image_summary_display"):
            self.image_summary_display.setPlainText("Generating image from chat request…")
        if hasattr(self, "image_generate_btn"):
            self.image_generate_btn.setEnabled(False)
            self.image_generate_btn.setText("Generating...")

        self.chat_response_signal.emit(f"🖼️ Generating an image for: {prompt}")
        self.status_signal.emit("🖼️ Generating image from chat request…")

        def worker():
            try:
                from eli.tools.image_engine import ImageGenerationRequest, generate_images

                settings = self._image_settings_snapshot()
                proactive_ground = {}
                if hasattr(self, "image_proactive_context_checkbox") and self.image_proactive_context_checkbox.isChecked():
                    proactive_ground = self._build_proactive_ground_truth(query=prompt)

                width = int(getattr(self, "image_width_input", None).value()) if hasattr(self, "image_width_input") else 1408
                height = int(getattr(self, "image_height_input", None).value()) if hasattr(self, "image_height_input") else 896
                req = ImageGenerationRequest(
                    prompt=prompt,
                    project=self.image_project_input.text().strip() if hasattr(self, "image_project_input") else "",
                    preset=self.image_preset_combo.currentText().strip() if hasattr(self, "image_preset_combo") else "",
                    scene_type="auto",
                    style=self.image_style_combo.currentText().strip() if hasattr(self, "image_style_combo") else "auto",
                    palette=self.image_palette_combo.currentText().strip() if hasattr(self, "image_palette_combo") else "auto",
                    backend=self.image_backend_combo.currentText().strip() if hasattr(self, "image_backend_combo") else "auto",
                    model=self._selected_image_model_path() if hasattr(self, "image_model_combo") else "",
                    device=self.image_device_combo.currentText().strip() if hasattr(self, "image_device_combo") else "auto",
                    steps=int(self.image_steps_input.value()) if hasattr(self, "image_steps_input") else 36,
                    guidance=float(self.image_guidance_input.value()) if hasattr(self, "image_guidance_input") else 7.2,
                    negative=self.image_negative_input.toPlainText().strip() if hasattr(self, "image_negative_input") else "",
                    count=1,
                    width=width,
                    height=height,
                    seed=self._resolve_image_seed(),
                    prefix="eli_chat",
                    sheet=False,
                    manifest=False,
                    save_specs=True,
                    use_chat_context=False,
                    use_proactive_context=bool(getattr(self, "image_proactive_context_checkbox", None).isChecked()) if hasattr(self, "image_proactive_context_checkbox") else False,
                    auto_personalize=bool(getattr(self, "image_personalize_checkbox", None).isChecked()) if hasattr(self, "image_personalize_checkbox") else True,
                )
                result = generate_images(
                    req,
                    settings,
                    proactive_ground=proactive_ground,
                    conversation_history=[],
                )
                self.chat_image_generation_done_signal.emit(result, settings, prompt)
            except Exception as e:
                self.chat_image_generation_failed_signal.emit(str(e))

        threading.Thread(target=worker, daemon=True, name="image-chat-generate").start()

    def _chat_image_generation_done(self, result, settings: Dict[str, Any], prompt: str):
        self._image_generation_done(result, settings)
        message = f"🖼️ Generated {len(result.saved_paths)} image(s) for: {prompt}"
        if result.saved_paths:
            message += f"\nSaved: {result.saved_paths[0]}"
        self.conversation_history.append({"role": "assistant", "content": message})
        self.chat_response_signal.emit(message)
        self._chat_image_prompt = ""
        self._chat_image_source_text = ""

    def _chat_image_generation_failed(self, error_text: str):
        self._image_generation_failed(error_text)
        message = f"❌ Image generation failed: {error_text}"
        self.conversation_history.append({"role": "assistant", "content": message})
        self.chat_response_signal.emit(message)
        self._chat_image_prompt = ""
        self._chat_image_source_text = ""

    def _on_image_chat_response(self, text: str):
        """Main-thread slot — updates the image chat response widget safely."""
        self.image_eli_response.setPlainText(text)
        if hasattr(self, "ask_img_btn_ref"):
            self.ask_img_btn_ref.setEnabled(True)

    def send_to_chat(self, text: str) -> None:
        """Submit `text` to the main chat programmatically so a panel's reply streams
        into the conversation (not the panel). Switches to the Chat tab and runs the
        normal send flow. Used by the Test & Review option buttons."""
        try:
            text = str(text or "").strip()
            if not text or getattr(self, "is_generating", False):
                return
            self.tabs.setCurrentIndex(0)            # Chat is the first/main tab
            self.chat_input.setPlainText(text)
            self.send_message()
        except Exception as e:
            log.debug(f"[GUI] send_to_chat failed: {e}")

    def _refresh_stt_diagnostics(self):
        """Read voice_profile.json + current recognizer state and render."""
        try:
            from pathlib import Path as _P
            import json as _json
            try:
                from eli.core.paths import get_paths
                vp_path = _P(get_paths().artifacts_dir) / "runtime" / "voice_profile.json"
                try:
                    from eli.kernel.state import get_active_user_id, _user_runtime_dir
                    uid = str(get_active_user_id("") or "").strip()
                    if uid and uid not in ("", "default-user"):
                        vp_path = _user_runtime_dir(uid) / "voice_profile.json"
                except Exception:
                    pass
            except Exception:
                vp_path = _P.home() / ".eli_voice_profile.json"

            profile = {}
            if vp_path.exists():
                try:
                    profile = _json.loads(vp_path.read_text(encoding="utf-8"))
                except Exception:
                    profile = {}

            count = int(profile.get("count", 0) or 0)
            mean = float(profile.get("energy_mean", 0.0) or 0.0)
            mn = float(profile.get("energy_min", 0.0) or 0.0)
            mx = float(profile.get("energy_max", 0.0) or 0.0)
            dmean = float(profile.get("duration_mean_s", 0.0) or 0.0)
            last_e = profile.get("last_energy", "?")
            last_d = profile.get("last_duration_s", "?")

            # Live recognizer state if present.
            live_threshold = "?"
            live_pause = "?"
            live_dynamic = "?"
            try:
                from eli.perception.audio_stt import get_audio_stt as _g
                stt_obj = _g()
                if stt_obj is not None:
                    rec = getattr(stt_obj, "recognizer", None)
                    if rec is not None:
                        live_threshold = f"{getattr(rec, 'energy_threshold', '?'):.0f}"
                        live_pause = f"{getattr(rec, 'pause_threshold', '?')}"
                        live_dynamic = str(getattr(rec, 'dynamic_energy_threshold', '?'))
            except Exception:
                log.debug("suppressed exception", exc_info=True)

            text = (
                f"Voice profile file: {vp_path}\n"
                f"Samples recorded:   {count}\n"
                f"Mean speech energy: {mean:.1f}    "
                f"min={mn:.1f}    max={mx:.1f}\n"
                f"Last sample:        energy={last_e}    duration={last_d}\n"
                f"Mean utterance dur: {dmean:.2f} s\n\n"
                f"--- Live recognizer ---\n"
                f"energy_threshold:   {live_threshold}\n"
                f"pause_threshold:    {live_pause}\n"
                f"dynamic_energy:     {live_dynamic}\n\n"
                f"After ~5 confirmed utterances, the threshold biases\n"
                f"toward your range (50%–90% of mean speech energy)."
            )
            try:
                from eli.perception.speaker_id import capabilities, list_speakers, get_active_speaker
                caps = capabilities()
                active = get_active_speaker()
                speakers = list_speakers()
                text += (
                    f"\n\n--- Speaker ID ---\n"
                    f"enabled:            {caps.get('enabled')}\n"
                    f"enrolled speakers:  {caps.get('enrolled_count')}\n"
                    f"active speaker:     {active.get('name') or '(none)'} "
                    f"(conf={active.get('confidence', 0):.2f})\n"
                )
                if speakers:
                    text += "profiles:           " + ", ".join(
                        s.get("name", "?") for s in speakers[:6]
                    ) + "\n"
                text += 'Enroll: say "enroll speaker <name>" with mic on.\n'
            except Exception:
                pass
            self._stt_diag_label.setText(text)
        except Exception as e:
            self._stt_diag_label.setText(f"Failed to read STT diagnostics: {e}")

    def _reset_voice_profile(self):
        try:
            from pathlib import Path as _P
            from eli.core.paths import get_paths
            vp_path = _P(get_paths().artifacts_dir) / "runtime" / "voice_profile.json"
            if vp_path.exists():
                vp_path.unlink()
            QMessageBox.information(
                self, "Voice Profile Reset",
                f"Removed {vp_path}. STT will recalibrate from ambient on next utterance."
            )
            self._refresh_stt_diagnostics()
        except Exception as ex:
            QMessageBox.warning(self, "Reset failed", str(ex))

    def _refresh_tts_diagnostics(self):
        try:
            from eli.perception.tts_router import (
                available_backends, get_active_voice, neural_fallback_state,
            )
            backends = available_backends()
            active = get_active_voice()
            voices = backends.get("piper_voices", [])
            # A `natural:`/`clone:` voice that cannot synthesise falls back to Piper.
            # This panel used to report only the SELECTED voice, so it read
            # "Active voice: natural:sophia" while every word was spoken by
            # en_US-amy-medium — and the model-file line showed a third, unrelated
            # voice. Say plainly what is actually coming out of the speakers.
            _fb = neural_fallback_state() or {}
            txt_lines = [
                f"Active voice:       {active}",
            ]
            if _fb.get("active"):
                txt_lines += [
                    f"  ⚠ NOT IN USE:     '{_fb.get('requested')}' cannot synthesise —",
                    f"                    {_fb.get('reason')}.",
                    f"  ▶ Actually speaking: {backends.get('default_voice') or '(piper default)'}",
                ]
            txt_lines += [
                f"Active model file:  {backends.get('active_model') or '(missing)'}",
                f"Piper binary:       {backends.get('piper_bin') or '(not found)'}",
                f"Installed voices:   {len(voices)} ({', '.join(voices[:6])}{'…' if len(voices) > 6 else ''})",
                f"pyttsx3 fallback:   {backends.get('pyttsx3')}",
                f"espeak-ng fallback: {backends.get('espeak_ng')}",
                f"espeak fallback:    {backends.get('espeak')}",
            ]
            self._tts_diag_label.setText("\n".join(txt_lines))
        except Exception as e:
            self._tts_diag_label.setText(f"Failed to read TTS diagnostics: {e}")

    def _test_tts_voice(self):
        try:
            from eli.perception.tts_router import speak_text
            speak_text("Voice test complete. STT and TTS are wired.")
            self._tts_diag_label.setText(
                self._tts_diag_label.text() + "\n\n[Test] sent 'Voice test complete' to TTS."
            )
        except Exception as ex:
            QMessageBox.warning(self, "TTS test failed", str(ex))


    def _on_send_or_stop(self):
        if getattr(self, "is_generating", False):
            self.stop_generation()
        else:
            self.send_message()

    def stop_generation(self):
        """Abort the in-flight model response so a new message can be sent."""
        if not getattr(self, "is_generating", False):
            return
        self._request_generation_cancel()
        self.status_signal.emit("Stopping generation…")

    def _request_generation_cancel(self):
        self._cancel_stream_requested = True
        try:
            from eli.cognition import gguf_inference as _ggi
            _ggi.request_cancel_generation()
        except Exception:
            log.debug("[GUI] request_cancel_generation failed", exc_info=True)
        try:
            _ce = getattr(self, "_cognitive_engine", None)
            if _ce is not None and hasattr(_ce, "cancel_generation"):
                _ce.cancel_generation()
        except Exception:
            log.debug("[GUI] cognitive engine cancel failed", exc_info=True)

    def send_message(self):
        user_message = self.chat_input.toPlainText().strip()
        if not user_message:
            return

        # Auto reasoning-mode selection (toggle on the bottom row controls it)
        try:
            self._maybe_auto_select_reasoning_mode(user_message)
        except Exception as _amx:
            log.debug(f"[GUI] auto reasoning-mode selection failed: {_amx}")

        # Resolve any drag-and-drop attachments into inline context
        dropped_paths = list(getattr(self.chat_input, "_dropped_paths", []))
        if dropped_paths or any(t in user_message for t in ("[Image:", "[File:", "[PDF:")):
            user_message = self._resolve_dropped_attachments(user_message, dropped_paths)
            if hasattr(self.chat_input, "_dropped_paths"):
                self.chat_input._dropped_paths.clear()

        _uc = getattr(self, '_user_text_color', '#a3be8c')
        self.chat_display.append(
            f'\n<b><span style="color:{_uc};">🧑 You [{now_hms()}]:</span></b>'
            f'<br>{user_message}<br>'
        )
        self.chat_input.clear()
        self.conversation_history.append({'role': 'user', 'content': user_message})

        # ── Agent wizard intercept ──────────────────────────────────────────
        if self._agent_wizard_state is not None:
            self._handle_wizard_input(user_message)
            return
        # ───────────────────────────────────────────────────────────────────

        image_prompt = self._extract_image_prompt_from_chat(user_message)
        if image_prompt:
            self._start_chat_image_request(image_prompt, source_text=user_message)
            return

        # Normal chat input always goes through CognitiveEngine.process().
        # Direct executor dispatch is reserved for explicit Quick Action buttons,
        # otherwise routed commands can bypass agent evidence and final synthesis.

        backend = self._text_backend_ready(notify=True)
        if backend is None:
            return

        # A prior worker's followthrough tail may still be running — stop it first.
        _prior_thread = getattr(self, "_generate_thread", None)
        if _prior_thread is not None and _prior_thread.is_alive():
            self._request_generation_cancel()
            _prior_thread.join(timeout=2.0)

        self.is_generating = True
        self._cancel_stream_requested = False
        try:
            from eli.cognition import gguf_inference as _ggi
            _ggi.clear_cancel_generation()
        except Exception:
            log.debug("[GUI] clear_cancel_generation failed", exc_info=True)
        try:
            from eli.cognition import expression_state as _es_think
            _es_think.set_thinking(True)  # face shows a focused "working" look
        except Exception:
            log.debug("[GUI] thinking-state set failed", exc_info=True)
        self.status_signal.emit('Generating')
        self.status_signal.emit('🔄 Generating response...')

        def generate_worker():
            try:
                max_tokens  = self.max_tokens_input.value()
                temperature = self.temperature_input.value()
                reasoning_mode = getattr(self, '_reasoning_mode', 'quick')
                n_ctx = _backend_context(backend)

                # ── CognitiveEngine-first runtime path ──
                adapter = _GUIEngineAdapter(
                    backend          = backend,
                    memory           = self._central_memory,
                    max_tokens       = max_tokens,
                    temperature      = temperature,
                    n_ctx            = n_ctx,
                    inference_lock   = self.__class__._inference_lock,
                    cognitive_engine = getattr(self, '_cognitive_engine', None),
                )

                _ce = getattr(self, '_cognitive_engine', None)
                _ce_ok = False
                _orchestrator_ok = False

                if _ce is not None:
                    log.debug("[GUI] PATH1 -> CognitiveEngine.process (orchestrator owned inside CE)")
                    try:
                        result = _ce.process(
                            user_message,
                            stream=True,
                            reasoning_mode=reasoning_mode,
                        )
                        _ce_ok = True
                    except Exception as _ce_err:
                        log.debug(f"[GUI] CognitiveEngine.process failed: {_ce_err}")
                        result = None

                if not _ce_ok:
                    log.debug("[GUI] PATH2 removed -> falling back to direct backend only if CognitiveEngine fails")
                    result = None

                full_tokens = []
                _response_streamed = False
                _storage_handled = False  # CognitiveEngine stores turns internally
                _was_cancelled = False
                try:
                    from eli.kernel.engine import MAIN_REPLY_DONE_SENTINEL as _MAIN_REPLY_DONE
                except Exception:
                    _MAIN_REPLY_DONE = "__MAIN_REPLY_DONE__"

                def _generation_cancelled() -> bool:
                    if getattr(self, "_cancel_stream_requested", False):
                        return True
                    try:
                        from eli.cognition import gguf_inference as _ggi
                        return _ggi.is_cancel_requested()
                    except Exception:
                        return False

                import types as _types

                if (_ce_ok or _orchestrator_ok) and result is not None:
                    if isinstance(result, (_types.GeneratorType,)) or hasattr(result, '__next__'):
                        # ── Streaming CHAT response ──
                        first_token = True
                        for token in result:
                            if _generation_cancelled():
                                _was_cancelled = True
                                break
                            token = str(token or "")
                            if not token:
                                continue
                            if token == _MAIN_REPLY_DONE:
                                # Visible reply done — don't make the user wait on followthrough.
                                self.is_generating = False
                                self.status_signal.emit('Send enabled')
                                self.status_signal.emit('🟢 Ready')
                                continue
                            full_tokens.append(token)
                            if first_token:
                                self.chat_response_signal.emit('__STREAM_START__')
                                first_token = False
                            self.chat_response_signal.emit(token)
                        if first_token:
                            # The engine's _stream_chat() handles all internal
                            # recovery (Stage 11 → direct GGUF fallback → fault message).
                            # A zero-token generator here means a truly silent action
                            # (e.g. VOLUME) that intentionally produces no visible output.
                            # Do NOT re-call process() — that would execute the action twice.
                            log.debug("[GUI] Stream generator produced zero tokens (silent action or suppressed output).")
                            response = ""
                            _response_streamed = True
                            _storage_handled = True
                        else:
                            if _was_cancelled:
                                self.chat_response_signal.emit('__STREAM_CANCELLED__')
                            else:
                                self.chat_response_signal.emit('__STREAM_END__')
                            _response_streamed = True
                            response = ''.join(full_tokens)
                        if _ce_ok:
                            # CognitiveEngine stores turns in its finalization paths.
                            _storage_handled = True
                    elif isinstance(result, dict):
                        # ── Action result or non-streaming CHAT ──
                        try:
                            self._generated_artifact_open_sig.emit(dict(result))
                        except Exception as _artifact_emit_err:
                            log.debug(f"[GUI] generated artifact open signal failed: {_artifact_emit_err}")
                        response = _eli_gui_visible_text(result)
                        if _ce_ok:
                            _storage_handled = True
                    else:
                        response = _eli_gui_visible_text(result)
                else:
                    # ── PATH 3: Direct backend fallback (all pipelines failed) ──
                    memory_context = self._retrieve_relevant_memories(user_message, limit=10)
                    system_prompt  = ELI_SYSTEM_PROMPT + self._get_mode_prefix()
                    if memory_context:
                        system_prompt += "\n\n" + memory_context
                    messages = [{'role': 'system', 'content': system_prompt}]
                    messages.extend(self.conversation_history[-10:])
                    with self.__class__._inference_lock:
                        if hasattr(backend, 'chat_stream'):
                            first_token = True
                            for token in backend.chat_stream(
                                    messages, max_tokens=max_tokens, temperature=temperature):
                                if _generation_cancelled():
                                    _was_cancelled = True
                                    break
                                full_tokens.append(token)
                                if first_token:
                                    self.chat_response_signal.emit('__STREAM_START__')
                                    first_token = False
                                self.chat_response_signal.emit(token)
                            if _was_cancelled:
                                self.chat_response_signal.emit('__STREAM_CANCELLED__')
                            else:
                                self.chat_response_signal.emit('__STREAM_END__')
                            _response_streamed = True
                            response = ''.join(full_tokens)
                        else:
                            response = backend.chat(
                                messages=messages,
                                max_tokens=max_tokens,
                                temperature=temperature,
                            )

                # ── Identity / persona guardrails ──
                low = response.lower()
                banned = [
                    'i am an ai model', 'as an ai assistant', 'trained on',
                    "memory is part of my training data",
                    "i don't retain new information",
                    'each interaction is independent',
                    'i am a large language model',
                    'i do not retain personal data',
                    "i don't have memory of past conversations",
                    'i cannot remember previous interactions',
                ]
                if any(b in low for b in banned):
                    response = "⚠️ Empty response from backend."

                try:
                    from eli.cognition.reasoning_modes import apply_final_reasoning_contract as _rm_final
                    response = _rm_final(response)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

                _hist = (
                    (response.rstrip() + "\n\n[Generation stopped.]")
                    if (_was_cancelled and response) else
                    ("[Generation stopped.]" if _was_cancelled else response)
                )
                self.conversation_history.append({'role': 'assistant', 'content': _hist})
                self._last_eli_response = _hist

                # ── Persist to conversation_turns table (skip if CognitiveEngine stored) ──
                if not _storage_handled:
                    try:
                        # Canonical persistence lives in CognitiveEngine.
                        # GUI direct SQLite fallback disabled to prevent duplicate rows.
                        pass
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)

                if not _response_streamed:
                    self.chat_response_signal.emit(response)

                # Note: "no fake actions" follow-through now lives in the engine
                # (_stream_with_followthrough), so it covers every consumer and
                # the appended real result already arrives via the token stream.

                if getattr(self, '_tts_auto', False):
                    self._speak_response(response)
                self._mem_refresh_sig.emit()
                # Refresh confidence/grounding badge in status bar (via queued signal — worker thread safe)
                try:
                    self._conf_meta_update_sig.emit()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            except Exception as e:
                self.chat_response_signal.emit(f"❌ Error: {str(e)}")
                try:
                    import sqlite3 as _sq2, time as _t2, traceback as _tb2
                    from eli.core.paths import memory_db_path as _mdp
                    _c2 = _sq2.connect(str(_mdp()))
                    _c2.execute(
                        "CREATE TABLE IF NOT EXISTS failures "
                        "(id INTEGER PRIMARY KEY, ts REAL, user_input TEXT, error TEXT, traceback TEXT)")
                    _c2.execute(
                        "INSERT INTO failures (ts, user_input, error, traceback) VALUES (?,?,?,?)",
                        (_t2.time(), str(locals().get('user_message', ''))[:500],
                         str(e)[:500], _tb2.format_exc()[:1000]))
                    _c2.commit(); _c2.close()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            finally:
                self.is_generating = False
                self._cancel_stream_requested = False
                try:
                    from eli.cognition import gguf_inference as _ggi
                    _ggi.clear_cancel_generation()
                except Exception:
                    log.debug("[GUI] clear_cancel_generation failed in finally", exc_info=True)
                try:
                    from eli.cognition import expression_state as _es_think2
                    _es_think2.set_thinking(False)
                except Exception:
                    log.debug("[GUI] thinking-state clear failed", exc_info=True)
                self.status_signal.emit('Send enabled')
                self.status_signal.emit('🟢 Ready')

        self._generate_thread = threading.Thread(target=generate_worker, daemon=True)
        self._generate_thread.start()

    def clear_chat(self):
        reply = QMessageBox.question(
            self,
            "Clear Chat",
            "Are you sure you want to clear the conversation?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.chat_display.clear()
            self.conversation_history = []
            self.status_signal.emit("Chat cleared")

    def _load_into_labs_file_chat(self, path: Path) -> bool:
        """Load a file into Labs > File Chat and switch to that tab. Returns True on success."""
        try:
            labs = getattr(self, "_labs_widget", None)
            if labs is None:
                return False
            fc = getattr(labs, "_file_chat_tab", None)
            if fc is None:
                return False
            fc._load_path(str(path))
            # Switch to Labs tab, then to File Chat sub-tab
            self._focus_tab_widget(labs)
            inner = getattr(labs, "_inner_tabs", None)
            if inner is not None:
                idx = inner.indexOf(fc)
                if idx >= 0:
                    inner.setCurrentIndex(idx)
            return True
        except Exception:
            return False

    def _append_chat_response(self, text: str):
        """Handle streamed tokens and complete responses (always on GUI thread)."""
        _ELI_HEADER_COLOR = "#88c0d0"   # nordic blue for ELI's name
        _ELI_BODY_COLOR   = "#d8dee9"   # light grey for ELI's response text

        if text == "__STREAM_START__":
            self._stream_buffer = []
            self._streaming = True
            ts = datetime.now().strftime("%H:%M:%S")
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertBlock()
            header_fmt = QTextCharFormat()
            header_fmt.setFontWeight(QFont.Weight.Bold)
            header_fmt.setForeground(QColor(_ELI_HEADER_COLOR))
            cursor.setCharFormat(header_fmt)
            cursor.insertText(f"🤖 ELI [{ts}]:")
            cursor.insertBlock()
            body_fmt = QTextCharFormat()
            body_fmt.setFontWeight(QFont.Weight.Normal)
            body_fmt.setForeground(QColor(_ELI_BODY_COLOR))
            cursor.setCharFormat(body_fmt)
            self.chat_display.setTextCursor(cursor)
            return

        if text == "__STREAM_END__":
            self._streaming = False
            self._stream_buffer = []
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertBlock()
            self.chat_display.setTextCursor(cursor)
            self.chat_display.ensureCursorVisible()
            return

        if text == "__STREAM_CANCELLED__":
            self._streaming = False
            self._stream_buffer = []
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            stop_fmt = QTextCharFormat()
            stop_fmt.setFontWeight(QFont.Weight.Normal)
            stop_fmt.setForeground(QColor("#bf616a"))
            cursor.setCharFormat(stop_fmt)
            cursor.insertText("\n[Generation stopped.]")
            cursor.insertBlock()
            self.chat_display.setTextCursor(cursor)
            self.chat_display.ensureCursorVisible()
            return

        if getattr(self, "_streaming", False):
            # Append token using the body colour format
            cursor = self.chat_display.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            body_fmt = QTextCharFormat()
            body_fmt.setFontWeight(QFont.Weight.Normal)
            body_fmt.setForeground(QColor(_ELI_BODY_COLOR))
            cursor.setCharFormat(body_fmt)
            self.chat_display.setTextCursor(cursor)
            self.chat_display.insertPlainText(text)
            self.chat_display.ensureCursorVisible()
            return

        # ── Non-streamed complete response ───────────────────────────────────
        # Pre-process: extract user-visible text from any stray dict blobs
        # before display so raw Python repr never reaches the chat widget.
        _r = text
        _r_stripped = _r.strip()
        if _r_stripped.startswith("\u26a1 {") or _r_stripped.startswith("{'") or _r_stripped.startswith("{\""):
            try:
                import ast as _ast, re as _re2
                _blob = _re2.sub(r"^\u26a1\s*", "", _r_stripped)
                _data = _ast.literal_eval(_blob)
                if isinstance(_data, dict):
                    if _data.get("ok") and "results" in _data:
                        _rs = _data["results"]
                        _r = "\U0001f4cb " + " | ".join(r["text"] for r in _rs[:3]) if _rs else "\U0001f4cb No memories found."
                    else:
                        # Any other result dict \u2014 extract the user-visible field
                        _r = (
                            _data.get("response") or _data.get("content")
                            or _data.get("text") or _data.get("message")
                            or _data.get("result") or _data.get("answer")
                            or _data.get("output") or _r
                        )
                        if not isinstance(_r, str):
                            _r = str(_r)
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        ts = datetime.now().strftime("%H:%M:%S")
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertBlock()
        header_fmt = QTextCharFormat()
        header_fmt.setFontWeight(QFont.Weight.Bold)
        header_fmt.setForeground(QColor(_ELI_HEADER_COLOR))
        cursor.setCharFormat(header_fmt)
        cursor.insertText(f"🤖 ELI [{ts}]:")
        cursor.insertBlock()
        body_fmt = QTextCharFormat()
        body_fmt.setFontWeight(QFont.Weight.Normal)
        body_fmt.setForeground(QColor(_ELI_BODY_COLOR))
        cursor.setCharFormat(body_fmt)
        cursor.insertText(_r)
        cursor.insertBlock()
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()
