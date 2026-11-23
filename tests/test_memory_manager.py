import pytest
from eli.memory import get_memory, get_agent_memory, resolve_db_paths
from eli.memory.memory import Memory
import sqlite3
import os

class TestMemoryManager:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Create a temporary memory instance for each test."""
        db_path = tmp_path / "test_memory.sqlite3"
        self.memory = Memory(db_path=db_path)
        self.memory.init_db()
        yield
        if db_path.exists():
            db_path.unlink()


    def test_get_recent_memories(self):
        mem = self.memory
        mem.store_memory("First")
        mem.store_memory("Second")
        recent = mem.get_recent_memories(limit=2)
        assert len(recent) >= 2

    def test_get_stats(self):
        mem = self.memory
        mem.store_memory("Stats test")
        stats = mem.get_stats()
        assert stats["total"] >= 1


        # No assertion, just ensure no crash

if __name__ == "__main__":
    pytest.main([__file__])
