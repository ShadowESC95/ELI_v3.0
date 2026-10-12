"""ELI's local server, for a bundle that reaches the core over it.

A bundle that is a program of its own (ELI VR is a Godot application) cannot call into ELI's
Python. It says ``"needs": ["server"]`` in its description, and ELI makes sure its local server
is answering before the bundle starts: the one already running if there is one, otherwise one
started here, on this machine only, and stopped again when the bundle closes.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Iterator
from typing import Any

log = logging.getLogger("eli.bundles")

START_SECONDS = 30.0


def running(port: int) -> bool:
    """Whether ELI, and not some other program, is serving on this port."""
    from eli.runtime.server_util import probe_eli_server

    return bool(probe_eli_server(port).get("eli_running"))


def _start(port: int) -> Any:
    import uvicorn

    from api.server import app

    # Signal handlers are skipped off the main thread, so this is safe beside a window.
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio"
        )
    )
    threading.Thread(
        target=server.run, name="eli-bundle-server", daemon=True
    ).start()
    return server


@contextlib.contextmanager
def serving() -> Iterator[int | None]:
    """ELI's local server for as long as the block runs. Gives its port, or None when it
    could not be had: the bundle still opens, and says ELI is not there."""
    from eli.runtime.server_util import effective_api_port, port_in_use

    port = effective_api_port()
    if running(port):
        yield port
        return
    if port_in_use(port):
        log.warning(
            "port %s is held by another program; the bundle starts without ELI", port
        )
        yield None
        return
    try:
        server = _start(port)
    except Exception:
        log.warning("ELI's local server could not be started", exc_info=True)
        yield None
        return
    try:
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline and not running(port):
            time.sleep(0.25)
        yield port if running(port) else None
    finally:
        server.should_exit = True
