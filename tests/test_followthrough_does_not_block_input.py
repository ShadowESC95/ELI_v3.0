"""Live session report (2026-10-02): "ELI finished response but why is it still
generating, which is stopping me from asking a follow-up query?" — _stream_with_
followthrough (engine.py) silently re-runs the whole pipeline after the visible
reply is done (to make good on anything ELI said it would do), and the GUI's
Send button stayed locked for that whole hidden re-run since it only unlocks
when the generator is fully exhausted.

Fixed with a sentinel the engine yields right after the visible reply, which the
GUI uses to unlock input early while the worker thread keeps consuming any
trailing followthrough text in the background — plus a prior-thread cancel+join
in send_message() so a second worker can't write to the chat at the same time
as a still-finishing followthrough tail from the first.

Qt-coupled GUI module, not directly executable in CI — verified at the
source-text level (established pattern, see test_moe_gpu_layer_print_matches_
what_loads.py).
"""
from pathlib import Path

from tests._live_gui import live_gui_source
ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "eli" / "kernel" / "stages" / "generation.py"


def test_engine_exports_the_sentinel_and_yields_it_before_followthrough():
    src = ENGINE.read_text(encoding="utf-8")
    assert "MAIN_REPLY_DONE_SENTINEL" in src
    i = src.index("def _stream_with_followthrough")
    j = src.index("detect_action_commitment", i)
    block = src[i:j]
    assert "yield MAIN_REPLY_DONE_SENTINEL" in block


def test_gui_unlocks_input_on_the_sentinel_without_appending_it_as_text():
    src = live_gui_source()
    i = src.index("def generate_worker")
    j = src.index("def clear_chat", i) if "def clear_chat" in src[i:] else i + 20000
    block = src[i:j]
    assert "_MAIN_REPLY_DONE" in block
    assert "self.is_generating = False" in block
    k = block.index("token == _MAIN_REPLY_DONE")
    nearby = block[k:k + 600]
    assert "continue" in nearby  # must not fall through to full_tokens.append


def test_send_message_cancels_and_joins_a_still_running_prior_worker():
    src = live_gui_source()
    i = src.index("def send_message")
    j = src.index("self.is_generating = True", i)
    block = src[i:j]
    assert "_generate_thread" in block
    assert "is_alive()" in block
    assert "_request_generation_cancel" in block
    assert ".join(timeout=" in block


def test_stop_generation_and_send_message_share_the_same_cancel_helper():
    src = live_gui_source()
    assert src.count("_request_generation_cancel()") >= 2


def test_worker_thread_reference_is_stored_not_fire_and_forget():
    src = live_gui_source()
    assert "self._generate_thread = threading.Thread(target=generate_worker" in src


# ── A message while ELI answers or the model loads (the real mixins, offscreen Qt) ──────────
# Owner's rule: a message typed while ELI is answering never stops the answer (Stop is its own
# control); a command that needs no model runs at once; anything else waits its turn. A turn
# stopped before its first word shows it was stopped, as its history says.

import threading
import time

import pytest


class _Spin:
    def __init__(self, v):
        self._v = v

    def value(self):
        return self._v


class _Engine:
    """CognitiveEngine.process as the window uses it: a streamed reply that waits on `go`, and the
    dict an action returns when asked without streaming."""

    def __init__(self, tokens=("A long ", "answer.")):
        self.calls, self.go, self.tokens, self.closed = [], threading.Event(), tokens, []

    def process(self, text, stream=False, reasoning_mode=None, **kw):
        self.calls.append((text, stream))
        if not stream:
            return {"ok": True, "action": "VOLUME", "response": f"Volume set ({text})."}

        def reply():
            try:
                for i, token in enumerate(self.tokens):
                    if i:
                        self.go.wait(60)
                    yield token
            finally:
                self.closed.append(text)
        return reply()

    def cancel_generation(self):
        from eli.cognition import gguf_inference
        gguf_inference.request_cancel_generation()


