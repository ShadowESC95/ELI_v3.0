#!/usr/bin/env python3
"""
Cross-platform OS controller for ELI.

Linux desktop integrations still use native tools when available, but Windows
and macOS now get platform-aware screenshot, volume, keyboard, and clipboard
fallbacks instead of falling through to Linux-only binaries.
"""

import os
import re
import threading
import time
from datetime import datetime
import subprocess
import shutil
from typing import Optional, Dict, Any
from eli.utils import platform_compat as platform
from eli.utils.platform_compat import LINUX, WINDOWS, MACOS


from eli.utils.log import get_logger
log = get_logger(__name__)

# ----------------------------------------------------------------------
# Environment detection
# ----------------------------------------------------------------------
SESSION_TYPE = os.environ.get('XDG_SESSION_TYPE', 'x11').lower()
DESKTOP = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
IS_WAYLAND = SESSION_TYPE == 'wayland'
IS_X11 = SESSION_TYPE == 'x11'
IS_GNOME = 'gnome' in DESKTOP

def _check_tool(tool: str) -> bool:
    """Return True if tool is installed and executable."""
    return shutil.which(tool) is not None


def _screenshot_path(prefix: str = "Screenshot", *, keep: bool = True) -> str:
    """Where a screenshot goes: the user's Pictures folder when they asked for one, ELI's own data
    folder when ELI takes it to read the screen (finding a button by its label filled Pictures)."""
    folder = platform.user_pictures_dir() if keep else _own_captures_dir()
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
    return str(folder / filename)


def _own_captures_dir():
    from eli.core.paths import data_dir
    return data_dir() / "screens"


_STARTED = time.time()


def _prune_own_captures(keep_path: str = "") -> None:
    """Pictures ELI took for its own reading (keep=False) are kept for this run of ELI only: those
    from an earlier run are removed, so the folder is not a growing archive of everything that was
    on screen."""
    folder = _own_captures_dir()
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if str(entry) != keep_path and entry.is_file() and entry.stat().st_mtime < _STARTED:
                entry.unlink()
        except OSError:
            log.debug("could not remove an earlier capture %s", entry, exc_info=True)


def _written(path: str) -> bool:
    """A capture counts only when its file is there: gnome-screenshot on GNOME 50 exits 0 having
    written nothing, and that was reported as a screenshot."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _saved(path: str) -> Dict[str, Any]:
    return {"ok": True, "path": path, "content": f"Screenshot saved: {path}", "response": f"Screenshot saved: {path}"}


def _screenshot_folders() -> list:
    """Where screenshots land on this machine: the (localised) Pictures folder, its Screenshots
    folder, and the Desktop."""
    found: list = []
    try:
        dirs = platform.user_dirs()
        named = [dirs.get("pictures"), dirs.get("desktop")]
    except Exception:
        named = []
    for folder in [platform.user_pictures_dir(), *named]:
        if not folder:
            continue
        folder = os.path.expanduser(str(folder))
        for candidate in (folder, os.path.join(folder, "Screenshots")):
            if candidate not in found and os.path.isdir(candidate):
                found.append(candidate)
    return found


PORTAL_PICTURE_NAME = re.compile(r"^Screenshot(?:-\d+)?\.png$")
"""How the desktop's portal names the picture it saves in the Pictures folder (GNOME's portal:
Screenshot.png, Screenshot-1.png, ...). ELI's own captures and the desktop's own screenshot tool use
other names, so a picture the user took is never mistaken for the portal's."""


def latest_screenshot(since: Optional[float] = None, folders: Optional[list] = None,
                      name: Optional["re.Pattern"] = None) -> Optional[str]:
    """The newest screenshot-named picture (png or jpg) in `folders` (default: where screenshots land
    on this machine), or None. `since`: only one written at or after that time, which is how a
    picture the desktop's portal saved for a request is told from older ones. `name`: only files
    whose name matches it."""
    import glob
    candidates = set()
    for folder in (folders if folders is not None else _screenshot_folders()):
        for pattern in ("*creenshot*.png", "*creenshot*.jpg"):
            candidates.update(c for c in glob.glob(os.path.join(str(folder), pattern))
                              if name is None or name.match(os.path.basename(c)))
    best, best_time = None, None
    for candidate in candidates:
        try:
            written = os.path.getmtime(candidate)
        except OSError:
            continue
        if since is not None and written < since:
            continue
        if best_time is None or written > best_time:
            best, best_time = candidate, written
    return best


# ----------------------------------------------------------------------
# How long each way of capturing may take: measured on this desktop
# ----------------------------------------------------------------------
FIRST_TRY_CEILING_S = 120.0
"""The longest ELI waits for a way of capturing it has not seen work on this desktop, and the bound
on one whole capture. The desktop's portal may be showing its own permission question and an area
capture waits for the user to drag a box, so this is the time a person needs to answer one dialog.
Once a way has worked here, its own measured times set its limit."""

STALL_FACTOR = 10.0
"""A way that has taken this many times its usual time here has stalled: load slows a capture by a
few times, not by ten."""

TIMES_KEPT = 8
"""How many recent successful times are kept for a way: enough to see this machine's spread, few
enough that one slow first run (a permission dialog answered) ages out."""

_RECORD_LOCK = threading.Lock()


def _desktop_key() -> str:
    """Which desktop a record is for: the OS, the session type and the desktop's name."""
    if WINDOWS:
        return "windows"
    if MACOS:
        return "macos"
    return f"linux|{SESSION_TYPE or '?'}|{DESKTOP or '?'}"


def _record_file():
    from eli.core.paths import data_dir
    return data_dir() / "runtime" / "screen_capture.json"


