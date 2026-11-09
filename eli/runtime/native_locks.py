from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

log = logging.getLogger(__name__)


class CallLock:
    """One model call at a time; the hold is the call's, not the thread's.

    Re-entrant for the thread that holds it (a call made inside a call: a reload unloads and loads,
    a timed call runs a completion), and given back by whoever ends the call. An owner-bound lock
    (threading.RLock) taken on the thread that asked for a stream's first piece could only be
    given back on that thread: a web server asks for each piece from any pool thread, the release
    on another thread failed, and the lock stayed held for good, so every later llama.cpp call (a
    reply, an embedding, an unload) waited forever. A release from another thread is logged at
    error, since it means a call ended away from where it started, and it frees the hold.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._owner: int | None = None
        self._depth = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return True
            if self._owner is not None and not blocking:
                return False
            deadline = None if timeout is None or timeout < 0 else time.monotonic() + float(timeout)
            while self._owner is not None:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)
            self._owner, self._depth = me, 1
            return True

    def release(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._owner is None:
                raise RuntimeError("release of the model lock, which nobody holds")
            if self._owner != me:
                log.error("the model lock was given back by a thread that did not take it "
                          "(taken by thread %s, given back by %s): a call ended away from where it began",
                          self._owner, me, stack_info=True)
            self._depth -= 1
            if self._depth <= 0:
                self._owner, self._depth = None, 0
                self._cond.notify()

    def _is_owned(self) -> bool:
        """Whether this thread holds it (the name threading.RLock answers to)."""
        return self._owner == threading.get_ident()

    def locked(self) -> bool:
        return self._owner is not None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


# llama.cpp is native code and ELI can host more than one Llama context in one
# process: the main GGUF model and the embedding model. Keep all llama.cpp entry
# points behind one process-local lock to avoid cross-context heap corruption.
LLAMA_CPP_NATIVE_LOCK = CallLock()

# Set while the user's turn waits for LLAMA_CPP_NATIVE_LOCK. A background generation holding the lock
# (news synthesis, insights, the morning report) checks it at every token and during prompt
# evaluation and yields (eli.cognition.gguf_inference), so the user's message never queues behind a chore.
FOREGROUND_WAITING = threading.Event()


@contextmanager
def hold_llama_lock(*, foreground: bool):
    """LLAMA_CPP_NATIVE_LOCK for one native call. Foreground work (the user's turn) announces itself
    while it waits, so a background generation holding the lock yields. Previously only
    generations did: embedding a typed "close settings" into memory waited 92 s behind a news
    synthesis."""
    if foreground:
        FOREGROUND_WAITING.set()
    try:
        LLAMA_CPP_NATIVE_LOCK.acquire()
    finally:
        if foreground:
            FOREGROUND_WAITING.clear()
    try:
        yield
    finally:
        LLAMA_CPP_NATIVE_LOCK.release()

# FAISS index cloning/writing is also native code. Keep disk persistence out of
# concurrent clone/write races while searches/adds use the VectorStore lock.
FAISS_IO_LOCK = threading.RLock()

