"""SelfImproveMixin — 4 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class SelfImproveMixin:
    def _update_failures_display(self, text: str):
        self.failures_display.setPlainText(text)

    def _update_improvements_display(self, text: str):
        self.improvements_display.append(text)

    def _si_analyze_failures(self):
        def worker():
            try:
                from eli.core.paths import memory_db_path
                import sqlite3
                db = str(memory_db_path())
                con = sqlite3.connect(db)
                try:
                    rows = con.execute(
                        "SELECT user_input, error, ts FROM failures ORDER BY ts DESC LIMIT 20"
                    ).fetchall()
                except Exception:
                    rows = []
                con.close()
                if not rows:
                    self.self_improve_failures_signal.emit("No failures recorded yet — keep chatting!")
                    return
                import time as _time
                def _fmt_ts(ts):
                    try:
                        return _time.strftime("%Y-%m-%d %H:%M", _time.localtime(float(ts)))
                    except Exception:
                        return str(ts or "")[:16]
                out = [f"[{_fmt_ts(ts)}] {str(inp or '')[:70]} → {str(err or '')[:70]}"
                       for inp, err, ts in rows]
                self.self_improve_failures_signal.emit("\n".join(out))
            except Exception as e:
                self.self_improve_failures_signal.emit(f"Error: {e}")
        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _si_run_cycle(self):
        def worker():
            try:
                lines = ["=== Self-Improvement Cycle ===\n"]

                # Run the actual improvement engine (analyze failures → store improvements)
                try:
                    from eli.runtime.self_improvement import SelfImprovementEngine
                    from eli.memory import get_agent_memory
                    engine = SelfImprovementEngine(memory=get_agent_memory())
                    result = engine.analyze_and_improve()
                    new_imps = result.get("improvements", [])
                    if new_imps:
                        lines.append(f"[Engine] Generated {len(new_imps)} new improvement records:")
                        for imp in new_imps[:6]:
                            lines.append(f"  • [{imp.get('category','?')}] {imp.get('description','')[:120]}")
                    else:
                        lines.append("[Engine] No new improvement records generated this cycle.")
                except Exception as _eng_err:
                    lines.append(f"[Engine] Could not run SelfImprovementEngine: {_eng_err}")

                # Also read stored improvements from agent DB
                import sqlite3
                from eli.core.paths import agent_db_path
                try:
                    con = sqlite3.connect(str(agent_db_path()))
                    imps = []
                    try:
                        imps = con.execute(
                            "SELECT category, description, status FROM improvements ORDER BY timestamp DESC LIMIT 20"
                        ).fetchall()
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                    fails = []
                    try:
                        fails = con.execute(
                            "SELECT user_input, error, occurrence_count FROM failures ORDER BY timestamp DESC LIMIT 6"
                        ).fetchall()
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                    con.close()
                except Exception as _db_err:
                    imps, fails = [], []
                    lines.append(f"[DB] Could not read agent DB: {_db_err}")

                if imps:
                    lines.append(f"\n=== Stored Improvements ({len(imps)}) ===")
                    for cat, desc, status in imps:
                        lines.append(f"  [{status or 'pending'}] {cat}: {(desc or '')[:120]}")

                if fails:
                    lines.append(f"\n=== Recent Failures ({len(fails)}) ===")
                    for ui, err, cnt in fails:
                        lines.append(f"  (×{cnt}) {(ui or '')[:60]} → {(err or '')[:80]}")

                # AI synthesis: generate concrete action suggestions for top failures
                backend = self._text_backend_ready(notify=False)
                if backend and fails:
                    fail_txt = "\n".join(
                        f"- (×{c}) {str(i or '')[:60]}: {str(e or '')[:60]}"
                        for i, e, c in fails[:4]
                    )
                    prompt = (
                        f"ELI self-improvement analysis. Recent recurring errors:\n{fail_txt}\n\n"
                        f"For each error, suggest ONE specific, actionable fix "
                        f"(file path + what to change or add). Be concrete."
                    )
                    try:
                        resp = backend.generate(prompt=prompt, max_tokens=400, temperature=0.4)
                        lines.append("\n=== AI Action Plan ===")
                        lines.append(resp.strip())
                    except Exception as _ai_err:
                        lines.append(f"\n[AI] Inference failed: {_ai_err}")

                if not imps and not fails:
                    lines.append("\nNo failures or improvements recorded yet.")
                    lines.append("Generate some interactions with ELI to build improvement data.")

                lines.append("\n--- Cycle complete ---")
                log.debug(f"[PROACTIVE] Self-improvement cycle done: {len(imps)} improvements, {len(fails)} failures")
                self.self_improve_improvements_signal.emit("\n".join(lines))
            except Exception as exc:
                self.self_improve_improvements_signal.emit(f"Error running cycle: {exc}")
        import threading
        threading.Thread(target=worker, daemon=True).start()