def _load_record() -> Dict[str, Any]:
    import json
    try:
        data = json.loads(_record_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ways_record(kind: str) -> Dict[str, Any]:
    """{way: {"times": [seconds of recent successes], "last": status, "at": time}} for this desktop."""
    entry = _load_record().get(_desktop_key(), {})
    return entry.get(kind, {}) if isinstance(entry, dict) else {}


def _note_outcome(kind: str, outcome: Dict[str, Any]) -> None:
    """Keep what a way did on this desktop: its time when it worked, and its last status."""
    import json
    import tempfile
    if outcome.get("status") in ("unavailable", "skipped", "cancelled"):
        return        # not tried, or the user said no: neither says whether the way works here
    with _RECORD_LOCK:
        data = _load_record()
        desktop = data.setdefault(_desktop_key(), {})
        ways = desktop.setdefault(kind, {})
        way = ways.setdefault(outcome["way"], {"times": []})
        if outcome["status"] == "ok":
            way["times"] = (list(way.get("times") or []) + [round(float(outcome["seconds"]), 3)])[-TIMES_KEPT:]
        way["last"] = outcome["status"]
        way["at"] = time.time()
        target = _record_file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, scratch = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1)
            os.replace(scratch, target)
        except OSError:
            log.debug("could not keep the screen-capture record", exc_info=True)


def _limit_for(record: Dict[str, Any]) -> float:
    """A way's time limit here: STALL_FACTOR times the median of its recent successes, at most the
    first-try ceiling; the ceiling itself when it has not worked here yet or last ran out of time
    (it may have been waiting on a dialog)."""
    times = sorted(float(t) for t in (record or {}).get("times") or [] if float(t) >= 0)
    if not times or (record or {}).get("last") == "timeout":
        return FIRST_TRY_CEILING_S
    middle = len(times) // 2
    median = times[middle] if len(times) % 2 else (times[middle - 1] + times[middle]) / 2
    return min(FIRST_TRY_CEILING_S, max(median, 1e-3) * STALL_FACTOR)


def _rank(record: Dict[str, Any]) -> tuple:
    """Order of trying on this desktop: what last worked here (most recent first), then ways not
    tried yet, then ways that worked once but failed last, then ways that have never worked here."""
    if not record:
        return (1, 0.0)
    if record.get("last") == "ok":
        return (0, -float(record.get("at") or 0.0))
    return (2 if record.get("times") else 3, float(record.get("at") or 0.0))


def _outcome(way: str, status: str, seconds: float, detail: str = "", exit_code: Optional[int] = None,
             limit: Optional[float] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"way": way, "status": status, "seconds": round(float(seconds), 3)}
    if detail:
        out["detail"] = detail
    if exit_code is not None:
        out["exit_code"] = exit_code
    if limit is not None:
        out["limit_s"] = round(float(limit), 1)
    return out


def _capture_with_tool(way: str, cmd: list, path: str, limit: float) -> Dict[str, Any]:
    """Run a capture program within `limit` seconds: ok when it wrote the picture, otherwise why not
    (its exit code and what it said, or that it ran out of time)."""
    started = time.monotonic()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return _outcome(way, "timeout", time.monotonic() - started, f"no picture within {limit:.0f} s", limit=limit)
    except OSError as exc:
        return _outcome(way, "failed", time.monotonic() - started, str(exc))
    took = time.monotonic() - started
    if done.returncode == 0 and _written(path):
        return _outcome(way, "ok", took)
    said = " ".join(((done.stderr or "") + " " + (done.stdout or "")).split())[-300:]
    if done.returncode == 0:
        return _outcome(way, "failed", took, "exited without writing a picture" + (f": {said}" if said else ""), 0)
    return _outcome(way, "failed", took, said or f"exited with {done.returncode}", done.returncode)


def _capture_with_imagegrab(path: str) -> Dict[str, Any]:
    """Pillow's capture: GDI on Windows (every monitor), XCB on X11, screencapture on macOS. Not on
    Wayland, where it sees only XWayland's windows and falls back to a tool it runs without a time
    limit."""
    started = time.monotonic()
    try:
        from PIL import ImageGrab  # type: ignore

        img = ImageGrab.grab(all_screens=True) if WINDOWS else ImageGrab.grab()
        img.save(path)
    except Exception as exc:
        log.debug("Pillow screen grab failed", exc_info=True)
        return _outcome("Pillow", "failed", time.monotonic() - started, f"{type(exc).__name__}: {exc}")
    took = time.monotonic() - started
    return _outcome("Pillow", "ok", took) if _written(path) else _outcome("Pillow", "failed", took, "no picture came back")


# ----------------------------------------------------------------------
# The desktop portal (xdg-desktop-portal): asked and answered on one D-Bus connection
# ----------------------------------------------------------------------
PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_REQUEST = "org.freedesktop.portal.Request"
_PORTAL_TOKENS = __import__("itertools").count(1)

PORTAL_POLL_S = 0.25
"""How often the Pictures folder is looked at for a picture the portal saved, when there is no D-Bus
library to hear its answer: below what a person notices."""


