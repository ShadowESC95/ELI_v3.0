"""Shutdown aborts in-flight inference so teardown never blocks on a long native call.

Regression for the 20-30 min shutdown hang: a background self-improvement/codegen call
sat in a single multi-minute native llm() call holding the shared lock, so unload_model()
blocked. signal_shutdown() installs a cooperative abort on EVERY generation (foreground
and background) and the broker short-circuits new calls.
"""
import eli.cognition.gguf_inference as gi


def test_shutdown_signal_roundtrip():
    gi.clear_shutdown()
    assert gi.is_shutting_down() is False
    gi.signal_shutdown()
    assert gi.is_shutting_down() is True
    gi.clear_shutdown()
    assert gi.is_shutting_down() is False


def test_shutdown_aborts_every_call():
    gi.clear_shutdown()
    gi._FG_PRIORITY.clear()
    try:
        gi.signal_shutdown()
        assert gi._should_abort_generation(background=True) is True
        assert gi._should_abort_generation(background=False) is True
    finally:
        gi.clear_shutdown()


def test_fg_priority_preempts_background_only_not_foreground():
    # A waiting foreground turn preempts BACKGROUND work but never aborts foreground work.
    gi.clear_shutdown()
    gi._FG_PRIORITY.set()
    try:
        assert gi._should_abort_generation(background=False) is False  # foreground runs
        assert gi._should_abort_generation(background=True) is True    # background yields
    finally:
        gi._FG_PRIORITY.clear()


def test_no_abort_when_idle():
    gi.clear_shutdown()
    gi._FG_PRIORITY.clear()
    assert gi._should_abort_generation(background=True) is False
    assert gi._should_abort_generation(background=False) is False


def test_broker_short_circuits_new_calls_during_shutdown():
    from eli.cognition.inference_broker import get_broker
    broker = get_broker()
    if broker is None or not getattr(broker, "gguf_ready", False):
        import pytest
        pytest.skip("no broker/model loaded")
    gi.signal_shutdown()
    try:
        assert broker.infer("hello", system="x", max_tokens=8) == ""
    finally:
        gi.clear_shutdown()


def test_a_call_that_waited_through_the_shutdown_signal_does_not_decode():
    """A background check waited for the model while ELI shut down; unload_model() held the lock and
    freed the model, the waiting call took the lock next and decoded on it: llama_decode returned 2
    and the process died (SIGSEGV), seen on a real model at the end of the replay. A call that gets
    the lock after the signal now gives up without decoding; with no shutdown the same call decodes."""
    import threading
    import time
    calls = []

    class StandInModel:
        def n_ctx(self):
            return 4096

        def __call__(self, prompt, **_kw):
            calls.append(prompt)
            return {"choices": [{"text": "decoded"}]}

    kw = dict(temperature=0.0, max_tokens=8, top_p=1.0, top_k=1, repeat_penalty=1.0, stop=None,
              stream=False, grammar=None)
    gi.clear_shutdown()
    gi._FG_PRIORITY.clear()
    out = {}
    gi._LLM_CALL_LOCK.acquire()            # held, as unload_model() holds it while it frees the model
    waiter = threading.Thread(target=lambda: out.setdefault("r", gi._safe_invoke_llm(StandInModel(), "hello", **kw)))
    try:
        waiter.start()
        time.sleep(0.2)
        gi.signal_shutdown()
    finally:
        gi._LLM_CALL_LOCK.release()
    waiter.join(10)
    gi.clear_shutdown()
    assert not waiter.is_alive()
    assert calls == [] and out["r"]["choices"][0]["text"] == "", "it decoded on a model being unloaded"
    assert gi._safe_invoke_llm(StandInModel(), "hello", **kw)["choices"][0]["text"] == "decoded"
    assert calls == ["hello"]
