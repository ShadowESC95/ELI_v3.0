"""Regression: a decode that cannot seat its batch must not return ZERO tokens, and a decode ELI
aborted itself must not be taken for one.

llama_decode returns 1 when it could not find a KV slot for the batch, and 2 when the abort
callback stopped it. `_safe_invoke_llm` recovers from 1 by dropping the reusable states, clearing
the cache and decoding fresh with headroom. 2 is ELI's own callback (Stop, shutdown, or a turn
taking the model from a background call): read as a full cache, a background call cleared the
cache and the saved state the turn was about to reuse and retried into the same abort three times.
"""
import eli.cognition.gguf_inference as g


class _Ctx:
    def __init__(self):
        self.cleared = 0

    def kv_cache_clear(self):
        self.cleared += 1


class _Llm:
    def __init__(self, fail_times=1, code=1):
        self.calls = 0
        self.code = code
        self.resets = 0
        self.fail_times = fail_times
        self._ctx = _Ctx()
        self.n_batch = 256

    def reset(self):
        self.resets += 1

    def n_ctx(self):
        return 4096

    def __call__(self, prompt, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"llama_decode returned {self.code}")
        return {"choices": [{"text": "recovered"}], "usage": {"completion_tokens": 1}}


def _patch_helpers(monkeypatch, resume_calls):
    monkeypatch.setattr(g, "_fit_generation_budget", lambda llm, p, m: (p, m))
    monkeypatch.setattr(g, "is_background_inference", lambda: False)
    monkeypatch.setattr(g, "_make_stopping_criteria", lambda bg: None)
    monkeypatch.setattr(g, "_effective_ctx_limit", lambda llm: 4096)
    monkeypatch.setattr(g, "_estimate_prompt_tokens", lambda llm, p: 100)
    monkeypatch.setattr(g, "_resume_from_saved_state",
                        lambda llm, p, create: (resume_calls.append(create), "")[1])


def test_safe_invoke_recovers_from_kv_slot_decode_failure(monkeypatch):
    resume_calls = []
    _patch_helpers(monkeypatch, resume_calls)
    llm = _Llm(fail_times=1)

    out = g._safe_invoke_llm(
        llm, "a big prompt", temperature=0.7, max_tokens=512, top_p=0.9, top_k=40,
        repeat_penalty=1.1, stop=[], stream=False, grammar=None,
    )

    assert out["choices"][0]["text"] == "recovered"   # not zero tokens
    assert llm.calls == 2                              # failed once, then recovered
    assert llm.resets >= 1 and llm._ctx.cleared >= 1   # the cache was actually cleared
    assert resume_calls == [True]                      # resume ran once, then was SKIPPED on the retry


def test_non_kv_errors_still_propagate(monkeypatch):
    """A genuine, non-KV failure must not be swallowed by the recovery path — it still raises."""
    import pytest
    resume_calls = []
    _patch_helpers(monkeypatch, resume_calls)

    class _BadLlm(_Llm):
        def __call__(self, prompt, **kw):
            self.calls += 1
            raise RuntimeError("model file is corrupt")

    with pytest.raises(RuntimeError, match="corrupt"):
        g._safe_invoke_llm(
            _BadLlm(), "p", temperature=0.7, max_tokens=256, top_p=0.9, top_k=40,
            repeat_penalty=1.1, stop=[], stream=False, grammar=None,
        )


def test_a_decode_eli_aborted_is_neither_cleared_nor_retried(monkeypatch):
    import pytest
    resume_calls = []
    _patch_helpers(monkeypatch, resume_calls)
    llm = _Llm(fail_times=4, code=2)
    monkeypatch.setattr(g._bg_tls, "cut_short", True, raising=False)   # a turn took the model
    with pytest.raises(RuntimeError, match="returned 2"):
        g._safe_invoke_llm(llm, "p", temperature=0.7, max_tokens=200, top_p=0.9, top_k=40,
                           repeat_penalty=1.1, stop=[], stream=False, grammar=None)
    assert llm.calls == 1 and llm.resets == 0 and llm._ctx.cleared == 0


def test_an_abort_nothing_asked_for_is_tried_again_as_it_was(monkeypatch):
    resume_calls = []
    _patch_helpers(monkeypatch, resume_calls)
    monkeypatch.setattr(g._bg_tls, "cut_short", False, raising=False)
    llm = _Llm(fail_times=1, code=2)
    out = g._safe_invoke_llm(llm, "p", temperature=0.7, max_tokens=200, top_p=0.9, top_k=40,
                             repeat_penalty=1.1, stop=[], stream=False, grammar=None)
    assert out["choices"][0]["text"] == "recovered" and llm._ctx.cleared == 0 and resume_calls == [True, True]