async def _session_bus():
    """A connection to the session bus (dbus-fast). Tests put a stand-in here."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    return await MessageBus(bus_type=BusType.SESSION).connect()


def _request_path(unique_name: str, token: str) -> str:
    """Where the portal answers a request: its Request object for this connection and token."""
    return f"{PORTAL_PATH}/request/{unique_name.lstrip(':').replace('.', '_')}/{token}"


def portal_request(interface: str, method: str, signature: str, body: list, options: Dict[str, Any],
                   *, limit: float) -> Dict[str, Any]:
    """Ask the desktop portal for something and wait for its answer, on ONE D-Bus connection in this
    process. The portal sends its Response signal to the connection that made the request, on the
    Request object named from that connection and the handle_token, so the match for it is set up
    on the same connection before the call goes out (as ELI VR's portal bridge does). Asking with a
    `gdbus call` process and listening with a separate `gdbus monitor` never heard the answer: every
    screen read on GNOME waited 90 s and failed although the portal had taken the picture.

    `options` maps option names to dbus_fast Variants; handle_token is added here. Returns
    {"status": "answered" | "timeout" | "refused" | "unavailable", "code": the portal's response
    (0 done, 1 cancelled by the user, 2 ended another way), "results": {name: value},
    "seconds": float, "detail": str, "version": the interface's version}."""
    import asyncio
    started = time.monotonic()

    def _done(status: str, detail: str = "", **extra) -> Dict[str, Any]:
        out = {"status": status, "seconds": round(time.monotonic() - started, 3), "detail": detail}
        out.update(extra)
        return out

    try:
        from dbus_fast import Message, MessageType, Variant
    except ImportError as exc:
        return _done("unavailable", f"no D-Bus library in this Python ({exc})")
    token = f"eli_{os.getpid()}_{next(_PORTAL_TOKENS)}"
    stage = {"now": "connecting to the session bus"}

    async def _ask() -> Dict[str, Any]:
        bus = await _session_bus()
        try:
            answer = asyncio.get_running_loop().create_future()
            handle = _request_path(bus.unique_name, token)
            watched = {handle}

            def _on_message(msg):
                if (msg.message_type == MessageType.SIGNAL and msg.interface == _PORTAL_REQUEST
                        and msg.member == "Response" and msg.path in watched and not answer.done()):
                    answer.set_result(list(msg.body))
                return None

            async def _match(path: str) -> None:
                rule = f"type='signal',interface='{_PORTAL_REQUEST}',member='Response',path='{path}'"
                await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                       interface="org.freedesktop.DBus", member="AddMatch",
                                       signature="s", body=[rule]))

            bus.add_message_handler(_on_message)
            stage["now"] = "asking the portal"
            await _match(handle)
            got = await bus.call(Message(destination=PORTAL_BUS, path=PORTAL_PATH,
                                         interface="org.freedesktop.DBus.Properties", member="Get",
                                         signature="ss", body=[interface, "version"]))
            if got.message_type == MessageType.ERROR:
                why = f"{got.error_name}: {got.body[0] if got.body else ''}".strip(": ")
                return _done("unavailable", f"the desktop's portal has no {interface.rsplit('.', 1)[-1]} "
                                            f"interface ({why})")
            version = got.body[0].value if got.body else None
            asked = dict(options or {})
            asked["handle_token"] = Variant("s", token)
            reply = await bus.call(Message(destination=PORTAL_BUS, path=PORTAL_PATH, interface=interface,
                                           member=method, signature=signature, body=list(body) + [asked]))
            if reply.message_type == MessageType.ERROR:
                why = f"{reply.error_name}: {reply.body[0] if reply.body else ''}".strip(": ")
                return _done("refused", why, version=version)
            returned = str(reply.body[0]) if reply.body else handle
            if returned not in watched:            # portals before 0.9 name the request themselves
                watched.add(returned)
                await _match(returned)
            stage["now"] = "waiting for the portal's answer"
            code, results = await answer
            values = {k: getattr(v, "value", v) for k, v in dict(results or {}).items()}
            return _done("answered", code=int(code), results=values, version=version)
        finally:
            bus.disconnect()

    async def _bounded() -> Dict[str, Any]:
        try:
            return await asyncio.wait_for(_ask(), timeout=max(0.0, limit))
        except asyncio.TimeoutError:
            return _done("timeout", f"no answer within {limit:.0f} s while {stage['now']}")
        except (OSError, EOFError) as exc:
            return _done("unavailable", f"no session bus ({type(exc).__name__}: {exc})")
        except Exception as exc:              # dbus-fast's own errors (auth, a closed bus)
            return _done("unavailable", f"the session bus failed ({type(exc).__name__}: {exc})")

    # Its own event loop on its own thread: the caller may be inside one already (the API's).
    box: Dict[str, Any] = {}

    def _run() -> None:
        box["out"] = asyncio.run(_bounded())

    worker = threading.Thread(target=_run, name="eli-portal-request", daemon=True)
    worker.start()
    worker.join()
    return box.get("out") or _done("unavailable", "the portal request did not run")


def _take_portal_file(source: str, path: str) -> bool:
    """Move the picture the portal saved to `path`; True when it is there."""
    try:
        if os.path.abspath(source) != os.path.abspath(path):
            shutil.move(source, path)
    except (OSError, shutil.Error):
        log.debug("could not move the portal's picture %s", source, exc_info=True)
        return False
    return _written(path)


def _capture_with_portal(path: str, *, interactive: bool, limit: float) -> Dict[str, Any]:
    """The desktop's own screenshot service on Linux (xdg-desktop-portal: GNOME, KDE, wlroots
    compositors through xdg-desktop-portal-wlr, Hyprland, and X11 desktops that run it). The first
    time, the desktop may ask the user whether ELI may take screenshots. The picture it saves is
    moved to `path`."""
    way = "the desktop's screenshot portal"
    asked_at = time.time()
    try:
        from dbus_fast import Variant
    except ImportError:
        return _capture_with_portal_gdbus(path, interactive=interactive, limit=limit)
    out = portal_request("org.freedesktop.portal.Screenshot", "Screenshot", "sa{sv}", [""],
                         {"interactive": Variant("b", bool(interactive))}, limit=limit)
    seconds = float(out.get("seconds") or 0.0)
    if out["status"] == "answered":
        code = out.get("code")
        if code == 0:
            uri = str((out.get("results") or {}).get("uri") or "")
            import urllib.parse
            source = urllib.parse.unquote(urllib.parse.urlparse(uri).path) if uri else ""
            if source and _take_portal_file(source, path):
                return _outcome(way, "ok", seconds)
            return _outcome(way, "failed", seconds, f"the portal answered but its picture is not there ({uri or 'no uri'})")
        if code == 1:
            return _outcome(way, "cancelled", seconds, "the screenshot was cancelled on the desktop")
        return _outcome(way, "failed", seconds, f"the portal ended the request (response {code})")
    late = _portal_picture_since(asked_at)
    if late and _take_portal_file(late, path):          # it saved the picture after all
        return _outcome(way, "ok", seconds, "the picture was found in the Pictures folder")
    status = {"timeout": "timeout", "refused": "refused", "unavailable": "unavailable"}.get(out["status"], "failed")
    return _outcome(way, status, seconds, str(out.get("detail") or ""), limit=limit if status == "timeout" else None)


