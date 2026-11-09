"""Runtime capability enforcement for loaded plugins.

Everything else in this package gates a plugin *before* it runs: the manifest is
checked against the source, the download is hashed, eleven scanners look at it, and
the operator consents per capability. All of that is defeated by one fact — once a
plugin is enabled it is `exec_module`'d into ELI's own interpreter, so the
permission API is **cooperative**. A plugin that passed every check can simply
`import socket` and never call ELI's gated helpers at all.

This closes that. `sys.addaudithook` fires below the Python API, on the actual
operation: `socket.connect`, `open`, `subprocess.Popen`, `os.system`,
`ctypes.dlopen`. Raising inside the hook aborts the operation. The hook attributes
each event to a plugin by walking the stack for a frame belonging to a plugin
package, then enforces that plugin's DECLARED capabilities and the operator's
consent.

Properties worth stating precisely, because "sandbox" oversells it:

  * **It cannot be uninstalled.** CPython provides no way to remove an audit hook,
    so a plugin cannot lift it once ELI has installed it.
  * **It enforces below the API.** Importing `socket` directly does not help; the
    connect still raises.
  * **It is not a security boundary against native code.** A plugin that gets as
    far as running arbitrary machine code — through a compiled extension — is
    outside anything an in-process hook can see, which is why `ctypes.dlopen` is
    itself refused unless declared, and why the manifest checker rejects `ctypes`
    outright.
  * **It does not contain subprocesses.** A plugin granted `process_exec` spawns a
    real child, and nothing here follows it. That is the same limit netguard has,
    and it is why `process_exec` is described to the operator as unlimited access.
  * **Work a plugin hands off is still the plugin's.** A thread started from a plugin
    frame carries the plugin's id, a callable a plugin submits to a thread pool or
    registers with atexit runs as the plugin, and a thread started below the
    `threading` module (raw `_thread`) by a plugin is refused, since it could not be
    attributed. A callback another framework runs later (a Qt timer, an asyncio
    loop ELI owns) is not followed.

What a plugin may do comes from its own manifest (eli_plugin.json). Only a plugin
that ships inside ELI (in ELI's own plugins directory, which is read-only in a
packaged install and is ELI's own source in a checkout) may run without one. A
folder in the user's plugins directory with no valid manifest declares nothing:
everything it attempts is refused, and the manager does not load it.

Only ELI's own code is exempt, and only because nothing outside a plugin directory
can be attributed to a plugin.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Dict, Optional, Set

from eli.utils.log import get_logger

log = get_logger(__name__)

_installed = False
_install_lock = threading.Lock()

# event name → capability it needs. `open` and `sqlite3.connect` are resolved per call.
_EVENT_CAPABILITY = {
    "socket.connect": "network",
    "socket.sendto": "network",          # UDP needs no connect
    "socket.sendmsg": "network",
    "socket.bind": "network",            # listening for the world
    "socket.getaddrinfo": "network",
    "socket.gethostbyname": "network",
    "socket.gethostbyaddr": "network",
    "urllib.Request": "network",
    "webbrowser.open": "process_exec",   # starts a browser
    "subprocess.Popen": "process_exec",
    "os.kill": "process_exec",           # signals another process
    "os.killpg": "process_exec",
    "os.startfile": "process_exec",      # Windows: opens a file with its program
    "os.system": "process_exec",
    "os.exec": "process_exec",
    "os.spawn": "process_exec",
    "os.posix_spawn": "process_exec",
    "os.fork": "process_exec",
    "os.forkpty": "process_exec",
    "pty.spawn": "process_exec",
    "ctypes.dlopen": "process_exec",
    "ctypes.dlsym": "process_exec",
    "os.remove": "filesystem_write",
    "os.rename": "filesystem_write",
    "os.rmdir": "filesystem_write",
    "os.mkdir": "filesystem_write",
    "os.chmod": "filesystem_write",
    "os.truncate": "filesystem_write",
    "shutil.rmtree": "filesystem_write",
    "shutil.copyfile": "filesystem_write",
    "shutil.move": "filesystem_write",
    "shutil.chown": "filesystem_write",
    "shutil.make_archive": "filesystem_write",
    "shutil.unpack_archive": "filesystem_write",
    "os.symlink": "filesystem_write",
    "os.link": "filesystem_write",
    "os.chown": "filesystem_write",
    "os.chflags": "filesystem_write",
    "os.utime": "filesystem_write",
    "os.setxattr": "filesystem_write",
    "os.removexattr": "filesystem_write",
    # sqlite opens its file in C, so no `open` event fires: ELI's memory database, a browser's
    # cookie store. Read or write is decided from the database argument (see _capability_for).
    "sqlite3.connect": "filesystem_write",
}

# A thread being started: the function it will run is the first argument. CPython 3.13 starts
# threading.Thread through start_joinable_thread.
_THREAD_EVENTS = ("_thread.start_new_thread", "_thread.start_joinable_thread")

# Loopback is exempt from the network capability: ELI's own model server, the local
# API and MQTT all live there, and a plugin talking to them is not egress. Anything
# leaving the machine is.
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

_plugin_roots: Dict[str, str] = {}          # resolved plugin dir -> plugin id
_declared: Dict[str, Set[str]] = {}          # plugin id -> declared capabilities
_frame_cache: Dict[str, Optional[str]] = {}  # code filename -> plugin id or None
_session_allowed: Set[tuple] = set()         # (plugin, capability) consented this session
_cache_lock = threading.RLock()
_acting = threading.local()                  # .plugin: the plugin a handed-off callable runs for


def plugin_trust(plugin_dir: Path) -> Dict[str, object]:
    """What a plugin directory may do, from where it lives and its own manifest.

    Returns {caps, shipped, loadable, problem}. A plugin that ships inside ELI (a child of ELI's
    own plugins directory, when that is not also the user's) without a manifest predates the
    marketplace and is fully trusted ('*'). Anything else declares its capabilities in a valid
    eli_plugin.json; without one it declares nothing, everything it attempts is refused, and it
    is not loadable.
    """
    from eli.plugins.manifest import MANIFEST_NAME, load_manifest
    child = Path(plugin_dir)
    try:
        from eli.plugins.manager import _bundled_plugins_dir, _plugins_dir
        bundled = _bundled_plugins_dir().resolve()
        user = _plugins_dir().resolve()
        shipped = child.resolve().parent == bundled and bundled != user
    except Exception:
        log.debug("[SANDBOX] plugin directories unavailable", exc_info=True)
        shipped = False
    manifest = child / MANIFEST_NAME
    if manifest.is_file():
        check = load_manifest(manifest)
        if check["ok"]:
            return {"caps": set(check["manifest"].get("permissions") or []), "shipped": shipped,
                    "loadable": True, "problem": ""}
        return {"caps": set(), "shipped": shipped, "loadable": False,
                "problem": f"its manifest is not valid: {'; '.join(check['problems'][:3])}"}
    if shipped:
        return {"caps": {"*"}, "shipped": True, "loadable": True, "problem": ""}
    return {"caps": set(), "shipped": False, "loadable": False,
            "problem": f"it has no {MANIFEST_NAME}, so it declares nothing it may do"}


def _refresh_roots() -> None:
    """Map every installed plugin directory to its id and declared capabilities."""
    roots: Dict[str, str] = {}
    declared: Dict[str, Set[str]] = {}
    try:
        from eli.plugins.manager import _plugin_search_dirs
        search = _plugin_search_dirs()
    except Exception:
        log.debug("[SANDBOX] could not list plugin directories", exc_info=True)
        return
    for base in search:
        try:
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if not child.is_dir() or child.name in ("base", "registry", "__pycache__"):
                    continue
                roots[str(child.resolve())] = child.name
                declared[child.name] = set(plugin_trust(child)["caps"])
        except Exception:
            log.debug(f"[SANDBOX] could not scan {base}", exc_info=True)
    with _cache_lock:
        _plugin_roots.clear()
        _plugin_roots.update(roots)
        _declared.clear()
        _declared.update(declared)
        _frame_cache.clear()


def _plugin_for_file(filename: str) -> Optional[str]:
    if not filename:
        return None
    try:
        resolved = str(Path(filename).resolve())
    except Exception:
        resolved = filename
    for root, pid in _plugin_roots.items():
        if resolved.startswith(root + os.sep):
            return pid
    return None


def _plugin_in_stack(frame) -> Optional[str]:
    """The innermost plugin whose code is on this stack, walking every frame. Cached per code
    filename (a code object's id is reused once it is freed; its file is not)."""
    while frame is not None:
        filename = frame.f_code.co_filename
        with _cache_lock:
            cached = _frame_cache.get(filename, ...)
        if cached is ...:
            cached = _plugin_for_file(filename)
            with _cache_lock:
                _frame_cache[filename] = cached
        if cached is not None:
            return cached
        frame = frame.f_back
    return None


def _attribute(frame=None) -> Optional[str]:
    """The plugin responsible for the current call, or None for ELI's own code: a plugin frame on
    this thread's stack, else the plugin a handed-off callable (a thread's run, a pool work item,
    an atexit hook) is running for."""
    if frame is None:
        frame = sys._getframe(2) if hasattr(sys, "_getframe") else None
    pid = _plugin_in_stack(frame)
    if pid is not None:
        return pid
    return getattr(_acting, "plugin", None)


def _as_plugin(plugin: str, fn):
    """`fn` run as `plugin`'s work: what it does is attributed to the plugin that handed it off."""
    import functools

    @functools.wraps(fn)
    def _run(*args, **kwargs):
        before = getattr(_acting, "plugin", None)
        _acting.plugin = plugin
        try:
            return fn(*args, **kwargs)
        finally:
            _acting.plugin = before
    return _run


def _thread_started(args) -> None:
    """A thread is starting. One started from a plugin frame is the plugin's: what its run()
    does is attributed to the plugin (the Thread's run is wrapped before the thread begins). A
    thread-pool worker is left alone, since its work items carry their own submitter (see
    _install_handoff_tracking). A raw `_thread` start from a plugin cannot be attributed, so it is
    refused."""
    plugin = _attribute(sys._getframe(2) if hasattr(sys, "_getframe") else None)
    if plugin is None:
        return
    fn = args[0] if args else None
    owner = getattr(fn, "__self__", None)
    if isinstance(owner, threading.Thread):
        target = getattr(owner, "_target", None)
        if getattr(target, "__module__", "") == "concurrent.futures.thread":
            return
        owner.run = _as_plugin(plugin, owner.run)
        return
    raise PermissionError(
        f"Plugin '{plugin}' tried to start a raw thread, which ELI cannot attribute. "
        f"Use the threading module. Blocked.")


def _install_handoff_tracking() -> None:
    """Callables a plugin hands to a thread pool or to atexit run as that plugin."""
    import atexit
    import concurrent.futures.thread as _cft

    original_submit = _cft.ThreadPoolExecutor.submit

    def submit(self, fn, /, *args, **kwargs):
        plugin = _attribute(sys._getframe(1))
        return original_submit(self, _as_plugin(plugin, fn) if plugin else fn, *args, **kwargs)
    submit.__wrapped__ = original_submit
    _cft.ThreadPoolExecutor.submit = submit

    original_register = atexit.register

    def register(func, *args, **kwargs):
        plugin = _attribute(sys._getframe(1))
        original_register(_as_plugin(plugin, func) if plugin else func, *args, **kwargs)
        return func
    register.__wrapped__ = original_register
    atexit.register = register


def _capability_for(event: str, args) -> Optional[str]:
    if event == "sqlite3.connect":
        try:
            database = str(args[0]) if args else ""
        except Exception:
            database = ""
        if database in ("", ":memory:") or "mode=memory" in database:
            return None
        return "filesystem_read" if "mode=ro" in database else "filesystem_write"
    if event == "open":
        try:
            mode = str(args[1] or "r") if len(args) > 1 else "r"
        except Exception:
            mode = "r"
        return "filesystem_write" if any(c in mode for c in "wxa+") else "filesystem_read"
    if event == "socket.connect":
        try:
            address = args[1]
            host = address[0] if isinstance(address, (tuple, list)) else address
            if str(host) in _LOOPBACK:
                return None
        except Exception:
            # Unparseable address — fall through to requiring the network
            # capability. Failing open here would be the one bypass worth having.
            log.debug("[SANDBOX] could not read connect address", exc_info=True)
        return "network"
    return _EVENT_CAPABILITY.get(event)


def _audit(event: str, args) -> None:
    if event in _THREAD_EVENTS:
        _thread_started(args)
        return
    if event not in _EVENT_CAPABILITY and event != "open":
        return
    plugin = _attribute()
    if plugin is None:
        return                      # ELI's own code — not this hook's business
    _enforce(plugin, event, args)


def _enforce(plugin: str, event: str, args) -> None:
    capability = _capability_for(event, args)
    if capability is not None:
        _require(plugin, capability, event)


def _require(plugin: str, capability: str, what: str) -> None:
    """The plugin must have declared `capability` and the operator must allow it, or PermissionError."""
    with _cache_lock:
        declared = _declared.get(plugin)
        already = (plugin, capability) in _session_allowed
    if already:
        return
    if declared and "*" in declared:
        return                      # shipped with ELI

    if declared is not None and capability not in declared:
        raise PermissionError(
            f"Plugin '{plugin}' attempted {what} which needs the '{capability}' "
            f"permission, and its manifest does not declare it. Blocked.")

    # Declared — now the operator's consent decides, once per session per capability.
    try:
        from eli.plugins.permissions import check
        verdict = check(plugin, capability, f"{what} (enforced at runtime)")
    except Exception as exc:
        raise PermissionError(
            f"Plugin '{plugin}' attempted {what} and consent could not be "
            f"established ({exc}). Blocked.") from None
    if not verdict["allowed"]:
        raise PermissionError(
            f"Plugin '{plugin}' is not permitted to {capability.replace('_', ' ')}. "
            f"{verdict['reason']}")
    with _cache_lock:
        _session_allowed.add((plugin, capability))


def install_plugin_sandbox() -> bool:
    """Install the runtime enforcement hook. Idempotent; cannot be undone.

    Returns True if it was installed by this call. Disable with
    ELI_PLUGIN_SANDBOX=0 — which is a real downgrade and should only be used to
    diagnose a plugin ELI is blocking.
    """
    global _installed
    with _install_lock:
        if _installed:
            # Already hooked, but the plugin set may have changed since — an install,
            # an uninstall, or a different plugins directory. Returning without
            # re-reading left stale roots, and a plugin the hook did not recognise is
            # a plugin it silently does not enforce.
            _refresh_roots()
            return False
        if (os.environ.get("ELI_PLUGIN_SANDBOX", "").strip().lower()
                in ("0", "false", "no")):
            log.warning("[SANDBOX] plugin runtime enforcement DISABLED by "
                        "ELI_PLUGIN_SANDBOX — plugins run unrestricted")
            _installed = True
            return False
        _refresh_roots()
        _install_handoff_tracking()
        sys.addaudithook(_audit)
        _installed = True
        log.info(f"[SANDBOX] runtime capability enforcement active for "
                 f"{len(_plugin_roots)} plugin director(ies)")
        return True


def refresh() -> None:
    """Re-read plugin directories and manifests after an install or uninstall."""
    if _installed:
        _refresh_roots()


def enforce(capability: str, what: str = "") -> None:
    """For ELI's own entry points that a plugin can call in-process (memory, the model, the
    clipboard, audio, the screen): when a plugin is behind this call, it must have declared
    `capability` and the operator must allow it, or PermissionError. ELI's own calls pass."""
    if not _installed:
        return
    plugin = _attribute(sys._getframe(1) if hasattr(sys, "_getframe") else None)
    if plugin is not None:
        _require(plugin, capability, what or capability)


def reset_session_grants() -> None:
    with _cache_lock:
        _session_allowed.clear()


def status() -> dict:
    with _cache_lock:
        return {
            "installed": _installed,
            "plugins": dict(_plugin_roots),
            "declared": {k: sorted(v) for k, v in _declared.items()},
            "session_allowed": sorted(_session_allowed),
        }


__all__ = ["install_plugin_sandbox", "refresh", "reset_session_grants", "status", "plugin_trust", "enforce"]