# ── the model lock and a streamed reply ────────────────────────────────────────────────────────
import threading


class _StreamLlm(_Llm):
    """Streams three pieces; records which thread each decode step ran on."""

    def __init__(self, pieces=3, gate=None):
        super().__init__(fail_times=0)
        self.pieces = pieces
        self.gate = gate              # when set: every piece after the first waits for it
        self.decode_threads = []

    def __call__(self, prompt, **kw):
        self.calls += 1
        stops = kw.get("stopping_criteria") or []

        def gen():
            for i in range(self.pieces):
                if i and self.gate is not None:
                    self.gate.wait(5)
                self.decode_threads.append(threading.current_thread().name)
                if any(c(None, None) for c in stops):
                    return
                yield {"choices": [{"text": f"p{i}"}]}
        return gen()


def _free_from_another_thread() -> bool:
    got = []

    def probe():
        ok = g._LLM_CALL_LOCK.acquire(timeout=2)
        got.append(ok)
        if ok:
            g._LLM_CALL_LOCK.release()
    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return got == [True]


def _stream(llm):
    return g._safe_invoke_llm(llm, "a prompt", temperature=0.7, max_tokens=64, top_p=0.9, top_k=40,
                              repeat_penalty=1.1, stop=[], stream=True, grammar=None)


def test_a_stream_read_piece_by_piece_from_different_threads_gives_the_model_back(monkeypatch):
    """A web server asks for each piece of a streamed reply from a pool thread. The model lock was
    taken on the first and given back on the last: the release failed, was swallowed, and the lock
    stayed held by a thread that had moved on, so every later model call waited for good."""
    _patch_helpers(monkeypatch, [])
    llm = _StreamLlm()
    stream = _stream(llm)
    got = []
    for _ in range(4):
        t = threading.Thread(target=lambda: got.append(next(stream, None)))
        t.start()
        t.join()
    assert [p["choices"][0]["text"] for p in got if p] == ["p0", "p1", "p2"]
    assert _free_from_another_thread(), "the model lock is free once the stream has ended"
    assert len(set(llm.decode_threads)) == 1, "the whole decode ran on one thread"


def test_a_reader_that_stops_early_stops_the_decode_and_frees_the_model(monkeypatch):
    _patch_helpers(monkeypatch, [])
    gate = threading.Event()
    llm = _StreamLlm(pieces=50, gate=gate)
    monkeypatch.setattr(g, "_make_stopping_criteria", lambda bg: [])
    stream = _stream(llm)
    assert next(stream)["choices"][0]["text"] == "p0"
    stream.close()
    gate.set()
    assert _free_from_another_thread()
    assert len(llm.decode_threads) <= 2, "the decode stopped at the next piece once nobody was reading"


def test_an_error_starting_a_stream_reaches_the_callers_retry(monkeypatch):
    """Errors before the first piece (the lock, the saved state, the call) are raised where the
    caller's retries handle them, as they were when the decode ran on the caller's thread."""
    calls = []
    _patch_helpers(monkeypatch, calls)

    class _Once(_StreamLlm):
        def __call__(self, prompt, **kw):
            if not self.calls:
                self.calls += 1
                raise RuntimeError("llama_decode returned 1")
            return super().__call__(prompt, **kw)
    llm = _Once()
    pieces = [p["choices"][0]["text"] for p in _stream(llm)]
    assert pieces == ["p0", "p1", "p2"] and llm._ctx.cleared == 1
    assert _free_from_another_thread()


def test_a_reload_places_the_embedder_before_it_takes_the_model_lock(monkeypatch):
    """The embedder's lock is taken by an embedding before the model lock. A reload that held the
    model lock and then waited for the embedder's lock met such an embedding: both stopped."""
    seen = []

    class _Store:
        def ready(self):
            seen.append(g._native_lock_held_here())
    import eli.memory.vector_store as vs
    monkeypatch.setattr(vs, "get_vector_store", lambda: _Store())
    monkeypatch.setattr(g, "unload_model", lambda: None)
    monkeypatch.setattr(g, "load_model", lambda force_reload=False, request=None: (
        g._place_embedder() if not g._native_lock_held_here() else None, object())[1])
    import eli.core.runtime_settings as rs
    monkeypatch.setattr(rs, "load_settings_from_disk", lambda: {})
    monkeypatch.setattr(rs, "apply_env", lambda s=None: s)
    assert g.reload_model()["ok"] is True
    assert seen == [False], "the embedder was placed once, outside the model lock"