def _portal_folders() -> list:
    """Where the portal saves a screenshot: the top of the (localised) Pictures folder."""
    found: list = []
    try:
        named = platform.user_dirs().get("pictures")
    except Exception:
        named = None
    for folder in (named, platform.user_pictures_dir()):
        folder = os.path.expanduser(str(folder or ""))
        if folder and folder not in found and os.path.isdir(folder):
            found.append(folder)
    return found


def _portal_picture_since(asked_at: float) -> Optional[str]:
    """A picture the portal saved in the Pictures folder at or after `asked_at`."""
    return latest_screenshot(since=asked_at, folders=_portal_folders(), name=PORTAL_PICTURE_NAME)


def _capture_with_portal_gdbus(path: str, *, interactive: bool, limit: float) -> Dict[str, Any]:
    """Without a D-Bus library: ask through `gdbus call`. Its answer goes to the gdbus process, which
    has left by then, so the picture is taken from where the portal saves it: the newest screenshot
    in the Pictures folder written after the request."""
    way = "the desktop's screenshot portal"
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return _outcome(way, "unavailable", 0.0, "no D-Bus library in this Python and no gdbus program")
    started, asked_at = time.monotonic(), time.time()
    token = f"eli_{os.getpid()}_{next(_PORTAL_TOKENS)}"
    options = f"{{'handle_token': <'{token}'>, 'interactive': <{'true' if interactive else 'false'}>}}"
    try:
        call = subprocess.run([gdbus, "call", "--session", "--dest", PORTAL_BUS, "--object-path", PORTAL_PATH,
                               "--method", "org.freedesktop.portal.Screenshot.Screenshot", "", options],
                              capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return _outcome(way, "timeout", time.monotonic() - started, f"gdbus got no reply within {limit:.0f} s", limit=limit)
    except OSError as exc:
        return _outcome(way, "failed", time.monotonic() - started, str(exc))
    if call.returncode != 0:
        said = " ".join((call.stderr or "").split())[-300:]
        status = "unavailable" if "UnknownMethod" in said or "ServiceUnknown" in said or "No such interface" in said else "refused"
        return _outcome(way, status, time.monotonic() - started, said or f"gdbus exited with {call.returncode}")
    deadline = started + limit
    while time.monotonic() < deadline:
        late = _portal_picture_since(asked_at)
        if late and _take_portal_file(late, path):
            return _outcome(way, "ok", time.monotonic() - started)
        time.sleep(PORTAL_POLL_S)
    return _outcome(way, "timeout", time.monotonic() - started, f"no picture within {limit:.0f} s", limit=limit)


def _macos_screen_recording_allowed() -> Optional[bool]:
    """macOS 10.15 and later: whether ELI may record the screen. Without it a capture shows the
    desktop picture and none of the windows. None where it cannot be asked."""
    try:
        import ctypes
        import ctypes.util
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def _capture_windows_area(path: str, limit: float) -> Dict[str, Any]:
    """Windows: the user picks the area with Windows' own snip overlay (ms-screenclip:), which puts
    the picture on the clipboard; it is saved from there."""
    way = "Windows' snip overlay"
    started = time.monotonic()
    try:
        from PIL import ImageGrab  # type: ignore

        def _clip_bytes():
            img = ImageGrab.grabclipboard()
            return img.tobytes() if hasattr(img, "tobytes") else None

        before = _clip_bytes()
        os.startfile("ms-screenclip:")  # type: ignore[attr-defined]
        while time.monotonic() - started < limit:
            time.sleep(PORTAL_POLL_S)
            img = ImageGrab.grabclipboard()
            if hasattr(img, "save") and img.tobytes() != before:
                img.save(path)
                return _outcome(way, "ok", time.monotonic() - started)
    except Exception as exc:
        log.debug("Windows area capture failed", exc_info=True)
        return _outcome(way, "failed", time.monotonic() - started, f"{type(exc).__name__}: {exc}")
    return _outcome(way, "timeout", time.monotonic() - started, f"no area was snipped within {limit:.0f} s", limit=limit)


# ----------------------------------------------------------------------
# Screenshot — every OS, every Linux desktop (X11 and Wayland)
# ----------------------------------------------------------------------
_PORTAL_BACKENDS = (("gnome", "xdg-desktop-portal-gnome"), ("kde", "xdg-desktop-portal-kde"),
                    ("hyprland", "xdg-desktop-portal-hyprland"), ("sway", "xdg-desktop-portal-wlr"),
                    ("river", "xdg-desktop-portal-wlr"), ("wayfire", "xdg-desktop-portal-wlr"),
                    ("labwc", "xdg-desktop-portal-wlr"), ("xfce", "xdg-desktop-portal-gtk"),
                    ("cinnamon", "xdg-desktop-portal-xapp"), ("mate", "xdg-desktop-portal-xapp"))


def _capture_ways(path: str, area: bool) -> list:
    """(name, the program it needs or None, run(limit) -> outcome) for this OS and desktop."""
    def tool(name: str, cmd: list):
        return (name, cmd[0], lambda limit: _capture_with_tool(name, cmd, path, limit))

    if WINDOWS:
        if area:
            return [("Windows' snip overlay", None, lambda limit: _capture_windows_area(path, limit))]
        return [("Pillow", None, lambda limit: _capture_with_imagegrab(path))]
    if MACOS:
        ways = [tool("screencapture", ["screencapture", "-x"] + (["-i"] if area else []) + [path])]
        if not area:
            ways.append(("Pillow", None, lambda limit: _capture_with_imagegrab(path)))
        return ways

    portal = ("the desktop's screenshot portal", None,
              lambda limit: _capture_with_portal(path, interactive=area, limit=limit))

    def grim(limit: float) -> Dict[str, Any]:
        if not area:
            return _capture_with_tool("grim", ["grim", path], path, limit)
        if not _check_tool("slurp"):
            return _outcome("grim", "unavailable", 0.0, "an area needs slurp, which is not installed")
        started = time.monotonic()
        try:
            pick = subprocess.run(["slurp"], capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired:
            return _outcome("grim", "timeout", time.monotonic() - started, f"no area was picked within {limit:.0f} s", limit=limit)
        except OSError as exc:
            return _outcome("grim", "failed", time.monotonic() - started, str(exc))
        geometry = (pick.stdout or "").strip()
        if pick.returncode != 0 or not geometry:
            return _outcome("grim", "cancelled", time.monotonic() - started, "no area was picked")
        left = max(0.0, limit - (time.monotonic() - started))
        return _capture_with_tool("grim", ["grim", "-g", geometry, path], path, left)

    spectacle = tool("spectacle", ["spectacle", "-b", "-n", "-r" if area else "-f", "-o", path])
    gnome = tool("gnome-screenshot", ["gnome-screenshot"] + (["-a"] if area else []) + ["-f", path])
    if IS_WAYLAND:
        return [portal, ("grim", "grim", grim), spectacle, gnome]
    ways = []
    if not area:
        ways.append(("Pillow", None, lambda limit: _capture_with_imagegrab(path)))
    ways += [tool("scrot", ["scrot"] + (["-s"] if area else []) + [path]),
             tool("maim", ["maim"] + (["-s"] if area else []) + [path]),
             tool("import", ["import"] + ([] if area else ["-window", "root"]) + [path]),
             spectacle, gnome, portal]
    return ways


def _attempt_words(outcome: Dict[str, Any]) -> str:
    detail = str(outcome.get("detail") or "")
    status = outcome.get("status")
    if status == "timeout":
        return f"{outcome['way']} timed out ({detail or 'no picture in time'})"
    if status == "cancelled":
        return f"{outcome['way']}: {detail or 'cancelled'}"
    if status == "unavailable":
        return f"{outcome['way']} is not available ({detail})" if detail else f"{outcome['way']} is not available"
    return f"{outcome['way']} failed ({detail})" if detail else f"{outcome['way']} failed"


def _missing_advice(attempts: list, installed: list) -> str:
    """Advice only about what is actually missing here: the portal backend for this desktop when the
    portal has no Screenshot interface, or a capture program when none is installed."""
    portal_missing = any(a["way"] == "the desktop's screenshot portal" and a["status"] == "unavailable"
                         and "interface" in str(a.get("detail") or "") for a in attempts)
    tips = []
    if portal_missing:
        backend = next((name for key, name in _PORTAL_BACKENDS if key in DESKTOP), "")
        tips.append(f"the desktop's portal has no screenshot service; installing {backend} would give it one"
                    if backend else "the desktop's portal has no screenshot service; its backend for this "
                                    "desktop is not installed")
    if LINUX and not IS_WAYLAND and not installed:
        tips.append("no capture program is installed (scrot or maim would do)")
    return "; ".join(tips)


def take_screenshot(region: str = "full", *, keep: bool = True) -> Dict[str, Any]:
    """Take a screenshot: region "full" (every screen) or "area" (the user picks one).
    `keep`: a screenshot the user asked for goes to their Pictures folder; one ELI takes to read
    the screen goes to its own data folder, kept for this run of ELI only. Returns
    {ok, path, error, attempts}: `attempts` is what each way did (status, seconds, exit code, what it
    said), and the error is built from it, so a way that timed out is reported as a timeout.

    The ways for each OS and desktop:
      Windows   Pillow (GDI, all monitors); an area through Windows' own snip overlay.
      macOS     screencapture, then Pillow; says so when screen recording is not allowed.
      Linux     on Wayland (GNOME, KDE, Sway, Hyprland, any compositor): the desktop's screenshot
                portal, then grim (+slurp for an area), spectacle, gnome-screenshot.
                On X11 (any desktop): Pillow (XCB), then scrot, maim, ImageMagick's import,
                spectacle, gnome-screenshot, then the portal.
    A record per desktop keeps which way worked and how long it took: that way is tried first, ways
    that never worked here last, each way's time limit comes from its own measured times, and the
    whole capture is bounded by FIRST_TRY_CEILING_S.
    """
    area = (region or "full").lower() != "full"
    kind = "area" if area else "full"
    path = _screenshot_path("Screenshot_area" if area else "Screenshot", keep=keep)
    if not keep:
        _prune_own_captures(keep_path=path)
    record = _ways_record(kind)
    ways = sorted(_capture_ways(path, area), key=lambda w: _rank(record.get(w[0]) or {}))
    deadline = time.monotonic() + FIRST_TRY_CEILING_S
    attempts: list = []
    installed: list = []
    portal_asked_at = None
    for name, needs, run in ways:
        if needs and not _check_tool(needs):
            attempts.append(_outcome(name, "unavailable", 0.0, f"{needs} is not installed"))
            continue
        if needs:
            installed.append(needs)
        left = deadline - time.monotonic()
        if left <= 0:
            attempts.append(_outcome(name, "skipped", 0.0, "the capture's time was used up"))
            continue
        if name == "the desktop's screenshot portal":
            portal_asked_at = time.time()
        outcome = run(min(_limit_for(record.get(name) or {}), left))
        attempts.append(outcome)
        _note_outcome(kind, outcome)
        if outcome["status"] == "ok":
            _collect_late_portal_picture(portal_asked_at, path, keep_as=None)
            result = _saved(path)
            result["way"] = name
            result["attempts"] = attempts
            if MACOS and _macos_screen_recording_allowed() is False:
                result["note"] = ("macOS has not allowed ELI to record the screen, so the picture shows the desktop "
                                  "without its windows: allow ELI in System Settings › Privacy & Security › Screen "
                                  "Recording.")
            return result

    if _collect_late_portal_picture(portal_asked_at, path, keep_as=path):
        attempts.append(_outcome("the desktop's screenshot portal", "ok", 0.0,
                                 "its picture arrived after the wait and was found in the Pictures folder"))
        result = _saved(path)
        result["way"] = "the desktop's screenshot portal"
        result["attempts"] = attempts
        return result

    tried = [a for a in attempts if a["status"] not in ("unavailable", "skipped")]
    said = "; ".join(_attempt_words(a) for a in (tried or attempts)) or "no way of capturing the screen was found"
    advice = _missing_advice(attempts, installed)
    error = f"No screenshot was taken: {said}" + (f". {advice[0].upper() + advice[1:]}" if advice else "") + "."
    return {"ok": False, "error": error, "attempts": attempts,
            "content": "Screenshot failed", "response": "Screenshot failed"}


def _collect_late_portal_picture(asked_at: Optional[float], path: str, *, keep_as: Optional[str]) -> bool:
    """A portal that did not answer in time may still save its picture in the Pictures folder. That
    full-screen picture of whatever was on screen is not left there unannounced: it becomes the
    capture (`keep_as`), or, when another way already gave one, it goes into ELI's own captures
    folder, which is cleared on the next run. True when it became the capture."""
    if asked_at is None:
        return False
    late = _portal_picture_since(asked_at)
    if not late or os.path.abspath(late) == os.path.abspath(path):
        return False
    if keep_as:
        return _take_portal_file(late, keep_as)
    try:
        _own_captures_dir().mkdir(parents=True, exist_ok=True)
        shutil.move(late, str(_own_captures_dir() / os.path.basename(late)))
    except (OSError, shutil.Error):
        log.debug("could not move a late portal picture out of Pictures", exc_info=True)
    return False


# ----------------------------------------------------------------------
# Volume control – wpctl (Wayland) then pactl (X11)
# ----------------------------------------------------------------------
def get_volume() -> Optional[int]:
    """Get current volume percentage (0-100)."""
    portable = platform.get_volume()
    if portable is not None:
        return portable

    if LINUX and IS_WAYLAND and _check_tool("wpctl"):
        result = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True)
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            for part in parts:
                try:
                    vol_float = float(part)
                    return int(vol_float * 100)
                except:
                    continue
    return None

def set_volume(level: Optional[int] = None, direction: Optional[str] = None, delta: int = 5) -> Dict[str, Any]:
    """
    Set or adjust volume.
    - level: absolute percentage (0-100)
    - direction: "up" or "down"
    - delta: step percentage for up/down

    NOTE: pactl is used as primary on both Wayland and X11.
    wpctl (WirePlumber) normalises streams and can revert volume immediately,
    so we only fall back to it if pactl is unavailable.
    """
    if level is not None:
        lvl = max(0, min(100, int(level)))
        if platform.set_volume(lvl):
            return {"ok": True, "content": f"Volume set to {lvl}%", "response": f"Volume set to {lvl}%"}
    elif direction in ("up", "raise"):
        if platform.adjust_volume(abs(int(delta))):
            return {"ok": True, "content": f"Volume raised by {delta}%", "response": f"Volume raised by {delta}%"}
    elif direction in ("down", "lower"):
        if platform.adjust_volume(-abs(int(delta))):
            return {"ok": True, "content": f"Volume lowered by {delta}%", "response": f"Volume lowered by {delta}%"}
    elif direction == "mute":
        if platform.set_muted(True):
            return {"ok": True, "content": "Muted", "response": "Muted"}
    elif direction == "unmute":
        if platform.set_muted(False):
            return {"ok": True, "content": "Unmuted", "response": "Unmuted"}

    if not LINUX:
        return {
            "ok": False,
            "error": "Volume control backend unavailable on this OS. On Windows install pycaw/comtypes for full control.",
            "content": "Volume control unavailable",
            "response": "Volume control unavailable",
        }

    # ── pactl (PulseAudio / PipeWire-pulse) ── primary on all sessions ──
    if _check_tool("pactl"):
        try:
            if level is not None:
                lvl = max(0, min(100, level))
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{lvl}%"], check=True, capture_output=True)
                return {"ok": True, "content": f"Volume set to {lvl}%", "response": f"Volume set to {lvl}%"}
            elif direction == "up":
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{delta}%"], check=True, capture_output=True)
                return {"ok": True, "content": f"Volume raised by {delta}%", "response": f"Volume raised by {delta}%"}
            elif direction == "down":
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{delta}%"], check=True, capture_output=True)
                return {"ok": True, "content": f"Volume lowered by {delta}%", "response": f"Volume lowered by {delta}%"}
            elif direction == "mute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], check=True, capture_output=True)
                return {"ok": True, "content": "Muted", "response": "Muted"}
            elif direction == "unmute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=True, capture_output=True)
                return {"ok": True, "content": "Unmuted", "response": "Unmuted"}
        except Exception as e:
            pass  # fall through to wpctl

    # ── wpctl fallback (only if pactl unavailable) ──
    if IS_WAYLAND and _check_tool("wpctl"):
        try:
            if level is not None:
                float_level = max(0, min(100, level)) / 100.0
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{float_level}"], check=True, capture_output=True)
                return {"ok": True, "content": f"Volume set to {level}%", "response": f"Volume set to {level}%"}
            elif direction == "up":
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{delta}%+"], check=True, capture_output=True)
                return {"ok": True, "content": f"Volume raised by {delta}%", "response": f"Volume raised by {delta}%"}
            elif direction == "down":
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{delta}%-"], check=True, capture_output=True)
                return {"ok": True, "content": f"Volume lowered by {delta}%", "response": f"Volume lowered by {delta}%"}
            elif direction == "mute":
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"], check=True, capture_output=True)
                return {"ok": True, "content": "Muted", "response": "Muted"}
            elif direction == "unmute":
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], check=True, capture_output=True)
                return {"ok": True, "content": "Unmuted", "response": "Unmuted"}
        except Exception as e:
            return {"ok": False, "error": str(e), "content": "Volume control failed", "response": "Volume control failed"}

    return {"ok": False, "error": "No volume control tool found (install pulseaudio-utils: sudo apt install pulseaudio-utils)", "content": "Volume control failed", "response": "Volume control failed"}

