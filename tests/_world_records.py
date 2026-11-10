"""ELI's records for the world's tests (the books, the stories, the Evidence Wall), every store in the
test's own folder: the memory, the evidence ledger and thoughts, the lessons, the world folder (journal,
stories, shelves), the goal store and the insight cache. Nothing of the machine's or the owner's is read,
even on the --noconftest lane. All names and words are invented."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

T0 = 1_790_000_000.0          # a fixed time every record here is placed from (September 2026)
DAY = 86400.0


class Records:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from eli.cognition import epistemics, thoughts
        from eli.memory import memory as memory_module
        from eli.memory.memory import Memory
        from eli.runtime import action_outcomes, evidence_ledger
        from eli.world import books, shown
        self.root = tmp_path
        self.db = tmp_path / "user.sqlite3"
        self.memory = Memory(db_path=self.db)
        self.ledger = tmp_path / "ledger.sqlite3"
        monkeypatch.setattr(memory_module, "get_memory", lambda *a, **k: self.memory)
        monkeypatch.setattr(evidence_ledger, "_default_db_path", lambda: self.ledger)
        monkeypatch.setattr(thoughts, "_default_db_path", lambda: self.ledger)
        monkeypatch.setattr(thoughts, "_trimmed_at", 0.0)
        monkeypatch.setenv("ELI_AGENT_DB", str(tmp_path / "agent.sqlite3"))
        monkeypatch.setenv("ELI_WORLD_DIR", str(tmp_path / "world"))
        monkeypatch.setenv("ELI_GOAL_STORE", str(tmp_path / "goals.json"))
        monkeypatch.setattr("eli.planning.insight_synthesis._cache_path", lambda: tmp_path / "insight.json")
        monkeypatch.setattr("eli.kernel.state.get_active_user_id", lambda *a, **k: "owner")
        epistemics.forget_fits()
        action_outcomes.forget()
        shown.forget()
        books._runs_kept.update({"newest": None, "owner": None, "runs": []})
        from eli.memory import policy
        from eli.world import stories
        monkeypatch.setattr(stories, "_tried", set())
        monkeypatch.setattr(stories, "_model_name", lambda: "")                  # no scan of the machine's models
        monkeypatch.setattr("eli.cognition.persona.get_persona", lambda: "ELI.")  # nor its persona file
        monkeypatch.setattr(policy, "rbac_members", lambda: frozenset())          # nor its API's users

    # ── the memory ──
    def turn(self, session: str, role: str, content: str, ts: float, user_id: str = "") -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO conversation_turns (session_id, user_id, role, content, ts, timestamp) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (session, user_id, role, content, ts, ts))
        conn.commit()
        conn.close()

    def summary(self, session: str, text: str, start: float, end: float, turns: int) -> None:
        self.memory.save_session_summary(session, user_id=None, summary=text, turns_count=turns,
                                         started_at=start, ended_at=end)

    def memory_row(self, text: str, ts: float, *, kind: str = "memory", tags: str = "") -> int:
        conn = sqlite3.connect(self.db)
        cur = conn.execute("INSERT INTO memories (text, content, kind, tags, ts, timestamp, source, user_id) "
                           "VALUES (?, ?, ?, ?, ?, ?, 'user', '')", (text, text, kind, tags, ts, ts))
        conn.commit()
        conn.close()
        return int(cur.lastrowid)

    def stance(self, topic: str, position: str, ts: float, valence: Optional[int] = None) -> int:
        from eli.cognition import stance_store
        conn = sqlite3.connect(self.db)
        cur = conn.cursor()
        stance_store.ensure_tables(cur)
        stance_store.record_stance(cur, topic, position, now=ts)
        sid = cur.execute("SELECT id FROM eli_stances WHERE position = ?", (position,)).fetchone()[0]
        if valence is not None:
            stance_store.set_valence(cur, sid, valence)
        conn.commit()
        conn.close()
        return int(sid)

    # ── the ledger ──
    def ran(self, action: str, ok: bool, ts: float, *, session: str = "s1", said: str = "done",
            args: Optional[Dict[str, Any]] = None, user_id: str = "") -> None:
        from eli.runtime import evidence_ledger
        evidence_ledger.record_event("executor_action", source="executor.post_dispatch", action=action, subject="",
                                     content=said, payload={"args": dict(args or {}), "result": {"ok": ok}},
                                     outcome="ok" if ok else "failed", session_id=session, user_id=user_id,
                                     timestamp=ts)

    def figure(self, kind: str, rid: str, score: float, right: bool, ts: float) -> None:
        """A figure ELI gave at `ts` and what became of it (eli/cognition/epistemics)."""
        from eli.cognition import epistemics
        from eli.runtime import evidence_ledger
        evidence_ledger.record_event(epistemics.EVENTS[kind][0], source="epistemics", action=kind.upper(), subject=rid,
                                     payload={"kind": kind, "class": "", "score": score, "p": score, "n": 0,
                                              "features": {}},
                                     reusable=False, request_id=rid, timestamp=ts)
        epistemics.settle(rid, right, kind=kind)


@pytest.fixture()
def records(tmp_path, monkeypatch) -> Records:
    return Records(tmp_path, monkeypatch)
