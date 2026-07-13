"""Routing — the decomposed router (strangler target).

`eli/execution/router_enhanced.py` is a ~7.3k-line monolith: 93 module-level
functions doing regex→intent dispatch with no class structure. Next god-file after
the executor.

Planned shape:

    router.py           orchestrates the matcher chain (the dispatch order)
    intents/
        media.py        news_web.py    windows.py     gaze.py
        memory.py       notes.py       plugins.py     screenshot.py
        runtime_audit.py  hardware.py  self_ops.py    file_audit.py
        wakeword.py     ...

Each `intents/*.py` owns one domain's regex→intent rules; `router.py` composes them
in priority order (preserving the current precedence, which is load-bearing —
several comments in the monolith call out ordering, e.g. FILE_AUDIT before
RUNTIME_AUDIT). `router_enhanced.py` becomes a re-export shim for its 39 importers.

Empty until the first intent domain is moved.
"""