# ----------------------------------------------------------------------
# Keyboard simulation – ydotool (Wayland) then xdotool (X11)
# ----------------------------------------------------------------------
def press_key(key: str) -> Dict[str, Any]:
    """Simulate a key press."""
    key_raw = (key or "").strip()
    key_norm = key_raw.lower().replace("control", "ctrl").replace("cmd", "command")
    if "+" in key_norm:
        parts = [p.strip() for p in key_norm.split("+") if p.strip()]
        pyautogui_parts = [
            {"ctrl": "ctrl", "control": "ctrl", "cmd": "command", "super": "win"}.get(p, p)
            for p in parts
        ]
        try:
            import pyautogui  # type: ignore
            pyautogui.hotkey(*pyautogui_parts)
            return {"ok": True, "content": f"Pressed {key_raw}", "response": f"Pressed {key_raw}"}
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        xdotool_map = {
            "ctrl": "ctrl",
            "control": "ctrl",
            "shift": "shift",
            "alt": "alt",
            "super": "super",
            "command": "super",
            "cmd": "super",
            "tab": "Tab",
            "w": "w",
            "t": "t",
            "l": "l",
            "r": "r",
            "enter": "Return",
            "escape": "Escape",
            "esc": "Escape",
        }
        keyname = "+".join(xdotool_map.get(p, p) for p in parts)
        if _check_tool("xdotool"):
            try:
                subprocess.run(["xdotool", "key", "--clearmodifiers", keyname], check=True, capture_output=True)
                return {"ok": True, "content": f"Pressed {key_raw}", "response": f"Pressed {key_raw}"}
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        return {
            "ok": False,
            "error": "Keyboard shortcut simulation requires pyautogui or xdotool.",
            "content": f"Failed to press {key_raw}",
            "response": f"Failed to press {key_raw}",
        }

    if platform.key_press(key_norm):
        return {"ok": True, "content": f"Pressed {key}", "response": f"Pressed {key}"}
    if not LINUX:
        return {
            "ok": False,
            "error": "Keyboard simulation requires pyautogui and OS accessibility permissions on this platform.",
            "content": f"Failed to press {key}",
            "response": f"Failed to press {key}",
        }

    key_map = {
        "enter": "Return", "return": "Return", "tab": "Tab", "space": "space",
        "backspace": "BackSpace", "delete": "Delete", "escape": "Escape",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
        "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super",
    }
    keyname = key_map.get(key.lower(), key)

    if IS_WAYLAND and _check_tool("ydotool"):
        try:
            subprocess.run(["ydotool", "key", f"{keyname}:1", f"{keyname}:0"], check=True, capture_output=True)
            return {"ok": True, "content": f"Pressed {key}", "response": f"Pressed {key}"}
        except Exception as e:
            log.debug("suppressed exception", exc_info=True)
    if _check_tool("xdotool"):
        try:
            subprocess.run(["xdotool", "key", keyname], check=True, capture_output=True)
            return {"ok": True, "content": f"Pressed {key}", "response": f"Pressed {key}"}
        except Exception as e:
            log.debug("suppressed exception", exc_info=True)
    return {"ok": False, "error": "No keyboard simulation tool found (install ydotool or xdotool)", "content": f"Failed to press {key}", "response": f"Failed to press {key}"}

