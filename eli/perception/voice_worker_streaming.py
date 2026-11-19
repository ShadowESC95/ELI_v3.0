#!/usr/bin/env python3
"""Stream-aware voice worker for ELI."""

from __future__ import annotations

import threading
from typing import Optional

from eli.perception.tts_router import speak_if_enabled

class VoiceWorker:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._current_thread: Optional[threading.Thread] = None

    def speak(self, text: str):
        with self._lock:
            self._active = True
            t = threading.Thread(target=self._run, args=(text,), daemon=True)
            self._current_thread = t
        t.start()

    def _run(self, text: str):
        # Read the flag once while holding the lock. If interrupt() fires after this, the long
        # speak_if_enabled call still runs; that's the inherent trade-off of non-blocking TTS
        # dispatch. The flag is reset to False when we finish so the next speak() is clean.
        with self._lock:
            active = self._active
        if active:
            speak_if_enabled(text, enabled=True)
        with self._lock:
            self._active = False

    def interrupt(self):
        with self._lock:
            self._active = False