def test_a_load_inside_the_model_lock_leaves_the_embedder_alone(monkeypatch, tmp_path):
    seen = []

    class _Store:
        def ready(self):
            seen.append(True)
    import eli.memory.vector_store as vs
    monkeypatch.setattr(vs, "get_vector_store", lambda: _Store())
    monkeypatch.setattr(g, "_load_runtime_settings", lambda *a, **k: {})
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(g, "_model_path_to_load", lambda: model)
    import eli.cognition.model_load_diagnostics as mld
    monkeypatch.setattr(mld, "preflight_gguf_model", lambda p: "stop here")
    import pytest
    with g._LLM_CALL_LOCK:
        with pytest.raises(Exception, match="stop here"):
            g._load_model_now()
    assert seen == []
    with pytest.raises(Exception, match="stop here"):
        g._load_model_now()
    assert seen == [True]


def test_a_stream_the_user_stopped_ends_quietly_without_a_fault_message(monkeypatch):
    """A Stop aborts the decode ("llama_decode returned 2"); the stream ran its fallback and then said
    'Something went wrong reaching the local model', which API and VR readers showed."""
    from eli.kernel.engine import CognitiveEngine
    from eli.kernel.stages import generation as gen_mod
    eng = CognitiveEngine()
    eng._gguf_available = True
    fallback = []

    def _aborted(*a, **k):
        raise RuntimeError("llama_decode returned 2")

    monkeypatch.setattr(gen_mod.gguf_inference, "generate", _aborted, raising=False)
    monkeypatch.setattr(gen_mod.gguf_inference, "is_cancel_requested", lambda: True)
    monkeypatch.setattr(eng, "_get_chat_response", lambda *a, **k: fallback.append(1) or "")
    out = "".join(eng._stream_model_response("tell me about tides", ""))
    assert out == "" and fallback == []


def _engine_streaming(monkeypatch, pieces):
    from eli.kernel.engine import CognitiveEngine
    from eli.kernel.stages import generation as gen_mod
    eng = CognitiveEngine()
    eng._gguf_available = True

    def _generate(*a, **k):
        assert k.get("stream") is True
        for p in pieces:
            yield {"response": p}

    monkeypatch.setattr(gen_mod.gguf_inference, "generate", _generate, raising=False)
    monkeypatch.setattr(gen_mod.gguf_inference, "is_cancel_requested", lambda: False)
    return eng


def _free_elsewhere(lock) -> bool:
    import threading
    got = []

    def _try():
        ok = lock.acquire(blocking=False)
        got.append(ok)
        if ok:
            lock.release()
    t = threading.Thread(target=_try)
    t.start()
    t.join()
    return got == [True]


def test_the_engines_stream_read_from_different_threads_leaves_nothing_held(monkeypatch):
    """The engine held its own lock (and, for a short greeting, the no-think flag) across every
    piece of a stream: taken on the thread that asked for the first piece, the release from the
    thread that asked for the last failed, and the lock stayed held for every later call."""
    import concurrent.futures as cf
    from eli.cognition import gguf_inference as g
    eng = _engine_streaming(monkeypatch, ["Hey", " there", "!"])
    stream = eng._stream_model_response("hey", "")
    with cf.ThreadPoolExecutor(max_workers=1) as first, cf.ThreadPoolExecutor(max_workers=1) as rest:
        got = [first.submit(next, stream).result()]
        got += rest.submit(list, stream).result()
        flag_left_on_first = first.submit(g._force_no_think_active).result()
    assert "".join(got) == "Hey there!"
    assert _free_elsewhere(eng._gguf_lock), "the engine's lock was left held"
    assert flag_left_on_first is False, "the no-think flag was left set on the thread that began the stream"


def test_the_engines_stream_stopped_midway_leaves_nothing_held(monkeypatch):
    import concurrent.futures as cf
    eng = _engine_streaming(monkeypatch, ["one", " two", " three"])
    stream = eng._stream_model_response("tell me about tides", "")
    with cf.ThreadPoolExecutor(max_workers=1) as reader:
        assert reader.submit(next, stream).result() == "one"
    stream.close()
    assert _free_elsewhere(eng._gguf_lock), "a stream stopped midway kept the engine's lock"