def move_mouse(x: int, y: int) -> Dict[str, Any]:
    """Move the cursor to absolute screen coordinates (pyautogui → xdotool/ydotool)."""
    x, y = int(x), int(y)
    try:
        import pyautogui  # type: ignore
        pyautogui.moveTo(x, y)
        return {"ok": True, "content": f"Moved cursor to ({x},{y})", "response": f"Moved cursor to ({x},{y})"}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if IS_WAYLAND and _check_tool("ydotool"):
        try:
            subprocess.run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)],
                           check=True, capture_output=True)
            return {"ok": True, "content": f"Moved cursor to ({x},{y})", "response": f"Moved cursor to ({x},{y})"}
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    if LINUX and _check_tool("xdotool"):
        try:
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True, capture_output=True)
            return {"ok": True, "content": f"Moved cursor to ({x},{y})", "response": f"Moved cursor to ({x},{y})"}
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return {"ok": False, "error": "No mouse tool (install xdotool or ydotool, or pyautogui).",
            "content": "Couldn't move the cursor.", "response": "Couldn't move the cursor."}


def mouse_click(button: str = "left", x: Optional[int] = None, y: Optional[int] = None,
                double: bool = False) -> Dict[str, Any]:
    """Click (optionally at x,y first). button: left|right|middle."""
    btn = str(button or "left").lower().strip()
    if x is not None and y is not None:
        move_mouse(x, y)
    _label = f"{'double-' if double else ''}{btn} click"
    try:
        import pyautogui  # type: ignore
        pyautogui.click(button=("right" if btn == "right" else "middle" if btn == "middle" else "left"),
                        clicks=2 if double else 1)
        return {"ok": True, "content": f"Performed {_label}", "response": f"Performed {_label}"}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    _xbtn = {"left": "1", "middle": "2", "right": "3"}.get(btn, "1")
    if LINUX and _check_tool("xdotool"):
        try:
            subprocess.run(["xdotool", "click", "--repeat", "2" if double else "1", _xbtn],
                           check=True, capture_output=True)
            return {"ok": True, "content": f"Performed {_label}", "response": f"Performed {_label}"}
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    if IS_WAYLAND and _check_tool("ydotool"):
        _ybtn = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(btn, "0xC0")
        try:
            args = ["ydotool", "click", _ybtn] + (["0xC0"] if double else [])
            subprocess.run(args, check=True, capture_output=True)
            return {"ok": True, "content": f"Performed {_label}", "response": f"Performed {_label}"}
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return {"ok": False, "error": "No mouse tool (install xdotool or ydotool, or pyautogui).",
            "content": f"Couldn't {_label}.", "response": f"Couldn't {_label}."}


