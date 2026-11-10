"""Bundles: separate ELI applications that run on ELI v3's core in their own window.

A bundle is a specialised product (ELI Legal and ELI VR now; Healthcare, Finance and others to
come) that has its own window and its own rules, and uses parts of ELI v3: its local model runtime
in particular. ELI v3 can start one *instead of* its full window, at start-up or with
``eli --bundle <id>``, or alongside it from the Bundles menu.

Two ways a bundle makes itself known, and nothing else is ever loaded:

``in-process``  a Python package installed in ELI v3's environment that advertises itself under
                the ``eli.bundles`` entry-point group. It runs inside ELI v3's process, so it can
                use ELI v3's loaded model. Its entry point returns a description (see
                ``Bundle.from_description``) whose ``launch`` names the function to call.
``external``    a separately installed application (an AppImage, an installed program) that
                registered itself by writing ``<id>.json`` into a ``bundles`` folder of ELI v3's
                data. It runs as its own program with its own settings.

``ELI_EXTRA_BUNDLES`` (``module:attr`` entries, separated by commas) adds in-process bundles from
a source checkout that is on the path but not installed, for development.

A bundle that is not a Python program reaches the core over ELI's local server. It asks for it
with ``"needs": ["server"]`` and ELI has the server answering before the bundle starts (see
``eli.bundles.core_server``).

Licensing: a bundle names the edition it belongs to. An official bundle (ELI Legal, ELI VR, ...) is
a separate ELI Product with a licence key of its own, and ``entitled`` will not start one this
machine holds no key for (see ``eli.licensing.products``). A bundle from another publisher is
licensed by that publisher, so ELI starts it without a key of ours. This decides only what ELI v3
will start: an official bundle that also runs on its own has to check its own key there.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

log = logging.getLogger("eli.bundles")

GROUP = "eli.bundles"
CONTRACT = 1


@dataclass(frozen=True)
class Bundle:
    id: str
    title: str
    summary: str = ""
    version: str = ""
    edition: str = ""
    icon: str = ""
    kind: str = "in-process"  # in-process | external
    launch: str = ""  # in-process: "module:function"
    command: tuple[str, ...] = ()  # external: the program and its arguments
    origin: str = ""  # where it was found
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_description(
        cls, raw: dict[str, Any], *, origin: str, kind: str = "in-process"
    ) -> Bundle:
        if int(raw.get("contract", CONTRACT)) > CONTRACT:
            raise ValueError(
                f"bundle contract {raw.get('contract')} is newer than this ELI ({CONTRACT})"
            )
        bundle_id = str(raw["id"]).strip().lower()
        if not bundle_id or not bundle_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"bad bundle id {raw.get('id')!r}")
        command = tuple(str(c) for c in raw.get("command") or ())
        if kind == "external" and not command:
            raise ValueError("an external bundle must give the command that starts it")
        if kind == "in-process" and ":" not in str(raw.get("launch", "")):
            raise ValueError(
                "an in-process bundle must name its launch function as module:function"
            )
        return cls(
            id=bundle_id,
            title=str(raw.get("title") or bundle_id),
            summary=str(raw.get("summary") or ""),
            version=str(raw.get("version") or ""),
            edition=str(raw.get("edition") or bundle_id),
            icon=str(raw.get("icon") or ""),
            kind=kind,
            launch=str(raw.get("launch") or ""),
            command=command,
            origin=origin,
            extra={
                k: v
                for k, v in raw.items()
                if k
                not in {
                    "id",
                    "title",
                    "summary",
                    "version",
                    "edition",
                    "icon",
                    "launch",
                    "command",
                    "contract",
                }
            },
        )


def _describe(target: Any) -> dict[str, Any]:
    value = target() if callable(target) else target
    if not isinstance(value, dict):
        raise TypeError(
            "a bundle entry point must give a dict, or a function returning one"
        )
    return value


def registry_dirs() -> list[Path]:
    """Folders where separately installed bundles register themselves."""
    out: list[Path] = []
    try:
        from eli.core.paths import data_dir

        out.append(data_dir() / "bundles")
    except Exception:
        log.debug("data_dir unavailable", exc_info=True)
    from eli.core.paths import user_root

    out.append(user_root() / "bundles")
    unique: list[Path] = []
    for p in out:
        if p not in unique:
            unique.append(p)
    return unique


def bundle_apps() -> dict[str, dict[str, Any]]:
    """Official bundles that ship as applications of their own, by id: their label, the files they
    ship as, and where they are released, from their edition's manifest ("app")."""
    from eli.editions import discover_editions
    from eli.licensing.products import OFFICIAL_BUNDLES

    out: dict[str, dict[str, Any]] = {}
    for name, edition in discover_editions().items():
        app = edition.manifest.get("app")
        if name in OFFICIAL_BUNDLES and isinstance(app, dict) and app.get("files"):
            out[name] = {"label": edition.label, "files": [str(f) for f in app["files"]],
                         "releases": str(app.get("releases") or "")}
    return out


