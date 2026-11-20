"""QuickActionsMixin — 11 EliMainWindow methods (v3 GUI mixin split)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class QuickActionsMixin:
    def _qa_set_category(self, name: str):
        """User clicked a category button. Update active selection and
        re-filter the capability list."""
        self._qa_active_category = name
        for label, btn in getattr(self, "_qa_category_buttons", {}).items():
            try:
                btn.setChecked(label == name)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        # Re-run the existing filter (which now considers the active category).
        try:
            self._qa_filter(self._qa_search.text())
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _qa_category_for(self, action: str) -> str:
        for cat, members in self._QA_CATEGORIES.items():
            if action in members:
                return cat
        return "Other"

    def _qa_refresh_capabilities(self):
        """
        Load all executor-backed actions.  Only include actions that have a real
        handler in executor_enhanced (SOURCE = 'executor' or 'executor+router').
        Router-only entries are excluded — they have no handler and will fail.
        """
        caps = set()

        # 1. Canonical SUPPORTED_ACTIONS list from executor (primary source of truth)
        try:
            from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
            caps.update(a.upper() for a in SUPPORTED_ACTIONS)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # 2. CapabilitySync — only keep executor-backed entries
        try:
            from eli.runtime.capability_sync import CapabilitySync
            sync = CapabilitySync()
            discovered = sync.discover()
            for action, meta in discovered.items():
                if meta.get("source", "").startswith("executor"):
                    caps.add(action.upper())
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if not caps:
            # Hard fallback — always-available no-arg actions
            caps = {"SCREENSHOT", "TIME", "DATE", "SYSTEM_STATS", "CPU_USAGE",
                    "RAM_USAGE", "MEMORY_STATS", "SELF_ANALYZE", "AWARENESS_STATUS",
                    "MORNING_REPORT", "NEWS_FETCH", "HABIT_STATUS", "PROACTIVE_STATUS"}

        self._qa_all_caps = sorted(caps)
        self._qa_render_list(self._qa_all_caps)
        self._qa_count_lbl.setText(f"{len(self._qa_all_caps)} capabilities")

    def _qa_render_list(self, caps: list):
        self._qa_cap_list.clear()
        for name in caps:
            cat = self._qa_category_for(name)
            arg_info = self._QA_ACTION_ARGS.get(name)
            display = f"[{cat}]  {name}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, name)
            tooltip = f"Category: {cat}\nAction: {name}"
            if arg_info:
                tooltip += f"\nRequires: {arg_info[0]}"
            else:
                tooltip += "\nNo arguments required"
            tooltip += "\n\nDrag or double-click to pin"
            item.setToolTip(tooltip)
            self._qa_cap_list.addItem(item)

    def _qa_filter(self, text: str):
        txt = text.strip().upper()
        cat = getattr(self, "_qa_active_category", "All")
        members = (
            None if cat == "All"
            else self._QA_CATEGORIES.get(cat, set())
        )
        caps = self._qa_all_caps
        if members is not None:
            caps = [c for c in caps if c in members]
        if txt:
            caps = [c for c in caps if txt in c]
        self._qa_render_list(caps)

    def _qa_pin_visible(self):
        for i in range(self._qa_cap_list.count()):
            it = self._qa_cap_list.item(i)
            # UserRole always holds the raw action name; text now has "[Cat]  NAME"
            name = it.data(Qt.ItemDataRole.UserRole)
            if not name:
                # fallback: strip "[Category]  " prefix if present
                raw = it.text()
                name = raw.split("]")[-1].strip() if "]" in raw else raw.split()[0]
            self._qa_board.add_card(name)
        self._qa_save_buttons()

    def _qa_clear_board(self):
        self._qa_board.clear_all()
        self._qa_save_buttons()

    def _qa_on_board_changed(self):
        has_cards = bool(self._qa_board.action_names())
        self._qa_drop_hint.setVisible(not has_cards)

    def _qa_run_action(self, action_name: str, args: dict):
        """Execute action in worker thread and show result via signal.

        If the action requires an argument and none were supplied (e.g. the
        card was clicked directly), prompt the user with an input dialog
        before dispatching.
        """
        # ── Arg prompting ────────────────────────────────────────────────────
        if not args and action_name in self._QA_ACTION_ARGS:
            prompt_text, arg_key = self._QA_ACTION_ARGS[action_name]
            value, ok = QInputDialog.getText(
                self, action_name.replace("_", " ").title(), prompt_text
            )
            if not ok or not value.strip():
                return
            args = {arg_key: value.strip()}

        self._qa_result.setPlainText(f"⏳ Running {action_name}…")

        def worker():
            try:
                import concurrent.futures
                from eli.execution.executor_enhanced import execute
                # Run with a 60-second timeout to prevent infinite hangs
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(execute, action_name, args)
                    try:
                        result = fut.result(timeout=60)
                    except concurrent.futures.TimeoutError:
                        self._qa_result_sig.emit(f"⏱ {action_name}: timed out after 60s")
                        return
                text = result.get("response") or result.get("content") or str(result)
                ok = result.get("ok", True)
                prefix = "✅" if ok else "❌"
                out = f"{prefix} {action_name}\n{str(text)[:1200]}"
            except Exception as exc:
                out = f"❌ {action_name}: {exc}"
            self._qa_result_sig.emit(out)

        threading.Thread(target=worker, daemon=True).start()

    def _qa_save_buttons(self):
        """Persist pinned actions to quick_actions.json."""
        try:
            import json as _j
            saved = gui_config_dir() / "quick_actions.json"
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(
                _j.dumps(self._qa_board.action_names(), indent=2),
                encoding="utf-8",
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _qa_load_buttons(self):
        """Restore previously pinned actions."""
        try:
            import json as _j
            f = gui_config_dir() / "quick_actions.json"
            if f.exists():
                for name in _j.loads(f.read_text(encoding="utf-8")):
                    self._qa_board.add_card(str(name))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self._qa_on_board_changed()