@pytest.fixture
def window():
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit, QWidget
    from eli.gui.main_window._mixins.chrome_io import ChromeIoMixin
    from eli.gui.main_window._mixins.conversation import ConversationMixin
    app = QApplication.instance() or QApplication([])

    class _Window(QObject, ConversationMixin, ChromeIoMixin):
        status_signal = Signal(str)
        chat_response_signal = Signal(str)
        chat_stream_signal = Signal(str)
        _ran_now_sig = Signal(str)
        _mem_refresh_sig = Signal()
        _conf_meta_update_sig = Signal()
        _generated_artifact_open_sig = Signal(object)

        def __init__(self):
            super().__init__()
            self._host = host = QWidget()            # never shown: nothing reaches a screen
            self.chat_input, self.chat_display = QTextEdit(host), QTextEdit(host)
            self.send_btn, self.stop_btn = QPushButton("Send", host), QPushButton("Stop", host)
            self.status_label, self.model_info_label = QLabel(host), QLabel(host)
            self.max_tokens_input, self.temperature_input = _Spin(256), _Spin(0.5)
            self.conversation_history, self._agent_wizard_state = [], None
            self.is_generating = self._model_loading = self._turn_running = False
            self._running_now, self._waiting_messages, self._held_chat = 0, [], []
            self.backend, self.statuses, self._cognitive_engine = object(), [], _Engine()
            self.status_signal.connect(self._status, Qt.ConnectionType.QueuedConnection)
            # as the window wires them: complete messages, and the reply being written
            self.chat_response_signal.connect(getattr(self, "_on_chat_message", self._append_chat_response),
                                              Qt.ConnectionType.QueuedConnection)
            if hasattr(self, "chat_stream_signal"):
                self.chat_stream_signal.connect(self._append_chat_response, Qt.ConnectionType.QueuedConnection)
            if hasattr(self, "_on_ran_now"):
                self._ran_now_sig.connect(self._on_ran_now, Qt.ConnectionType.QueuedConnection)

        def _status(self, message):
            self.statuses.append(message)
            self._update_status(message)

        def _text_backend_ready(self, notify=True):
            return self.backend

        def _maybe_auto_select_reasoning_mode(self, text):
            pass

        def save_conversation(self, quiet=False):
            pass

        def type_and_send(self, text):
            self.chat_input.setPlainText(text)
            self.send_message()

        def settle(self, until=None, seconds=10.0):
            """Run the event loop until `until()` holds; with no condition, a moment's events."""
            if until is None:
                until, seconds = (lambda: False), 0.3
            deadline = time.monotonic() + seconds
            while not until() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            app.processEvents()

        def idle(self):
            t = getattr(self, "_generate_thread", None)
            return not self._turn_running and (t is None or not t.is_alive()) and not self._running_now

    from eli.cognition import gguf_inference
    gguf_inference.clear_cancel_generation()
    w = _Window()
    yield w
    w._cognitive_engine.go.set()
    w.settle(w.idle)
    gguf_inference.clear_cancel_generation()


def _transcript(w):
    return w.chat_display.toPlainText()


def test_a_message_typed_while_eli_answers_does_not_stop_the_answer_and_is_sent_after_it(window):
    w, engine = window, window._cognitive_engine
    w.type_and_send("tell me about the history of bridges")
    w.settle(lambda: "A long" in _transcript(w))
    w.type_and_send("what is a river")                       # needs the model
    w.settle()
    assert engine.calls == [("tell me about the history of bridges", True)], "nothing else sent yet"
    assert w._cancel_stream_requested is False and not __import__(
        "eli.cognition.gguf_inference", fromlist=["x"]).is_cancel_requested(), "the answer was not stopped"
    assert "1 waiting" in w.send_btn.text() and "what is a river" in w.send_btn.toolTip()
    assert "what is a river" not in _transcript(w), "shown when it is sent, after this answer"
    engine.go.set()
    w.settle(lambda: len(engine.calls) == 2 and w.idle())
    assert engine.calls[1] == ("what is a river", True)
    text = _transcript(w)
    assert text.index("A long answer.") < text.index("what is a river")
    assert "[Generation stopped.]" not in text
    assert w.send_btn.text() == "Send"


def test_a_command_that_needs_no_model_runs_at_once_while_eli_answers(window):
    w, engine = window, window._cognitive_engine
    w.type_and_send("tell me about the history of bridges")
    w.settle(lambda: "A long" in _transcript(w))
    w.type_and_send("volume 40%")
    w.settle(lambda: any(m.startswith("⚡ Volume set") for m in w.statuses))
    assert ("volume 40%", False) in engine.calls, "run at once, through the engine"
    assert any(m.startswith("⚡ Volume set") for m in w.statuses), "what it did is said at once"
    assert "volume 40%" not in _transcript(w), "the answer being written is not split"
    engine.go.set()
    w.settle(lambda: w.idle() and "Volume set" in _transcript(w))
    text = _transcript(w)
    assert text.index("A long answer.") < text.index("volume 40%") < text.index("Volume set (volume 40%).")
    roles = [(m["role"], m["content"]) for m in w.conversation_history]
    assert roles[-2:] == [("user", "volume 40%"), ("assistant", "Volume set (volume 40%).")]


