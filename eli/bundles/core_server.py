"""ELI's local server, for a bundle that reaches the core over it.

A bundle that is a program of its own (ELI VR is a Godot application) cannot call into ELI's
Python. It says ``"needs": ["server"]`` in its description, and ELI makes sure its local server
is answering before the bundle starts: the one already running if there is one, otherwise one
started here, on this machine only, and stopped again when the bundle closes.
"""

from __future__ import annotations

import contextlib
import os
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


def start() -> tuple[int | None, Any]:
    """Have ELI's local server answering in this process. Gives its port and, when it had to
    be started here, the server, for the caller to stop (`server.should_exit = True`). The port
    is None when it could not be had: the bundle still opens, and says ELI is not there."""
    from eli.runtime.server_util import effective_api_port, free_port_after, port_in_use

    port = effective_api_port()
    if running(port):
        return port, None
    if port_in_use(port):
        # another program (ELI v2 among them) holds it: ELI serves on the next free port, and says so
        moved = free_port_after(port)
        log.warning("port %s is held by another program; ELI serves on %s", port, moved or "none")
        if not moved:
            return None, None
        port = moved
    os.environ["ELI_API_PORT"] = str(port)
    try:
        server = _start(port)
    except Exception:
        log.warning("ELI's local server could not be started", exc_info=True)
        return None, None
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline and not running(port):
        time.sleep(0.25)
    return (port if running(port) else None), server


@contextlib.contextmanager
def serving() -> Iterator[int | None]:
    """ELI's local server for as long as the block runs."""
    port, server = start()
    try:
        yield port
    finally:
        if server is not None:
            server.should_exit = True