def gaze_click(button: str = "left", double: bool = False) -> Dict[str, Any]:
    """Click where the user is LOOKING — move the cursor to the live gaze point
    (from the gaze engine) and click. Requires gaze tracking to be running."""
    try:
        from eli.perception.gaze_engine import get_last_gaze, is_gaze_running
    except Exception as e:
        return {"ok": False, "error": f"gaze engine unavailable: {e}",
                "content": "Gaze tracking isn't available.", "response": "Gaze tracking isn't available."}
    try:
        if not is_gaze_running():
            return {"ok": False, "error": "gaze engine not running",
                    "content": "Gaze tracking isn't on — turn it on first.",
                    "response": "Gaze tracking isn't on — turn it on first."}
        g = get_last_gaze() or {}
        if "screen_x" not in g or "screen_y" not in g:
            return {"ok": False, "error": "no gaze fix",
                    "content": "I can't tell where you're looking right now.",
                    "response": "I can't tell where you're looking right now."}
        x, y = int(float(g["screen_x"])), int(float(g["screen_y"]))
        res = mouse_click(button=button, x=x, y=y, double=double)
        if res.get("ok"):
            _what = "opened" if double else f"{button}-clicked"
            res["content"] = res["response"] = f"{_what} where you're looking ({x},{y})"
        return res
    except Exception as e:
        return {"ok": False, "error": str(e),
                "content": "Gaze click failed.", "response": "Gaze click failed."}


