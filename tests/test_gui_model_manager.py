"""The window's model manager (eli/gui/main_window/models.py) runs its generations through
gguf_inference, so llama.cpp's one lock, Stop and the user's priority over background work apply.

It called the shared Llama object's create_chat_completion directly, holding only a lock of the
window's own, so a proactive suggestion or a screen-text analysis could run inside llama.cpp at the
same moment as an engine turn or an embedding on the same native model.
"""
from __future__ import annotations

import eli.cognition.gguf_inference as G
from eli.gui.main_window.models import LocalModelManager
from eli.runtime.native_locks import LLAMA_CPP_NATIVE_LOCK


class _Llm:
    """The loaded Llama as far as a generation goes; it notes whether llama.cpp's lock was held."""

    def __init__(self):
        self.held, self.prompts = [], []

    def __call__(self, prompt, stream=False, **kw):
        self.held.append(LLAMA_CPP_NATIVE_LOCK._is_owned())
        self.prompts.append(prompt)
        if any(stop(None, None) for stop in kw.get("stopping_criteria") or ()):
            return iter(()) if stream else {"choices": [{"text": ""}]}      # stopped at once
        if stream:
            return iter([{"choices": [{"text": "a streamed reply"}]}])
        return {"choices": [{"text": "a reply"}]}

    def create_chat_completion(self, **kw):           # what the manager used to call
        self.held.append(LLAMA_CPP_NATIVE_LOCK._is_owned())
        if kw.get("stream"):
            return iter([{"choices": [{"delta": {"content": "a reply"}}]}])
        return {"choices": [{"message": {"content": "a reply"}}]}

    def tokenize(self, b, add_bos=False):
        return list(range(len(b) // 4 + 1))


def _manager(monkeypatch):
    llm = _Llm()
    monkeypatch.setattr(G, "_llm", llm, raising=False)
    monkeypatch.setattr(G, "_prompt_for_call", lambda system, prompt, **kw: f"{system}\n{prompt}")
    monkeypatch.setattr(G, "_effective_ctx_limit", lambda llm: 4096)
    monkeypatch.setattr(G, "_resume_from_saved_state", lambda llm, prompt, create: "")
    monkeypatch.setattr(G, "_note_headroom", lambda: None)
    mgr = LocalModelManager()
    mgr.model, mgr.is_loaded = llm, True
    return mgr, llm


def test_a_window_generation_holds_llama_cpps_lock(monkeypatch):
    mgr, llm = _manager(monkeypatch)
    reply = mgr.chat([{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
                     max_tokens=32, temperature=0.2)
    assert reply == "a reply"
    assert llm.held == [True]


def test_a_streamed_window_generation_holds_the_lock_while_it_streams(monkeypatch):
    mgr, llm = _manager(monkeypatch)
    assert "".join(mgr.chat_stream([{"role": "user", "content": "hi"}], max_tokens=32)) == "a streamed reply"
    assert llm.held == [True]
    assert not LLAMA_CPP_NATIVE_LOCK._is_owned()


def test_a_plain_prompt_is_the_prompt(monkeypatch):
    mgr, llm = _manager(monkeypatch)
    assert mgr.chat("summarise this", max_tokens=32) == "a reply"
    assert "summarise this" in llm.prompts[-1]


def test_stop_stops_a_window_generation(monkeypatch):
    mgr, llm = _manager(monkeypatch)
    G.request_cancel_generation()
    try:
        assert mgr.generate("what is on screen", max_tokens=32) == ""
    finally:
        G.clear_cancel_generation()
    assert mgr.generate("what is on screen", max_tokens=32) == "a reply"


def test_the_window_has_no_lock_of_its_own_for_the_model():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "eli" / "gui"
    for path in root.rglob("*.py"):
        assert "_inference_lock" not in path.read_text(encoding="utf-8"), path


def test_a_load_that_names_no_batch_asks_for_the_settings_own_default(tmp_path, monkeypatch):
    """The window's loader wrote the default batch out again as a number of its own (as did the
    terminal setup and the batch box); it is the batch setting's default (runtime_settings)."""
    import eli.core.runtime_settings as settings
    model = tmp_path / "sample.gguf"
    model.write_bytes(b"\0" * 64)
    asked = {}

    class _Asked(Exception):
        pass

    def _request(path, **kw):
        asked.update(kw)
        raise _Asked()

    monkeypatch.setitem(settings.DEFAULTS, "batch_size", 96)
    monkeypatch.setattr("eli.core.gpu_pack_runtime.try_activate_gpu_pack", lambda **k: False)
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda *a, **k: None)
    monkeypatch.setattr("eli.core.startup_hardware_optimizer.train_ctx_for_model", lambda p: 0)
    monkeypatch.setattr(G, "load_request", _request)
    mgr = LocalModelManager()
    assert mgr.load_model(str(model)) is False
    assert asked["n_batch"] == 96
    asked.clear()
    assert mgr.load_model(str(model), n_batch=40) is False
    assert asked["n_batch"] == 40