def _search_folders() -> list[Path]:
    """Where a downloaded application is usually put: the desktop, downloads, an applications folder."""
    home = Path.home()
    out = [home / "Desktop", home / "Downloads", home / "Applications"]
    if sys.platform == "darwin":
        out.append(Path("/Applications"))
    if sys.platform == "win32":
        out.append(Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Programs")
    return [p for p in out if p.is_dir()]


_INSTALLER = re.compile(r"setup|install", re.I)


def version_in(text: str) -> tuple[int, ...]:
    """The version a file or record names: 'ELI_Legal-0.7.0-linux-x86_64.AppImage' -> (0, 7, 0)."""
    m = re.search(r"(\d+(?:\.\d+)+)", str(text or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _program(path: Path) -> Path:
    """What to run for a copy found: a macOS application is a folder, and its program is the
    one its Info.plist names."""
    if path.suffix == ".app" and path.is_dir():
        try:
            with (path / "Contents" / "Info.plist").open("rb") as fh:
                name = plistlib.load(fh).get("CFBundleExecutable")
            if name and (path / "Contents" / "MacOS" / name).exists():
                return path / "Contents" / "MacOS" / name
        except (OSError, plistlib.InvalidFileException):
            pass
    return path


def find_installed(bundle_id: str) -> Path | None:
    """A copy of an official bundle's application where one is usually put, in the folder itself or
    one unpacked into it; the highest version when there are several (the newest file when they
    name none), never a setup program. ELI VR registered itself only when told by hand, so ELI v3
    had no way in to a copy sitting on the desktop."""
    app = bundle_apps().get(bundle_id)
    if not app:
        return None
    found: list[Path] = []
    for folder in _search_folders():
        for pattern in app["files"]:
            found += [p for p in (*folder.glob(pattern), *folder.glob(f"*/{pattern}"))
                      if p.exists() and not _INSTALLER.search(p.name)]
    if not found:
        return None
    best = max(found, key=lambda p: (version_in(f"{p.parent.name} {p.name}"), p.stat().st_mtime))
    return _program(best)


def register_program(bundle_id: str, program: Path) -> Path:
    """Put a separately installed bundle on the list as the program given, as the bundle itself
    would ("eli-vr bundle register"): for a copy found or picked by hand."""
    app = bundle_apps().get(bundle_id, {})
    folder = registry_dirs()[-1]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{bundle_id}.json"
    path.write_text(json.dumps({"contract": CONTRACT, "id": bundle_id, "title": app.get("label") or bundle_id,
                                "edition": bundle_id, "command": [str(program)], "needs": ["server"]},
                               indent=2) + "\n", encoding="utf-8")
    return path


def discover() -> dict[str, Bundle]:
    """Every bundle that can be started here, by id. One that cannot be read is skipped and
    logged; it never stops ELI starting."""
    found: dict[str, Bundle] = {}

    def add(bundle: Bundle, *, fallback: bool = False) -> None:
        held = found.get(bundle.id)
        newer = held is not None and version_in(bundle.version) > version_in(held.version) > ()
        if held is not None and (held.kind == "in-process" or fallback) and not newer:
            # An installed package is preferred, since it can use ELI's own model, and a
            # registration to a copy found by looking; either gives way to a newer version,
            # so an old copy never hides a new release.
            return
        found[bundle.id] = bundle

    try:
        eps = entry_points(group=GROUP)
    except TypeError:  # Python 3.9 style
        eps = entry_points().get(GROUP, [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            add(
                Bundle.from_description(
                    _describe(ep.load()), origin=f"package {ep.value}"
                )
            )
        except Exception:
            log.warning(
                "bundle entry point %s could not be loaded", ep.name, exc_info=True
            )
    for spec in [
        s.strip()
        for s in os.environ.get("ELI_EXTRA_BUNDLES", "").split(",")
        if s.strip()
    ]:
        try:
            module, _, attr = spec.partition(":")
            add(
                Bundle.from_description(
                    _describe(
                        getattr(importlib.import_module(module), attr or "describe")
                    ),
                    origin=f"ELI_EXTRA_BUNDLES {spec}",
                )
            )
        except Exception:
            log.warning("extra bundle %s could not be loaded", spec, exc_info=True)
    for folder in registry_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                bundle = Bundle.from_description(raw, origin=str(path), kind="external")
                if not Path(bundle.command[0]).exists():
                    continue  # the program was removed since it registered
                add(bundle)
            except Exception:
                log.warning(
                    "bundle registration %s could not be read", path, exc_info=True
                )
    for bundle_id, app in bundle_apps().items():
        program = find_installed(bundle_id)
        if program is not None:
            version = ".".join(map(str, version_in(f"{program.parent.name} {program.name}")))
            add(Bundle(id=bundle_id, title=app["label"], edition=bundle_id, kind="external",
                       version=version, command=(str(program),), origin=f"found at {program}",
                       extra={"needs": ["server"]}), fallback=True)
    return dict(sorted(found.items()))


@dataclass(frozen=True)
class Offer:
    """One bundle as a person starting their own ELI v3 is told about it."""

    id: str
    label: str
    state: str  # "ready" | "needs_key" | "not_installed" | "coming"
    detail: str = ""
    bundle: Bundle | None = None
    releases: str = ""


def offers() -> list[Offer]:
    """Every official bundle and what it takes to use it here: open it, enter its key, install it,
    or wait for it. ELI VR, ELI Legal and the bundles to come are upgrades to ELI v3, and a person
    is told which they have rather than finding a menu with nothing in it. A third party's bundle
    found here is listed as it is."""
    from eli.licensing.products import OFFICIAL_BUNDLES

    found, apps = discover(), bundle_apps()
    out: list[Offer] = []
    for product, label in OFFICIAL_BUNDLES.items():
        bundle, app = found.get(product), apps.get(product) or {}
        if bundle is not None:
            ok, why = entitled(bundle)
            out.append(Offer(product, bundle.title or label, "ready" if ok else "needs_key",
                             "" if ok else why, bundle, app.get("releases", "")))
        elif app:
            out.append(Offer(product, app["label"], "not_installed",
                             f"{app['label']} is not installed on this machine.", None, app.get("releases", "")))
        else:
            out.append(Offer(product, label, "coming", f"{label} is not released yet."))
    out += [Offer(bundle.id, bundle.title, "ready", "", bundle) for bundle in found.values()
            if bundle.id not in OFFICIAL_BUNDLES]
    return out


def entitled(bundle: Bundle) -> tuple[bool, str]:
    """Whether this machine is licensed to start the bundle, and why or why not."""
    from eli.licensing.activation import product_status
    from eli.licensing.products import bundle_product

    product = bundle_product(bundle.id, bundle.edition)
    if product is None:
        return True, "not an ELI product; its publisher licenses it"
    status = product_status(product, version=bundle.version)
    return status.active, status.message


def _host() -> dict[str, Any]:
    try:
        from importlib.metadata import version as _dist_version

        version = _dist_version("eli-v3")
    except Exception:  # noqa: BLE001 - the version is informative only
        version = ""
    return {
        "name": "ELI v3",
        "version": str(version),
        "root": os.environ.get("ELI_PROJECT_ROOT", ""),
    }


def _needs_server(bundle: Bundle) -> bool:
    return "server" in (bundle.extra.get("needs") or ())


# A bundle that gives ELI a body (ELI VR) draws on the same GPU ELI loads its model onto, and
# takes more as it goes. While one is on its way, a model load waits for it to be in the room
# (api/server.py's preload) and the plan keeps the card room it was measured needing
# (eli/core/load_plan.py). The expectation lasts as long as the bundle runs.
_bodies_lock = threading.Lock()
_bodies_expected: set[str] = set()


def brings_body(bundle: Bundle) -> bool:
    """Whether this bundle gives ELI a body: it says so ("brings": ["body"]), or it is ELI VR."""
    return "body" in (bundle.extra.get("brings") or ()) or bundle.edition == "vr" or bundle.id == "vr"


@contextlib.contextmanager
def _expecting(bundle: Bundle) -> Iterator[None]:
    if not brings_body(bundle):
        yield
        return
    with _bodies_lock:
        _bodies_expected.add(bundle.id)
    try:
        yield
    finally:
        with _bodies_lock:
            _bodies_expected.discard(bundle.id)


def body_expected() -> bool:
    """Whether a bundle that brings a body is running or starting."""
    with _bodies_lock:
        return bool(_bodies_expected)


def wait_for_body(poll_seconds: float = 0.25) -> bool:
    """Block while a body is on its way and not yet in ELI's rooms. True when one is here now,
    False when none is expected any more."""
    from eli.world import embodiment

    while True:
        if embodiment.present():
            return True
        if not body_expected():
            return False
        time.sleep(poll_seconds)


@contextlib.contextmanager
def _core(bundle: Bundle) -> Iterator[int | None]:
    """What the bundle asked of ELI's core, for as long as it runs: the port of ELI's local
    server, or None when it asked for nothing or the server could not be had."""
    if not _needs_server(bundle):
        yield None
        return
    from eli.bundles import core_server

    with core_server.serving() as port:
        yield port


def launch(bundle_id: str, argv: list[str] | None = None) -> int:
    """Start a bundle in this process, in place of ELI v3's own window. An external bundle is
    started as its own program and this process waits for it."""
    bundles = discover()
    bundle = bundles.get(bundle_id.lower())
    if bundle is None:
        known = ", ".join(bundles) or "none are installed"
        print(f"No bundle {bundle_id!r} is installed here ({known}).", file=sys.stderr)
        return 2
    ok, why = entitled(bundle)
    if not ok:
        print(f"{bundle.title} cannot start: {why}", file=sys.stderr)
        return 3
    log.info("starting bundle %s (%s) from %s", bundle.id, bundle.kind, bundle.origin)
    # expected before the server starts: its model preload looks for the body
    with _expecting(bundle), _core(bundle) as port:
        if bundle.kind == "external":
            command = [*bundle.command, *(argv or [])]
            if port is None:
                return subprocess.call(command)
            return subprocess.call(
                command, env={**os.environ, "ELI_API_PORT": str(port)}
            )
        module, _, attr = bundle.launch.partition(":")
        run: Callable[..., Any] = getattr(importlib.import_module(module), attr)
        host = _host()
        if port is not None:
            host["api_port"] = port
        return int(run(list(argv or []), host=host) or 0)


def spawn(bundle_id: str) -> subprocess.Popen:
    """Start a bundle in a window of its own, beside a running ELI v3."""
    bundles = discover()
    bundle = bundles[bundle_id.lower()]
    ok, why = entitled(bundle)
    if not ok:
        raise PermissionError(why)
    env = dict(os.environ)
    server = None
    if _needs_server(bundle):
        # Beside a running ELI the bundle is served by this ELI, in this process: the model
        # already loaded in ELI's window is the one that answers, and nothing is loaded twice.
        from eli.bundles import core_server

        port, server = core_server.start()
        if port is not None:
            env["ELI_API_PORT"] = str(port)
    if bundle.kind == "external":
        command = list(bundle.command)
    elif getattr(sys, "frozen", False):
        command = [sys.executable, "--bundle", bundle.id]
    else:
        command = [sys.executable, "-m", "eli", "--bundle", bundle.id]
    if brings_body(bundle):
        with _bodies_lock:
            _bodies_expected.add(bundle.id)
    process = subprocess.Popen(command, env=env, start_new_session=True)
    if server is not None or brings_body(bundle):
        # a server started for this bundle goes when the bundle does, and so does the body it brought
        def _stop_when_closed() -> None:
            process.wait()
            if server is not None:
                server.should_exit = True
            with _bodies_lock:
                _bodies_expected.discard(bundle.id)

        threading.Thread(target=_stop_when_closed, name="eli-bundle-watch", daemon=True).start()
    return process


__all__ = [
    "CONTRACT",
    "GROUP",
    "Bundle",
    "body_expected",
    "brings_body",
    "discover",
    "entitled",
    "launch",
    "registry_dirs",
    "spawn",
    "wait_for_body",
]