def type_text(text: str) -> Dict[str, Any]:
    """Type a string."""
    if platform.type_text(text):
        return {"ok": True, "content": f"Typed: {text}", "response": f"Typed: {text}"}
    if not LINUX:
        return {
            "ok": False,
            "error": "Text entry requires pyautogui and OS accessibility permissions on this platform.",
            "content": "Failed to type",
            "response": "Failed to type",
        }

    if IS_WAYLAND and _check_tool("ydotool"):
        try:
            subprocess.run(["ydotool", "type", text], check=True, capture_output=True)
            return {"ok": True, "content": f"Typed: {text}", "response": f"Typed: {text}"}
        except Exception as e:
            log.debug("suppressed exception", exc_info=True)
    if _check_tool("xdotool"):
        try:
            subprocess.run(["xdotool", "type", text], check=True, capture_output=True)
            return {"ok": True, "content": f"Typed: {text}", "response": f"Typed: {text}"}
        except Exception as e:
            log.debug("suppressed exception", exc_info=True)
    return {"ok": False, "error": "No keyboard simulation tool found", "content": f"Failed to type", "response": f"Failed to type"}

# ----------------------------------------------------------------------
# CLIPBOARD – finally fixed: no timeouts, foreground mode for wl-copy
# ----------------------------------------------------------------------
def set_clipboard(text: str) -> Dict[str, Any]:
    """Set clipboard content with platform-native fallbacks."""
    if platform.copy_to_clipboard(text):
        return {"ok": True, "content": "Clipboard set", "response": "Clipboard set"}
    if not LINUX:
        return {
            "ok": False,
            "error": "No clipboard backend works on this platform.",
            "content": "Failed to set clipboard",
            "response": "Failed to set clipboard",
        }

    # Wayland – use -f to keep in foreground, no timeout
    if _check_tool("wl-copy"):
        try:
            # No timeout – let it finish naturally
            proc = subprocess.run(
                ["wl-copy", "-f"],  # -f = foreground, prevents forking
                input=text,
                text=True,
                capture_output=True
            )
            if proc.returncode == 0:
                return {"ok": True, "content": "Clipboard set", "response": "Clipboard set"}
            else:
                log.debug(f"[CLIPBOARD] wl-copy failed: {proc.stderr}")
        except Exception as e:
            log.debug(f"[CLIPBOARD] wl-copy exception: {e}")

    # X11 fallback
    if _check_tool("xclip"):
        try:
            # No timeout
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                capture_output=True
            )
            if proc.returncode == 0:
                return {"ok": True, "content": "Clipboard set", "response": "Clipboard set"}
            else:
                log.debug(f"[CLIPBOARD] xclip failed: {proc.stderr}")
        except Exception as e:
            log.debug(f"[CLIPBOARD] xclip exception: {e}")

    return {"ok": False, "error": "No clipboard tool works",
            "content": "Failed to set clipboard", "response": "Failed to set clipboard"}

def get_clipboard() -> Optional[str]:
    """Get clipboard content. Returns None if unavailable."""
    portable = platform.get_clipboard()
    if portable:
        return portable
    if not LINUX:
        return None

    if _check_tool("wl-paste"):
        try:
            proc = subprocess.run(
                ["wl-paste"],  # no timeout
                capture_output=True,
                text=True
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
            else:
                log.debug(f"[CLIPBOARD] wl-paste failed: {proc.stderr}")
        except Exception as e:
            log.debug(f"[CLIPBOARD] wl-paste exception: {e}")

    if _check_tool("xclip"):
        try:
            proc = subprocess.run(
                ["xclip", "-o", "-selection", "clipboard"],
                capture_output=True,
                text=True
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return None
