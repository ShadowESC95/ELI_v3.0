# eli/plugins/manager.py
"""
ELI Plugin Manager
==================
Handles discovery, loading, downloading, installing, enabling and disabling plugins.

Usage (from executor or GUI):
    from eli.plugins.manager import get_manager
    mgr = get_manager()
    mgr.list_available()        # the plugins ELI ships (the bundled registry)
    mgr.list_installed()        # locally installed plugins
    mgr.install("weather")      # enable a shipped plugin; anything else goes through the marketplace
    mgr.uninstall("weather")    # disable + remove files
    mgr.enable("weather")       # re-enable disabled plugin
    mgr.disable("weather")      # disable without removing
    mgr.dispatch("WEATHER_GET_WEATHER", {"city": "Dublin"})   # a loaded plugin's action, by its published name
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from eli.core.legacy_paths import legacy_named_paths, migrate_text_file
from eli.core.paths import get_paths
from eli.utils.log import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _eli_src() -> Path:
    return Path(__file__).resolve().parent.parent


def _bundled_plugins_dir() -> Path:
    """Plugins that ship inside ELI. Read-only on a packaged install."""
    return _eli_src() / "plugins"


def _plugins_dir() -> Path:
    """Where DOWNLOADED plugins are written.

    Was `_eli_src()/plugins` — inside the installation. That works in a dev checkout
    and fails on a packaged build, where project_root() is a read-only mount (the
    same class of bug as test_runtime_paths_follow_the_data_dir). It also mixes a
    stranger's downloaded code in with ELI's own shipped plugins, which makes
    "what did I install" unanswerable.

    Falls back to the bundled directory only if the paths module is unavailable, so
    a dev checkout with no data dir configured still behaves as before. Finding it makes nothing:
    the installer makes the plugin's folder when it writes one (marketplace.install), so listing or
    loading plugins no longer left an empty plugins/ folder in a source checkout.
    """
    try:
        from eli.core.paths import plugins_dir as _pd
        return Path(_pd())
    except Exception:
        return _bundled_plugins_dir()


def _plugin_search_dirs() -> List[Path]:
    """Both places a plugin can live: shipped built-ins and user installs."""
    seen, out = set(), []
    for d in (_bundled_plugins_dir(), _plugins_dir()):
        key = str(d.resolve()) if d.exists() else str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _state_file() -> Path:
    """JSON file tracking enabled/disabled state and metadata per plugin."""
    state_dir = Path(os.environ.get("ELI_ARTIFACTS_DIR", "")).expanduser()
    if not state_dir or not state_dir.is_absolute():
        state_dir = Path(get_paths().config_dir)
    state_file = state_dir / "plugins_state.json"
    migrate_text_file(
        state_file,
        legacy_named_paths("plugins_state.json"),
        default_text=json.dumps({"enabled": [], "disabled": [], "installed": {}}, indent=2),
    )
    return state_file


def _local_registry() -> Path:
    """Bundled fallback registry shipped with ELI."""
    return _eli_src() / "plugins" / "registry" / "index.json"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state() -> Dict[str, Any]:
    p = _state_file()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return {"enabled": [], "disabled": [], "installed": {}}


def _save_state(state: Dict[str, Any]) -> None:
    _state_file().write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _fetch_registry(timeout: int = 8) -> List[Dict[str, Any]]:
    """The plugins ELI ships, from the bundled index.json. Never the network: a plugin from
    anywhere else is a marketplace listing (eli.plugins.marketplace), installed only through its
    checksum, scan, manifest and consent. `timeout` is unused and kept for callers."""
    p = _local_registry()
    if p.exists():
        try:
            return json.loads(p.read_text()).get("plugins", [])
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return []


# ---------------------------------------------------------------------------
# Plugin base (re-exported for convenience)
# ---------------------------------------------------------------------------

from eli.plugins.base.base import Plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class PluginManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded: Dict[str, Plugin] = {}
        self._registry_cache: Optional[List[Dict]] = None
        self._state: Dict[str, Any] = _load_state()
        # Runtime capability enforcement goes in BEFORE the first plugin is
        # exec_module'd. A plugin loaded ahead of the audit hook would have a window
        # in which its module-level code ran unrestricted.
        try:
            from eli.plugins.sandbox import install_plugin_sandbox
            install_plugin_sandbox()
        except Exception:
            log.debug("[PLUGIN] runtime sandbox unavailable", exc_info=True)
        self._auto_load()

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------


    def list_available(self) -> List[Dict]:
        """Return all plugins from registry (cached)."""
        if self._registry_cache is None:
            self._registry_cache = _fetch_registry()
        return self._registry_cache

    def get_registry_entry(self, plugin_id: str) -> Optional[Dict]:
        for p in self.list_available():
            if p["id"] == plugin_id:
                return p
        return None

    # ------------------------------------------------------------------
    # Discovery & loading
    # ------------------------------------------------------------------

    @staticmethod
    def _all_plugin_dirs() -> List[Path]:
        """Every candidate plugin package across both search directories. A user
        install of the same id shadows the bundled one (last writer wins)."""
        found: Dict[str, Path] = {}
        for root in _plugin_search_dirs():
            try:
                if not root.is_dir():
                    continue
                for child in sorted(root.iterdir()):
                    if child.is_dir() and child.name not in ("base", "registry", "__pycache__"):
                        found[child.name] = child
            except Exception:
                log.debug(f"[PLUGIN] could not scan {root}", exc_info=True)
        return list(found.values())

    def _auto_load(self) -> None:
        """Load every plugin on disk that has not been disabled, at startup."""
        for pkg_dir in self._all_plugin_dirs():
            plugin_py = pkg_dir / "plugin.py"
            if not plugin_py.exists():
                continue
            pid = pkg_dir.name
            if pid in ("base", "registry", "__pycache__"):
                continue
            if self._state.get("disabled") and pid in self._state["disabled"]:
                continue
            try:
                self._load_plugin_from_file(pid, plugin_py)
            except Exception as exc:
                log.debug(f"[PLUGIN] Failed to auto-load {pid}: {exc}")

    def _load_plugin_from_file(self, plugin_id: str, path: Path) -> Optional[Plugin]:
        # Only a plugin that ships with ELI, or one whose own valid manifest says what it may do,
        # is executed. A folder dropped into the user's plugins directory (or a plugin whose
        # manifest went missing) would otherwise run with every capability unchecked.
        from eli.plugins.sandbox import plugin_trust
        trust = plugin_trust(Path(path).parent)
        if not trust["loadable"]:
            log.warning(f"[PLUGIN] not loading {plugin_id}: {trust['problem']}")
            return None
        spec = importlib.util.spec_from_file_location(
            f"plugins.{plugin_id}.plugin", path
        )
        if spec is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"plugins.{plugin_id}.plugin"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        # Find Plugin subclass
        import inspect
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if inspect.isclass(attr) and issubclass(attr, Plugin) and attr is not Plugin:
                instance = attr()
                with self._lock:
                    self._loaded[plugin_id] = instance
                log.debug(f"[PLUGIN] Loaded: {plugin_id} — {instance.description}")
                return instance
        return None

    def list_installed(self) -> List[Dict]:
        """Plugins with a local plugin.py file."""
        result = []
        for pkg_dir in self._all_plugin_dirs():
            plugin_py = pkg_dir / "plugin.py"
            if not plugin_py.exists():
                continue
            pid = pkg_dir.name
            if pid in ("base", "registry", "__pycache__"):
                continue
            disabled = pid in self._state.get("disabled", [])
            meta = self._state.get("installed", {}).get(pid, {})
            result.append({
                "id": pid,
                "enabled": not disabled,
                "loaded": pid in self._loaded,
                "version": meta.get("version", "?"),
                "description": meta.get("description", ""),
            })
        return result

    # ------------------------------------------------------------------
    # Install / uninstall
    # ------------------------------------------------------------------

    def install(
        self,
        plugin_id: str,
        progress_cb: Optional[Callable[[str], None]] = None,
        *,
        confirm: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Dict[str, Any]:
        """
        Install a plugin by id.

        A plugin that ships with ELI is enabled from the copy ELI ships: nothing is downloaded or
        installed, and the Python packages it lists must already be present (ELI's own
        requirements carry them). Any other id is a marketplace listing and goes through
        marketplace.install — checksum, signature, scan, manifest check, and `confirm` (the
        operator's consent; none means refused) — and is installed switched off.
        """
        def _log(msg: str) -> None:
            log.debug(f"[PLUGIN:install:{plugin_id}] {msg}")
            if progress_cb:
                progress_cb(msg)

        shipped = _bundled_plugins_dir() / plugin_id / "plugin.py"
        if not shipped.is_file():
            return self._install_from_marketplace(plugin_id, confirm=confirm, log_cb=_log)

        entry = self.get_registry_entry(plugin_id) or {"id": plugin_id, "name": plugin_id, "version": "?"}
        _log(f"Enabling the {entry.get('name', plugin_id)} plugin that ships with ELI…")
        missing = []
        for pkg in entry.get("pip", []) or []:
            name = str(pkg).split(";")[0].strip()
            for sep in ("[", "=", "<", ">", "!", "~", " "):
                name = name.split(sep)[0]
            try:
                from importlib.metadata import PackageNotFoundError, version
                version(name)
            except PackageNotFoundError:
                missing.append(str(pkg))
            except Exception:
                log.debug(f"[PLUGIN] could not check {pkg}", exc_info=True)
                missing.append(str(pkg))
        if missing:
            return {"ok": False, "error": (f"'{entry.get('name', plugin_id)}' needs the Python package(s) "
                                           f"{', '.join(missing)}, which are not installed in this ELI. "
                                           "ELI does not install packages for a plugin; nothing was changed.")}

        state = _load_state()
        enabled = set(state.get("enabled", []))
        disabled = set(state.get("disabled", []))
        enabled.add(plugin_id)
        disabled.discard(plugin_id)
        state["enabled"] = sorted(enabled)
        state["disabled"] = sorted(disabled)
        state.setdefault("installed", {})[plugin_id] = {
            "version": entry.get("version", "?"),
            "description": entry.get("description", ""),
            "source": "builtin",
        }
        _save_state(state)
        self._state = state

        try:
            from eli.plugins.sandbox import refresh as _sandbox_refresh
            _sandbox_refresh()
        except Exception:
            log.debug("[PLUGIN] sandbox refresh failed", exc_info=True)

        loaded = self._load_plugin_from_file(plugin_id, shipped)
        if loaded is None:
            return {"ok": False, "error": "Plugin enabled but could not be loaded"}

        _log(f"  ✅ {entry.get('name', plugin_id)} enabled and loaded")
        return {
            "ok": True,
            "plugin_id": plugin_id,
            "name": entry.get("name", plugin_id),
            "actions": entry.get("actions", []),
            "content": f"✅ Plugin '{entry.get('name', plugin_id)}' installed successfully.",
            "response": f"✅ Plugin '{entry.get('name', plugin_id)}' installed successfully.",
        }

    @staticmethod
    def _install_from_marketplace(plugin_id: str, *, confirm, log_cb) -> Dict[str, Any]:
        """A plugin that does not ship with ELI: its marketplace listing, through the verified path."""
        try:
            from eli.plugins import marketplace
            listing = next((m for m in marketplace.search(plugin_id)
                            if str(m.get("id", "")).lower() == plugin_id.lower()), None)
        except Exception as exc:
            return {"ok": False, "error": f"Plugin '{plugin_id}' does not ship with ELI, and the "
                                          f"marketplace could not be read: {exc}"}
        if listing is None:
            return {"ok": False, "error": f"Plugin '{plugin_id}' does not ship with ELI and no "
                                          f"marketplace lists it"}
        log_cb(f"Installing {listing.get('name', plugin_id)} from {listing.get('registry_label') or 'the marketplace'}…")
        result = marketplace.install(listing, confirm=confirm)
        if not result.get("ok"):
            result.setdefault("error", "; ".join(result.get("problems") or []) or "installation refused")
        return result

    def uninstall(self, plugin_id: str) -> Dict[str, Any]:
        """Remove plugin files and state."""
        import shutil

        # Only a downloaded plugin's files are removed: a built-in is ELI's own code (in a source
        # checkout, the repository), so uninstalling one disables it. This used to delete the
        # shipped plugin's folder whenever no downloaded copy existed.
        plugin_dir = _plugins_dir() / plugin_id
        if plugin_dir.exists() and plugin_dir.resolve() != (_bundled_plugins_dir() / plugin_id).resolve():
            shutil.rmtree(plugin_dir)

        state = _load_state()
        state.get("enabled", []).remove(plugin_id) if plugin_id in state.get("enabled", []) else None
        state.get("disabled", []).remove(plugin_id) if plugin_id in state.get("disabled", []) else None
        state.get("installed", {}).pop(plugin_id, None)
        _save_state(state)
        self._state = state

        with self._lock:
            self._loaded.pop(plugin_id, None)

        # Remove from sys.modules
        for key in list(sys.modules.keys()):
            if key.startswith(f"plugins.{plugin_id}"):
                del sys.modules[key]

        try:
            from eli.plugins.sandbox import refresh as _sandbox_refresh
            _sandbox_refresh()
        except Exception:
            log.debug("[PLUGIN] sandbox refresh failed", exc_info=True)

        return {
            "ok": True,
            "content": f"🗑️ Plugin '{plugin_id}' uninstalled.",
            "response": f"🗑️ Plugin '{plugin_id}' uninstalled.",
        }

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    def dispatch(self, action: str, args: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Run an installed plugin's own action, or None if no plugin claims it.

        This is the step that was missing, and without it the marketplace stopped
        one move short of useful: a plugin could be downloaded, verified, scanned,
        consented to, written to disk, enabled and imported — and then nothing
        could call it. `Plugin.register()` did publish actions into
        `capability_registry`, but that registry is a CATALOGUE (`list_capabilities`
        backs the docs and compatibility surfaces); nothing ever looked a `handler`
        back up and invoked it. And `base.load_plugins()`, which drove registration,
        enumerates the `eli.plugins` **source package**, so a plugin installed into
        the user's plugins directory was never among the ones it registered.

        Resolution happens against `self._loaded` at call time rather than through a
        table built at startup, so a plugin enabled mid-session is callable
        immediately — no restart, which is what "as easy as clicking install"
        requires.

        Action names follow `Plugin.register`'s convention, `<PLUGIN>_<ACTION>`, and
        a bare action name is accepted when exactly one enabled plugin offers it.
        Ambiguity is reported rather than guessed: silently picking one of two
        plugins that both define `SEARCH` is how a plugin ends up shadowing another.
        """
        name = str(action or "").strip().upper()
        if not name:
            return None

        enabled = set(_load_state().get("enabled", []))
        candidates = []
        for pid, inst in list(self._loaded.items()):
            if pid not in enabled:
                continue
            for act in (getattr(inst, "actions", {}) or {}):
                qualified = f"{str(getattr(inst, 'name', pid)).upper()}_{str(act).upper()}"
                if name == qualified or name == str(act).upper():
                    candidates.append((pid, inst, act))

        if not candidates:
            return None
        if len(candidates) > 1:
            exact = [c for c in candidates
                     if name == f"{str(getattr(c[1], 'name', c[0])).upper()}_{str(c[2]).upper()}"]
            if len(exact) == 1:
                candidates = exact
            else:
                names = ", ".join(f"{c[0]}_{c[2]}".upper() for c in candidates[:5])
                msg = (f"'{name}' is offered by more than one enabled plugin ({names}). "
                       f"Use the full <PLUGIN>_<ACTION> name.")
                return {"ok": False, "action": name, "error": msg,
                        "content": msg, "response": msg}

        pid, inst, act = candidates[0]
        try:
            result = inst.execute(act, dict(args or {}))
        except Exception as exc:
            log.debug(f"[PLUGIN] {pid}.{act} raised", exc_info=True)
            msg = f"Plugin '{pid}' failed running {act}: {exc}"
            return {"ok": False, "action": name, "plugin": pid,
                    "error": str(exc), "content": msg, "response": msg}

        if not isinstance(result, dict):
            text = str(result)
            return {"ok": True, "action": name, "plugin": pid,
                    "content": text, "response": text}
        result.setdefault("ok", True)
        result.setdefault("action", name)
        result["plugin"] = pid
        text = result.get("content") or result.get("response") or ""
        result.setdefault("content", text)
        result.setdefault("response", text)
        return result

    def enable(self, plugin_id: str) -> Dict[str, Any]:
        plugin_py = _plugins_dir() / plugin_id / "plugin.py"
        if not plugin_py.exists():
            plugin_py = _bundled_plugins_dir() / plugin_id / "plugin.py"
        if not plugin_py.exists():
            return {"ok": False, "error": f"Plugin '{plugin_id}' not installed"}
        from eli.plugins.sandbox import plugin_trust
        trust = plugin_trust(plugin_py.parent)
        if not trust["loadable"]:
            return {"ok": False, "error": f"Plugin '{plugin_id}' cannot be enabled: {trust['problem']}"}

        state = _load_state()
        disabled = set(state.get("disabled", []))
        disabled.discard(plugin_id)
        enabled = set(state.get("enabled", []))
        enabled.add(plugin_id)
        state["enabled"] = sorted(enabled)
        state["disabled"] = sorted(disabled)
        _save_state(state)
        self._state = state

        if plugin_id not in self._loaded:
            self._load_plugin_from_file(plugin_id, plugin_py)

        return {
            "ok": True,
            "content": f"✅ Plugin '{plugin_id}' enabled.",
            "response": f"✅ Plugin '{plugin_id}' enabled.",
        }

    def disable(self, plugin_id: str) -> Dict[str, Any]:
        state = _load_state()
        enabled = set(state.get("enabled", []))
        enabled.discard(plugin_id)
        disabled = set(state.get("disabled", []))
        disabled.add(plugin_id)
        state["enabled"] = sorted(enabled)
        state["disabled"] = sorted(disabled)
        _save_state(state)
        self._state = state

        with self._lock:
            self._loaded.pop(plugin_id, None)

        return {
            "ok": True,
            "content": f"⏸ Plugin '{plugin_id}' disabled.",
            "response": f"⏸ Plugin '{plugin_id}' disabled.",
        }

    # ------------------------------------------------------------------
    # Search (semantic matching against plugin descriptions/actions)
    # ------------------------------------------------------------------

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Find plugins matching a natural language query.
        Searches plugin names, descriptions, and action names.
        Returns registry entries sorted by relevance.
        """
        q = (query or "").strip().lower()
        if not q:
            return []

        q_words = set(q.split())
        scored: List[tuple] = []

        for entry in self.list_available():
            score = 0.0
            pid = str(entry.get("id", "")).lower()
            name = str(entry.get("name", "")).lower()
            desc = str(entry.get("description", "")).lower()
            actions = [str(a).lower() for a in entry.get("actions", [])]
            all_text = f"{pid} {name} {desc} {' '.join(actions)}"

            # Exact id/name match
            if q == pid or q == name:
                score += 10.0
            # Query is substring of name or description
            if q in name:
                score += 5.0
            if q in desc:
                score += 3.0
            # Word overlap
            text_words = set(all_text.split())
            overlap = q_words & text_words
            if overlap:
                score += len(overlap) * 2.0
            # Partial word matching (e.g. "timer" matches "pomodoro timer")
            for qw in q_words:
                if any(qw in tw for tw in text_words):
                    score += 1.0

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored]


    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: Optional[PluginManager] = None
_manager_lock = threading.Lock()


def get_manager() -> PluginManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PluginManager()
    return _manager
