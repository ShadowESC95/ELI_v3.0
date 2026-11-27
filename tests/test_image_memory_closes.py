"""The image engine's memory closes its database after each use: a connection left to the garbage
collector keeps the file locked on Windows, so a folder holding it could not be removed."""
from __future__ import annotations

import os
import sys

import pytest

from eli.tools.image_engine.image_engine.memory import ImageMemory


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/self/fd")
def test_no_connection_stays_open_after_use(tmp_path):
    db = tmp_path / "image_memory.sqlite3"
    memory = ImageMemory(db)
    with memory.connect() as conn:
        conn.execute("SELECT 1")

    def held():
        found = 0
        for fd in os.listdir("/proc/self/fd"):
            try:
                found += os.readlink(f"/proc/self/fd/{fd}") == str(db.resolve())
            except OSError:
                continue
        return found

    assert held() == 0


def test_a_failed_block_is_rolled_back_and_closed(tmp_path):
    memory = ImageMemory(tmp_path / "image_memory.sqlite3")
    with memory.connect() as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
    with pytest.raises(RuntimeError):
        with memory.connect() as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("stop")
    with memory.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0  # rolled back
    (tmp_path / "image_memory.sqlite3").unlink()  # nothing holds it