def test_a_command_that_needs_no_model_runs_with_no_model_loaded(window):
    """With no model loaded "volume 40%" was answered "No model is loaded yet" and nothing ran,
    though the same message runs at once while a model is loading."""
    w, engine = window, window._cognitive_engine
    w.backend = None
    w.type_and_send("volume 40%")
    w.settle(lambda: w.idle() and "Volume set" in _transcript(w))
    assert engine.calls == [("volume 40%", False)]
    assert _transcript(w).count("volume 40%") == 2, "the message once, and once in what was done"
    w.type_and_send("what is a river")                       # needs the model: nothing is sent
    w.settle()
    assert engine.calls == [("volume 40%", False)]


def test_enter_and_send_never_stop_only_stop_does(window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from eli.gui.main_window.window import EliMainWindow
    w, engine = window, window._cognitive_engine
    w.type_and_send("tell me about the history of bridges")
    w.settle(lambda: "A long" in _transcript(w))
    assert "Stop" not in w.send_btn.text() and not w.stop_btn.isHidden()
    w.chat_input.setPlainText("what is a river")
    enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    assert EliMainWindow.eventFilter(w, w.chat_input, enter) is True
    assert w.chat_input.toPlainText() == "", "Enter took the message"
    assert [m["text"] for m in w._waiting_messages] == ["what is a river"]
    assert not w._cancel_stream_requested
    w._waiting_messages.clear()
    w.stop_generation()
    engine.go.set()
    w.settle(w.idle)
    assert "[Generation stopped.]" in _transcript(w)
    assert engine.closed == ["tell me about the history of bridges"]
    assert w.stop_btn.isHidden()


def test_a_turn_stopped_before_its_first_word_shows_it_was_stopped(window):
    """Stopped before any token, the screen showed the user's message and nothing else, while the
    saved history said '[Generation stopped.]'."""
    w = window
    w._cognitive_engine.tokens = ("never shown",)
    from eli.cognition import gguf_inference
    gguf_inference.request_cancel_generation()          # Stop pressed before the first token...
    real_clear = gguf_inference.clear_cancel_generation
    gguf_inference.clear_cancel_generation = lambda: None   # ...after the turn cleared it at its start
    try:
        w.type_and_send("vol 60%")
        w.settle(w.idle)
    finally:
        gguf_inference.clear_cancel_generation = real_clear
    text = _transcript(w)
    assert "vol 60%" in text and "ELI [" in text.split("vol 60%", 1)[1]
    assert text.rstrip().endswith("[Generation stopped.]")
    assert "never shown" not in text
    assert w.conversation_history[-1] == {"role": "assistant", "content": "[Generation stopped.]"}


def test_a_message_typed_while_the_model_loads_waits_for_it_and_a_command_runs_at_once(window):
    """'play ...' typed while the model loaded was answered 'Still loading the model' and dropped."""
    w, engine = window, window._cognitive_engine
    w._model_loading, w.backend = True, None
    w.type_and_send("open the calculator")                  # needs no model: runs now
    w.settle(lambda: not w._running_now)
    assert engine.calls == [("open the calculator", False)]
    assert "Volume set (open the calculator)." in _transcript(w)    # nothing is being written: shown now
    w.type_and_send("what is a river")                       # waits for the model
    w.settle()
    assert "Still loading" not in _transcript(w) and len(engine.calls) == 1
    assert any("sent when the model has loaded" in m for m in w.statuses)
    w._model_loading, w.backend = False, object()
    w._update_status("Take next message")
    engine.go.set()
    w.settle(lambda: w.idle() and len(engine.calls) == 2)
    assert engine.calls[1] == ("what is a river", True)
    assert "what is a river" in _transcript(w)


def test_a_reply_that_says_trained_on_is_kept_as_written(window):
    """Any reply containing 'trained on' was replaced in the history, the saved conversation and
    what was spoken by a made-up 'Empty response from backend'."""
    w, engine = window, window._cognitive_engine
    engine.tokens = ("GPT-2 was trained on WebText.",)
    w.type_and_send("what was GPT-2 trained on?")
    w.settle(w.idle)
    assert w.conversation_history[-1] == {"role": "assistant", "content": "GPT-2 was trained on WebText."}
    assert w._last_eli_response == "GPT-2 was trained on WebText."
    assert "Empty response" not in _transcript(w)


def test_what_needs_no_model_is_the_engines_direct_set():
    from eli.gui.main_window._mixins.conversation import ConversationMixin
    assert ConversationMixin._needs_no_model("pause")
    assert ConversationMixin._needs_no_model("volume 40%")
    assert not ConversationMixin._needs_no_model("what is a river")


def test_a_message_that_comes_while_a_reply_is_written_is_shown_after_it_not_inside_it(window):
    """An offer, an error or a turn said elsewhere, emitted while a reply streamed, went into the
    middle of the reply as one of its tokens."""
    w, engine = window, window._cognitive_engine
    w.type_and_send("tell me about the history of bridges")
    w.settle(lambda: "A long" in _transcript(w))
    w.chat_response_signal.emit("💡 An offer of mine")
    w.settle()
    assert "An offer of mine" not in _transcript(w), "the reply being written is not split"
    engine.go.set()
    w.settle(lambda: w.idle() and "An offer of mine" in _transcript(w))
    text = _transcript(w)
    assert "A long answer." in text and text.index("A long answer.") < text.index("An offer of mine")
    assert all("An offer of mine" not in m["content"] for m in w.conversation_history), "not a turn"


def test_a_command_said_while_eli_answers_runs_at_once_like_a_typed_one(window, monkeypatch):
    """The voice path ran the executor on the window's thread and wrote its own chat lines into the
    reply being written; it goes the way a typed message goes now."""
    import eli.execution.executor_enhanced as ex
    for name in ("execute_action", "execute"):             # nothing real may run (the volume)
        monkeypatch.setattr(ex, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    w, engine = window, window._cognitive_engine
    w.type_and_send("tell me about the history of bridges")
    w.settle(lambda: "A long" in _transcript(w))
    w._on_stt_transcript("volume 40%")
    w.settle(lambda: any(m.startswith("⚡ Volume set") for m in w.statuses))
    assert ("volume 40%", False) in engine.calls, "run at once, through the engine"
    assert "volume 40%" not in _transcript(w), "the answer being written is not split"
    engine.go.set()
    w.settle(lambda: w.idle() and "Volume set" in _transcript(w))
    text = _transcript(w)
    assert text.index("A long answer.") < text.index("volume 40%") < text.index("Volume set (volume 40%).")


def test_what_is_read_aloud_leaves_out_scripts_and_reads_headlines_as_lines():
    from eli.gui.main_window._mixins.conversation import ConversationMixin
    said = ConversationMixin._spoken_part
    assert said("I can repair it. Exact command:\nsudo apt install x\nConfirm?") == "I can repair it."
    assert said("Here it is:\n```bash\nrm -rf build\n```") == "Here it is:"
    assert said("Headlines (3 fetched):\n• [Example News] Rivers rise\n• [Sample Post] (2h) New bridge…") == \
        "3 fetched. Example News: Rivers rise. Sample Post: New bridge"
    assert said("Just a plain answer.") == "Just a plain answer."


def test_each_message_is_laid_out_on_its_own_after_a_bulleted_reply(window):
    """Live: after a reply laid out as a bulleted list, every message after it, the user's too, came out
    as bullet points: the next block took the list's last block's format."""
    w = window
    for piece in ("__STREAM_START__", "Here is what I have:\n", "- the first thing\n", "- the second thing",
                  "__STREAM_END__"):
        w._append_chat_response(piece)
    w._draw_chat("user", "and what about the third?")
    w._append_chat_response("Nothing for the third, I'm afraid.")

    def block_of(words):
        block = w.chat_display.document().begin()
        while block.isValid():
            if words in block.text():
                return block
            block = block.next()
        raise AssertionError(f"{words!r} is not in the transcript")

    assert block_of("the second thing").textList() is not None, "the reply's own list is laid out as one"
    for words in ("and what about the third?", "Nothing for the third", "ELI ["):
        later = [b for b in _blocks(w) if words in b.text()][-1]
        assert later.textList() is None, f"{words!r} was laid out inside the reply's list"
    assert block_of("You [").textList() is None


def _blocks(w):
    block, out = w.chat_display.document().begin(), []
    while block.isValid():
        out.append(block)
        block = block.next()
    return out
