"""What a release must not carry under the GPL or a non-commercial licence, how every build leaves out
what a package ELI uses declares or holds under one, and where the source of every LGPL package a
release carries is.

ELI v3 is sold under its own licence (LICENSE) and passes on no package under the GPL, which lets a
program be passed on as a binary only with its Corresponding Source and under the GPL's own terms.
PyAutoGUI (BSD-3-Clause), which drives the keyboard and mouse where no native tool does, declares three
that are: MouseInfo, PyMsgBox and, on Linux, python3-xlib. ELI runs the same without them (LEFT_OUT says
why), but pip installs whatever a package declares, so each build that bundles packages takes them out.
Two packages ELI keeps hold files it never uses under such a licence (LEFT_OUT_FILES): openWakeWord's
pre-trained wake-word models (CC BY-NC-SA 4.0) and SpeechRecognition's FLAC programs (GPL-2.0); those
files are taken out the same way:

    python packaging/gpl_sources.py --leave-out           # uninstall them from this environment,
                                                          # reinstall what shared files with them, and
                                                          # delete the files left out of the others
    python packaging/gpl_sources.py --leave-out DIR       # rewrite the wheels in DIR without those files
    python packaging/gpl_sources.py --check-wheels DIR    # fail when a wheel or sdist in DIR is under the
                                                          # GPL or holds a file left out
    python packaging/gpl_sources.py --check-bundle DIR    # fail when a built bundle holds a library or
                                                          # program under a strong copyleft (NATIVE_LIBRARIES)
                                                          # or a file left out
    python packaging/gpl_sources.py --kept-dependencies WHEEL PLATFORM PYTHON...
                                                          # what WHEEL needs on that platform, less them
    python packaging/gpl_sources.py --list                # the GPL and LGPL packages a release built
                                                          # here would carry (no network)

The frozen builds run --leave-out after their last install, and ELI.spec stops a build whose environment
still holds a package under the GPL (gpl_distributions) or a file left out (left_out_files), whose
stand-in for a left-out package is not intact, or that holds a package in a class a closed, paid product
may not pass on (breach_distributions: a strong copyleft, a non-commercial licence, a native library
under the GPL inside a package whose own metadata says otherwise, or no licence anyone could read; see
the licence classes below). Each build checks what it built with --check-bundle. The build's own tools are not counted: what requirements-build.txt brings and ELI does not
(PyInstaller, whose bootloader every frozen app starts through, is under the GPL with an exception that
lets the bootloader go with any program; pyinstaller-hooks-contrib only runs during the build). The
wheelhouses pass --leave-out DIR, then --check-wheels. A user's own `pip install` from PyPI still fetches PyAutoGUI's
dependencies onto that user's machine; ELI passes none of them on.

The release workflow runs this in the Linux build's environment, which holds every package the Windows
and macOS builds carry plus a few for Linux alone, and attaches what it wrote to the release:

    python packaging/gpl_sources.py OUT_DIR [--bundle DIR]
                                               # SOURCES.md (the GPL sources, or that there are none)
                                               # and LGPL_SOURCES.md beside it; with --bundle, the native
                                               # LGPL libraries the built bundle carries too

GPL packages: every installed distribution a release would carry whose licence metadata names the GPL
or AGPL and offers no LGPL choice. SOURCES.md says plainly when there is none. For each there is, the
source is that version's sdist from PyPI, checked against PyPI's digest; without one, the project's
repository at the version's tag. A package that carries compiled code whose build files the sdist
leaves out also gets the repository at the tag. When a package's build fetches more source (CMake
ExternalProject or FetchContent), that is fetched too, at the commit the build names.

LGPL packages (Qt for Python and the Qt libraries inside its wheels, zeroconf, chardet, python-xlib and
whatever else the environment holds): every installed distribution whose licence metadata names the
LGPL. Their source is not attached; GPL-3.0 section 6(d), which LGPL-3.0 section 4 applies, lets it be
offered from another server when clear directions next to the object code say where. LGPL_SOURCES.md
is those directions: each package, its version and the address of that version's source: the sdist
PyPI lists for it, with its digest; for a project with none, the archive it publishes (SOURCE_ARCHIVES)
or its GitHub repository at the version's tag; for Qt, its source at the version the wheels carry
(QT_SOURCE). An address not read from PyPI is used only once it answers.

Anything that cannot be fetched or found stops the run: a release must not go out without it.

The standard library, and the `packaging` library that pip carries wherever it runs: this runs before
anything else is known to be installed.
"""
from __future__ import annotations

import base64
import email.parser
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
LGPL_LISTING = "LGPL_SOURCES.md"

# Projects that put no sdist on PyPI, by the repository their metadata names, with the archive of each
# version's source the project publishes, tried in order (the first that answers is listed). Qt for
# Python's wheels (PySide6, PySide6_Essentials, PySide6_Addons, shiboken6) come from one tree.
SOURCE_ARCHIVES: Dict[str, Tuple[str, ...]] = {
    "code.qt.io/cgit/pyside/pyside-setup.git": (
        "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{version}-src/"
        "pyside-setup-everywhere-src-{version}.tar.xz",
        "https://code.qt.io/cgit/pyside/pyside-setup.git/snapshot/pyside-setup-v{version}.tar.gz",
    ),
}
# Qt itself, the C++ libraries inside those wheels, at the version they carry: the archive a release
# stays in first, then where a current release is published before it moves there.
QT_SOURCE: Tuple[str, ...] = (
    "https://download.qt.io/archive/qt/{minor}/{version}/single/qt-everywhere-src-{version}.tar.xz",
    "https://download.qt.io/official_releases/qt/{minor}/{version}/single/qt-everywhere-src-{version}.tar.xz",
)
# A distribution file that is a Qt library (libQt6Core.so.6, Qt6Core.dll, QtCore.framework/...), and
# the one that is Qt's core, whose package is asked which Qt it is.
_QT_LIBRARY = re.compile(r"(?:^|/)(?:lib)?Qt\d+\w*\.(?:so[.\d]*|dll|dylib)$|(?:^|/)Qt\w*\.framework/")
_QT_CORE = re.compile(r"(?:^|/)(?:lib)?Qt\d*Core(?:\.so[.\d]*|\.dll|\.dylib)?$|(?:^|/)QtCore\.framework/")

_GPL = re.compile(r"(?<![A-Za-z])A?GPL|GNU (?:AFFERO )?GENERAL PUBLIC LICEN[CS]E", re.I)
_LGPL = re.compile(r"(?<![A-Za-z])LGPL|GNU (?:LESSER|LIBRARY) (?:GENERAL )?PUBLIC LICEN[CS]E", re.I)


class LeftOut(NamedTuple):
    """A distribution under the GPL, or carrying a library that is, that a package ELI uses declares and
    no release carries."""
    of: str                       # the package that declares it
    modules: Tuple[str, ...]      # its import packages, which ELI.spec leaves out of the frozen apps
    needs: Tuple[str, ...]        # what of that package needs it; ELI calls none of these
    stand_in: str                 # what provides the same import package instead, or ""
    held_by: str = ""             # where the package imports it unguarded: ELI's function that imports
                                  # the package with a placeholder for it ("module:function")


# PyAutoGUI 0.9.54 imports mouseinfo and pymsgbox inside try/except ImportError and in their place puts
# a function that raises only when it is called: pyautogui.mouseInfo() and its alert, confirm, prompt
# and password boxes are all that need them, and ELI calls none of those. Its X11 backend drives the
# Xlib package, which python-xlib (LGPL, in pyproject.toml's dependencies on Linux) provides;
# python3-xlib is an old copy of the same package under the GPL. PyAV (BSD-3-Clause) carries an FFmpeg
# with x264 and x265 (GPL-2.0) linked in, on every platform: faster-whisper imports it unguarded at the
# top of its audio module, only for decode_audio(), which opens a file; ELI hands faster-whisper samples
# it decoded itself and imports it through local_whisper_stt.faster_whisper(), which puts a placeholder
# where PyAV is missing. tests/claims/test_license_reachable.py holds each of these to the code.
LEFT_OUT: Dict[str, LeftOut] = {
    "mouseinfo": LeftOut("pyautogui", ("mouseinfo",), ("mouseInfo",), ""),
    "pymsgbox": LeftOut("pyautogui", ("pymsgbox",), ("alert", "confirm", "prompt", "password"), ""),
    "python3-xlib": LeftOut("pyautogui", (), (), "python-xlib"),
    "av": LeftOut("faster-whisper", ("av",), ("decode_audio",), "",
                  "eli.perception.local_whisper_stt:faster_whisper"),
}


class LeftOutFiles(NamedTuple):
    """Files inside a distribution ELI keeps that no release carries and no install keeps."""
    pattern: str                  # what is left out: a regular expression over its RECORD paths
    keep: Tuple[str, ...]         # the file stems matching it that stay (what ELI loads)
    licence: str                  # what they are under
    why: str                      # why ELI runs the same without them

    def matches(self, rel: str) -> bool:
        rel = str(rel).replace("\\", "/")
        return bool(re.search(self.pattern, rel)) and Path(rel).stem not in self.keep


# openWakeWord's wheel carries its pre-trained models, which its licence note puts under CC BY-NC-SA 4.0
# (non-commercial), its two feature models among them, and a copy of Silero's VAD; ELI runs its own two
# feature models (eli/perception/wake_features/, built by packaging/wake_features.py) and trains its own
# head, so none of the package's model files is kept. SpeechRecognition's carries FLAC encoder programs
# (GPL-2.0), which only AudioData.get_flac_data() runs, for the online recognisers ELI never calls.
# tests/claims/test_license_reachable.py holds both to the code.
LEFT_OUT_FILES: Dict[str, LeftOutFiles] = {
    "openwakeword": LeftOutFiles(r"^openwakeword/resources/models/[^/]+$", (),
                                 "CC-BY-NC-SA-4.0 (its pre-trained models)",
                                 "ELI runs its own feature models (eli/perception/wake_features/)"),
    "speechrecognition": LeftOutFiles(r"^speech_recognition/flac-[^/]+$", (), "GPL-2.0-or-later",
                                      "only get_flac_data() runs them, for online recognisers ELI never calls"),
}


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _requirement(text: str):
    try:
        from packaging.requirements import Requirement
    except ImportError:   # pip carries its own copy wherever it runs
        from pip._vendor.packaging.requirements import Requirement
    return Requirement(text)


def installed_distributions(path: Optional[List[str]] = None) -> Dict[str, metadata.Distribution]:
    """{normalised name: distribution} for what is installed (on `path`, else sys.path); the first of
    a name wins, as an import would."""
    found: Dict[str, metadata.Distribution] = {}
    for dist in (metadata.distributions(path=path) if path is not None else metadata.distributions()):
        name = str(dist.metadata.get("Name") or "").strip()
        if name:
            found.setdefault(_key(name), dist)
    return found


def requirement_lines(path: Path) -> List[str]:
    """The requirements in a requirements file, comments and options left out."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        if line and not line.startswith("-"):
            out.append(line)
    return out


def _project(root: Path) -> dict:
    try:
        import tomllib
    except ImportError:   # Python 3.10
        import tomli as tomllib
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def release_requirements(extras: Optional[Iterable[str]] = None, root: Path = ROOT) -> List[str]:
    """pyproject.toml's dependencies and those of `extras` (every extra when None), an extra that names
    the project itself (full = eli-v3[llm,image]) read as the extras it names."""
    project = _project(root)
    optional = project.get("optional-dependencies") or {}
    own = _key(project["name"])
    out = list(project.get("dependencies") or [])
    todo, seen = list(optional if extras is None else extras), set()
    while todo:
        extra = todo.pop()
        if extra in seen:
            continue
        seen.add(extra)
        for text in optional.get(extra, []):
            req = _requirement(text)
            if _key(req.name) == own:
                todo += sorted(req.extras)
            else:
                out.append(text)
    return out


def brought_by(requirements: Iterable[str], installed: Dict[str, metadata.Distribution],
               skip: Iterable[str] = ()) -> Dict[str, metadata.Distribution]:
    """The installed distributions `requirements` bring on this system, through each one's own
    dependencies (and the extras asked of it), less `skip` and what it alone brings. A requirement
    that is not installed is not followed: its licence cannot be read."""
    skip = {_key(s) for s in skip}
    found: Dict[str, metadata.Distribution] = {}
    done = set()
    todo = [(text, "") for text in requirements]
    while todo:
        text, extra = todo.pop()
        try:
            req = _requirement(text)
            if req.marker is not None and not req.marker.evaluate({"extra": extra}):
                continue
        except Exception as exc:   # a malformed line in some package's metadata: named, not followed
            print(f"[gpl] requirement not read: {text!r}: {exc}", file=sys.stderr)
            continue
        key = _key(req.name)
        if key in skip or key not in installed:
            continue
        dist = found[key] = installed[key]
        for wanted in {""} | set(req.extras):
            if (key, wanted) not in done:
                done.add((key, wanted))
                todo += [(line, wanted) for line in dist.requires or []]
    return found


def build_only(installed: Optional[Dict[str, metadata.Distribution]] = None, root: Path = ROOT) -> set:
    """What only the build itself runs: what requirements-build.txt brings and ELI's own requirements
    (every extra) do not."""
    installed = installed_distributions() if installed is None else installed
    tools = brought_by(requirement_lines(root / "requirements-build.txt"), installed)
    return set(tools) - set(brought_by(release_requirements(None, root), installed))


def bundled_distributions(extras: Optional[Iterable[str]] = None, root: Path = ROOT,
                          installed: Optional[Dict[str, metadata.Distribution]] = None) -> Dict[str, metadata.Distribution]:
    """What a release built from this environment carries: what ELI's requirements (and `extras`)
    bring, less LEFT_OUT, which every build takes out."""
    installed = installed_distributions() if installed is None else installed
    return brought_by(release_requirements(extras, root), installed, skip=LEFT_OUT)


def left_out_modules() -> List[str]:
    return sorted({m for entry in LEFT_OUT.values() for m in entry.modules})


def _path_of(dist: metadata.Distribution, file) -> str:
    return os.path.normcase(os.path.abspath(str(dist.locate_file(file))))


def _record(dist: metadata.Distribution) -> List[Tuple[str, str]]:
    """(path, sha256) of each file the distribution's RECORD lists, missing ones too: from Python 3.12,
    Distribution.files leaves out what is not on disk, which is just what has to be found here."""
    import csv
    rows = []
    for row in csv.reader((dist.read_text("RECORD") or "").splitlines()):
        if row and row[0]:
            rows.append((row[0], row[1].split("=", 1)[1] if len(row) > 1 and row[1].startswith("sha256=") else ""))
    return rows


def intact(dist: metadata.Distribution) -> bool:
    """Whether every file the distribution's RECORD lists with a digest is on disk as recorded."""
    for file, recorded in _record(dist):
        if not recorded:
            continue
        path = Path(_path_of(dist, file))
        if not path.is_file():
            return False
        digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).rstrip(b"=").decode()
        if digest != recorded:
            return False
    return True


def stand_in_problems(path: Optional[List[str]] = None) -> List[str]:
    """What is wrong with the stand-ins of what LEFT_OUT leaves out: one that is not installed where the
    package that needs it is installed and declares the left-out one on this system, or one whose files
    are not its own (a left-out copy of the same package overwrote them)."""
    installed = installed_distributions(path)
    problems = []
    for name, entry in sorted(LEFT_OUT.items()):
        if not entry.stand_in or _key(entry.of) not in installed:
            continue
        declared = [line for line in installed[_key(entry.of)].requires or []
                    if _key(_requirement(line).name) == name]
        wanted = any(_requirement(line).marker is None or _requirement(line).marker.evaluate({"extra": ""})
                     for line in declared)
        stand_in = installed.get(_key(entry.stand_in))
        if stand_in is None:
            if wanted:
                problems.append(f"{entry.stand_in} is not installed: {entry.of} needs it in place of {name}")
        elif not intact(stand_in):
            problems.append(f"{entry.stand_in} {stand_in.version}'s files are not its own "
                            f"(run `python packaging/gpl_sources.py --leave-out`)")
    return problems


def leave_out(path: Optional[List[str]] = None, run: Callable = subprocess.run) -> int:
    """Leave out of this environment what no release carries: the distributions LEFT_OUT names and the
    files LEFT_OUT_FILES names in those ELI keeps."""
    status = _leave_out_distributions(path, run)
    return leave_out_files(path) or status


def left_out_files(path: Optional[List[str]] = None) -> List[Tuple[metadata.Distribution, List[str]]]:
    """Each installed distribution LEFT_OUT_FILES names, with the files it leaves out that are on disk."""
    installed = installed_distributions(path)
    found = []
    for key, entry in sorted(LEFT_OUT_FILES.items()):
        dist = installed.get(key)
        if dist is None:
            continue
        files = [f for f, _ in _record(dist) if entry.matches(f) and Path(_path_of(dist, f)).is_file()]
        if files:
            found.append((dist, files))
    return found


def _drop_record_rows(text: str, gone: Iterable[str]) -> str:
    import csv
    gone = {str(g) for g in gone}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in csv.reader(text.splitlines()):
        if row and row[0] not in gone:
            writer.writerow(row)
    return out.getvalue()


def leave_out_files(path: Optional[List[str]] = None) -> int:
    """Delete the files LEFT_OUT_FILES names from the distributions installed here, and their rows from
    each one's RECORD, so the distribution stays whole as installed (and uninstalls cleanly)."""
    for dist, files in left_out_files(path):
        name, entry = str(dist.metadata["Name"]), LEFT_OUT_FILES[_key(str(dist.metadata["Name"]))]
        print(f"[gpl] leaving out {len(files)} file(s) of {name} {dist.version} ({entry.licence}; {entry.why}): "
              + ", ".join(Path(f).name for f in files), flush=True)
        for f in files:
            Path(_path_of(dist, f)).unlink()
        record = next(f for f, _ in _record(dist) if f.replace("\\", "/").endswith(".dist-info/RECORD"))
        record_path = Path(_path_of(dist, record))
        record_path.write_text(_drop_record_rows(record_path.read_text(encoding="utf-8"), files), encoding="utf-8")
    still = left_out_files(path)
    for dist, files in still:
        print(f"::error::{dist.metadata['Name']} still holds {', '.join(files)}", file=sys.stderr)
    return 1 if still else 0


def _leave_out_distributions(path: Optional[List[str]] = None, run: Callable = subprocess.run) -> int:
    """Uninstall what LEFT_OUT names from this environment, then reinstall, without its dependencies, each
    distribution that shared a file with them (python-xlib and python3-xlib are the same Xlib package),
    and check that each of those is intact again."""
    installed = installed_distributions(path)
    present = {key: dist for key, dist in installed.items() if key in LEFT_OUT}
    if not present:
        print(f"[gpl] none of {', '.join(sorted(LEFT_OUT))} is installed: nothing to leave out")
        return 0
    gone = {_path_of(dist, f) for dist in present.values() for f, _ in _record(dist)}
    names = sorted(str(dist.metadata["Name"]) for dist in present.values())
    print(f"[gpl] leaving out {', '.join(names)} (GPL; ELI runs without them, see LEFT_OUT)", flush=True)
    pip = [sys.executable, "-m", "pip"]
    try:
        run(pip + ["uninstall", "-y", *names], check=True)
        after = installed_distributions(path)
        still = sorted(key for key in after if key in LEFT_OUT)
        if still:
            print(f"::error::still installed after pip uninstall: {', '.join(still)}", file=sys.stderr)
            return 1
        shared = {key: (str(dist.metadata["Name"]), str(dist.version)) for key, dist in after.items()
                  if any(_path_of(dist, f) in gone for f, _ in _record(dist))}
        for _, (name, version) in sorted(shared.items()):
            print(f"[gpl] {name} {version} shared files with them: reinstalling it", flush=True)
            run(pip + ["install", "--no-deps", "--force-reinstall", f"{name}=={version}"], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"::error::{' '.join(map(str, exc.cmd))} failed ({exc.returncode})", file=sys.stderr)
        return 1
    final = installed_distributions(path)
    broken = sorted(name for key, (name, _) in shared.items() if key not in final or not intact(final[key]))
    if broken:
        print(f"::error::not intact after reinstalling: {', '.join(broken)}", file=sys.stderr)
        return 1
    return 0


def archive_metadata(path: Path):
    """The metadata inside a wheel (its .dist-info/METADATA) or an sdist (its PKG-INFO), or None."""
    name = path.name.lower()
    text = None
    if name.endswith(".whl") or name.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            wanted = r"^[^/]+\.dist-info/METADATA$" if name.endswith(".whl") else r"^[^/]+/PKG-INFO$"
            member = next((n for n in z.namelist() if re.match(wanted, n)), None)
            text = z.read(member).decode("utf-8", "replace") if member else None
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path) as t:
            member = next((m for m in t.getmembers() if m.isfile() and re.match(r"^[^/]+/PKG-INFO$", m.name)), None)
            text = t.extractfile(member).read().decode("utf-8", "replace") if member else None
    return email.parser.Parser().parsestr(text) if text else None


def _archive_names(path: Path) -> List[str]:
    """The members of a wheel, or of an sdist less its top folder (name-version/)."""
    name = path.name.lower()
    if name.endswith((".whl", ".zip")):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        return names if name.endswith(".whl") else [n.split("/", 1)[-1] for n in names]
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path) as t:
            return [m.name.split("/", 1)[-1] for m in t.getmembers() if m.isfile()]
    return []


def archive_left_out(path: Path, meta=None) -> List[str]:
    """The members of a wheel or sdist that LEFT_OUT_FILES leaves out of its distribution."""
    meta = archive_metadata(path) if meta is None else meta
    entry = LEFT_OUT_FILES.get(_key(str(meta.get("Name") or ""))) if meta is not None else None
    return [n for n in _archive_names(path) if entry.matches(n)] if entry else []


def leave_out_of_wheels(directory: Path) -> int:
    """Take out of `directory` the wheels and sdists of what LEFT_OUT names, and rewrite each wheel without
    the files LEFT_OUT_FILES names and their rows in its RECORD, as every install leaves them out of the
    environment. An sdist holding such files is not rewritten: --check-wheels refuses it."""
    for path in sorted(p for p in Path(directory).iterdir() if p.is_file()):
        meta = archive_metadata(path)
        if meta is not None and _key(str(meta.get("Name") or "")) in LEFT_OUT:
            print(f"[gpl] {path.name}: left out (packaging/gpl_sources.py, LEFT_OUT)", flush=True)
            path.unlink()
    for path in sorted(Path(directory).glob("*.whl")):
        gone = set(archive_left_out(path))
        if not gone:
            continue
        name = str(archive_metadata(path).get("Name"))
        print(f"[gpl] {path.name}: leaving out {len(gone)} file(s) ({LEFT_OUT_FILES[_key(name)].licence}): "
              + ", ".join(sorted(Path(g).name for g in gone)), flush=True)
        part = path.with_name(path.name + ".part")
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename in gone:
                    continue
                body = src.read(info)
                if re.match(r"^[^/]+\.dist-info/RECORD$", info.filename):
                    body = _drop_record_rows(body.decode("utf-8"), gone).encode("utf-8")
                dst.writestr(info, body)
        part.replace(path)
    return 0


def gpl_archives(directory: Path) -> List[str]:
    """Each wheel or sdist in `directory` whose metadata puts it under the GPL, that LEFT_OUT names, that
    carries a library NATIVE_LIBRARIES puts under a strong copyleft, or that holds files no release
    carries (LEFT_OUT_FILES), with what it says."""
    found = []
    for path in sorted(p for p in Path(directory).iterdir() if p.is_file()):
        meta = archive_metadata(path)
        carried = native_breaches(_archive_names(path)) if meta is not None else []
        if meta is not None and is_gpl(licence_facts(meta)):
            found.append(f"{path.name} ({'; '.join(licence_facts(meta))})")
        elif meta is not None and _key(str(meta.get("Name") or "")) in LEFT_OUT:
            found.append(f"{path.name} (left out of every release: packaging/gpl_sources.py, LEFT_OUT)")
        elif carried:
            found.append(f"{path.name} (carries {', '.join(sorted({Path(f).name for f, _ in carried}))}: "
                         f"{'; '.join(sorted({lic for _, lic in carried}))})")
        elif meta is not None and archive_left_out(path, meta):
            entry = LEFT_OUT_FILES[_key(str(meta.get("Name")))]
            found.append(f"{path.name} (holds {len(archive_left_out(path, meta))} file(s) under {entry.licence}, "
                         f"which no release carries: run `python packaging/gpl_sources.py --leave-out {directory}`)")
    return found


# The environment markers of a pip platform tag, by its start.
_PLATFORMS = (("win", "win32", "Windows", "nt"), ("macosx", "darwin", "Darwin", "posix"),
              ("manylinux", "linux", "Linux", "posix"), ("musllinux", "linux", "Linux", "posix"),
              ("linux", "linux", "Linux", "posix"))


def kept_dependencies(wheel: Path, platform: str, pythons: Iterable[str]) -> List[str]:
    """What `wheel` declares it needs on `platform` (a pip platform tag) under any of `pythons`, less
    LEFT_OUT: what a wheelhouse carries beside a package installed without its declared dependencies."""
    meta = archive_metadata(Path(wheel))
    if meta is None:
        raise ValueError(f"{wheel}: no metadata inside")
    system = next((s for s in _PLATFORMS if platform.startswith(s[0])), None)
    if system is None:
        raise ValueError(f"unknown platform tag {platform}")
    machine = platform.rsplit("_", 1)[-1]
    machine = machine.upper() if system[2] == "Windows" else machine     # what Python reports there
    out: List[str] = []
    for line in meta.get_all("Requires-Dist") or []:
        req = _requirement(line)
        if _key(req.name) in LEFT_OUT:
            continue
        for python in pythons:
            env = {"sys_platform": system[1], "platform_system": system[2], "os_name": system[3],
                   "python_version": python, "python_full_version": f"{python}.0", "extra": "",
                   "platform_machine": machine, "implementation_name": "cpython",
                   "platform_python_implementation": "CPython"}
            if req.marker is None or req.marker.evaluate(env):
                req.marker = None
                if str(req) not in out:
                    out.append(str(req))
                break
    return out


def licence_facts(dist) -> List[str]:
    """What a distribution says about its licence: the SPDX expression, the first line of its
    License field (some carry the whole text there) and its licence classifiers. `dist` is an installed
    distribution or the metadata read out of a wheel or sdist (archive_metadata)."""
    meta = getattr(dist, "metadata", dist)
    facts = [str(meta.get("License-Expression") or "")]
    facts += [line for line in str(meta.get("License") or "").splitlines() if line.strip()][:1]
    facts += [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return [f.strip() for f in facts if f and f.strip()]


def is_gpl(facts: List[str]) -> bool:
    """Under the GPL or AGPL with no LGPL choice. Mixed metadata (a BSD text, a GPL classifier) counts
    as GPL: shipping a source that was not owed costs nothing."""
    text = "\n".join(facts)
    return bool(_GPL.search(text)) and not _LGPL.search(text)


def gpl_distributions() -> List[Dict[str, str]]:
    """Every installed distribution under the GPL that a release built here would carry: all of them
    but the build's own tools (build_only)."""
    installed = installed_distributions()
    tools = build_only(installed)
    found: Dict[str, Dict[str, str]] = {}
    for key, dist in installed.items():
        name = str(dist.metadata.get("Name") or "").strip()
        facts = licence_facts(dist)
        if key not in tools and is_gpl(facts):
            found[name.lower()] = {"name": name, "version": str(dist.version),
                                   "licence": "; ".join(facts), "home": _home_page(dist),
                                   "compiled": "yes" if _compiled(dist) else ""}
    return [found[k] for k in sorted(found)]


def _compiled(dist: metadata.Distribution) -> bool:
    """Whether the installed distribution carries compiled code: an extension module or a library."""
    return any(re.search(r"\.(?:so|pyd|dylib)(?:\.\d+)*$", str(f)) for f in (dist.files or []))


def is_lgpl(facts: List[str]) -> bool:
    """Under the LGPL, or with the LGPL among the licences offered (Qt for Python's wheels)."""
    return bool(_LGPL.search("\n".join(facts)))


# ── what a closed, paid product may pass on ─────────────────────────────────────────────────────────
# Every package a release carries, and every library or model file inside one, falls in one of these
# classes, freest first. ELI v3 is sold under its own licence: what is in BREACH is not shipped, not
# installed by ELI's installers and not loaded into ELI's process, and what is UNKNOWN is found by hand
# (KNOWN_LICENCES) before a release carries it.
PERMISSIVE = "permissive"          # MIT, BSD, Apache, ISC, PSF, zlib, CC0, CC-BY...: keep the notice
VENDOR = "vendor terms"            # a vendor's runtime whose licence lets an application carry it (CUDA)
WEAK = "weak copyleft"             # LGPL, MPL, EPL, a GPL with a linking exception: fine as whole files,
                                   # linked dynamically and replaceable, with directions to the source
UNKNOWN = "unknown"
STRONG = "strong copyleft"         # GPL, AGPL, Sleepycat, SSPL, EUPL, OSL
NONCOMMERCIAL = "non-commercial"   # CC BY-NC, research-only and other non-commercial licences
CLASSES = (PERMISSIVE, VENDOR, WEAK, UNKNOWN, STRONG, NONCOMMERCIAL)
BREACH = (STRONG, NONCOMMERCIAL)

# A licence's name or text, by the first class whose pattern it matches.
_CLASS_PATTERNS = (
    (NONCOMMERCIAL, re.compile(r"non[- ]?commercial|(?:^|[-\s(])(?-i:NC)(?:[-\s)]|$)|research[- ](?:only|licen[cs]e)"
                               r"|qwen[- ]research|\bCPML\b|coqui public model", re.I)),
    (WEAK, re.compile(r"(?:GPL|General Public Licen[cs]e).{0,80}exception|LGPL|(?:Lesser|Library) General Public"
                      r"|\bMPL\b|Mozilla Public|\bEPL\b|Eclipse Public|\bCDDL\b|Common Development and Distribution"
                      r"|\bCPL\b|Common Public Licen|MS-RL|CC[- ]BY[- ]SA|ShareAlike", re.I | re.S)),
    (STRONG, re.compile(r"(?<![A-Za-z])A?GPL|GNU (?:AFFERO )?GENERAL PUBLIC LICEN[CS]E|Sleepycat|\bSSPL\b"
                        r"|Server Side Public|\bEUPL\b|European Union Public|\bOSL\b|Open Software Licen[cs]e"
                        r"|\bRPL\b|Reciprocal Public", re.I)),
    (VENDOR, re.compile(r"NVIDIA", re.I)),
    (PERMISSIVE, re.compile(r"\bMIT\b|\bBSD|Apache|\bISC\b|\bPSF|Python Software Foundation|Python License"
                            r"|\bzlib\b|\bCC0|Unlicense|Public Domain|\bHPND|Historical Permission|\bBSL-1\.0"
                            r"|Boost Software|\bZPL|Zope Public|WTFPL|\bX11\b|\bNCSA\b|Unicode|\bEDL\b"
                            r"|Eclipse Distribution|\bOFL\b|Open Font|\bCC[- ]BY(?![- ](?:NC|SA|ND))"
                            r"|Creative Commons Attribution(?![- ](?:Non|Share|No))|libpng|\bFTL\b|FreeType"
                            r"|Expat|\bUPL\b|Artistic|\bCNRI|OpenSSL|\bAFL\b|Academic Free|BlueOak"
                            r"|Permission is hereby granted, free of charge|Redistribution and use in source and binary"
                            r"|Permission to use, copy, modify", re.I)),
)
_RANK = {c: i for i, c in enumerate(CLASSES)}

# What the metadata leaves unsaid, found by hand: the licence, and where it was read.
KNOWN_LICENCES: Dict[str, Tuple[str, str]] = {
    "pypiwin32": ("PSF-2.0", "a stub that installs pywin32 (PSF-2.0), published by pywin32's maintainers: "
                             "https://github.com/mhammond/pywin32"),
    "cuda-toolkit": ("LicenseRef-NVIDIA-CUDA-EULA", "NVIDIA's metapackage for the CUDA wheels PyPI's Linux torch "
                                                    "pulls in, under the CUDA Toolkit EULA; the release builds "
                                                    "take the CPU torch and carry none (release.yml)"),
}

# Native libraries and programs that wheels and build machines bring in, by file name, whatever the
# package's own metadata says, with their class: PyAV's wheels (BSD-3-Clause) link x264 and x265 (GPL-2.0)
# into their FFmpeg on every platform, Python's readline module links GNU Readline (GPL-3.0) and PortAudio
# built with JACK links Berkeley DB (Sleepycat) on a Linux build machine: strong copyleft, a breach. The
# LGPL ones (FFmpeg built without its GPL parts, Qt, libsndfile) may go with a release when their source is
# offered: LGPL_SOURCES.md says where, at the version read from the library itself (`probe`, a C function
# returning its version string; else the version in its file name), from `sources`. The libraries a Linux
# build takes from its build machine are found by the system's package manager instead (host_libraries).
class NativeFamily(NamedTuple):
    pattern: str                      # the file name
    licence: str
    klass: str                        # STRONG (no release carries it) or WEAK (its source is listed)
    project: str = ""
    probe: str = ""                   # e.g. av_version_info
    sources: Tuple[str, ...] = ()     # templates: {version}, {minor}
    note: str = ""


NATIVE_LIBRARIES: Tuple[NativeFamily, ...] = (
    NativeFamily(r"^(?:lib)?x264(?:[-.]|$)", "GPL-2.0-or-later (x264)", STRONG),
    NativeFamily(r"^(?:lib)?x265(?:[-.]|$)", "GPL-2.0-or-later (x265)", STRONG),
    NativeFamily(r"^(?:lib)?postproc(?:[-.]|$)", "GPL-2.0-or-later (FFmpeg's libpostproc)", STRONG),
    NativeFamily(r"^(?:lib)?(?:xvidcore|rubberband|vidstab|frei0r)", "GPL-2.0-or-later", STRONG),
    NativeFamily(r"^libreadline\.|^libhistory\.", "GPL-3.0-or-later (GNU Readline)", STRONG),
    NativeFamily(r"^libdb(?:-[\d.]+)?\.(?:so|dylib)|^libdb[\d_]*\.dll$", "Sleepycat (Berkeley DB)", STRONG),
    NativeFamily(r"^libgdbm", "GPL-3.0-or-later (GNU dbm)", STRONG),
    NativeFamily(r"^libfftw3", "GPL-2.0-or-later (FFTW)", STRONG),
    NativeFamily(r"^libgsl\.", "GPL-3.0-or-later (GNU Scientific Library)", STRONG),
    NativeFamily(r"^(?:lib)?espeak(?:-ng)?(?:[-.]|$)|^espeakbridge\.|^(?:lib)?piper_phonemize",
                 "GPL-3.0-or-later (eSpeak NG)", STRONG),
    NativeFamily(r"^flac(?:-[\w.-]+)?(?:\.exe)?$", "GPL-2.0-or-later (the FLAC command-line programs)", STRONG),
    NativeFamily(r"^(?:lib)?(?:avutil|avcodec|avformat|avfilter|avdevice|swresample|swscale)[-.\d]",
                 "LGPL-2.1-or-later", WEAK, "FFmpeg", "av_version_info",
                 ("https://ffmpeg.org/releases/ffmpeg-{version}.tar.xz",)),
    NativeFamily(r"^(?:lib)?Qt[56]Core(?:[-.]|$)", "LGPL-3.0-only", WEAK, "Qt", "",
                 ("https://download.qt.io/archive/qt/{minor}/{version}/single/qt-everywhere-opensource-src-{version}.tar.xz",
                  "https://download.qt.io/archive/qt/{minor}/{version}/single/qt-everywhere-src-{version}.tar.xz")),
    NativeFamily(r"^libsndfile", "LGPL-2.1-or-later", WEAK, "libsndfile", "sf_version_string",
                 ("https://github.com/libsndfile/libsndfile/releases/download/{version}/libsndfile-{version}.tar.xz",),
                 "soundfile's build compiles libogg, libvorbis, libFLAC, libopus (BSD), mpg123 and LAME (LGPL) into it: "
                 "https://github.com/bastibe/libsndfile-binaries"),
)
# Source, stubs and text that name such a library are not the library (pyttsx3's espeak.py driver).
_NOT_NATIVE = (".py", ".pyc", ".pyi", ".pyx", ".pxd", ".h", ".txt", ".md", ".rst", ".json", ".html")


def _class_of_name(text: str) -> str:
    for klass, pattern in _CLASS_PATTERNS:
        if pattern.search(text):
            return klass
    return UNKNOWN


def _expression_class(text: str) -> Optional[str]:
    """The class of an SPDX licence expression (A OR B takes the freer, A AND B the stricter; a GPL WITH
    an exception is weak), or None when the text is not one."""
    tokens = re.findall(r"\(|\)|[^\s()]+", text)
    if not tokens or not any(t.upper() in ("OR", "AND", "WITH") for t in tokens) and len(tokens) != 1:
        return None
    pos = 0

    def peek() -> str:
        return tokens[pos].upper() if pos < len(tokens) else ""

    def atom() -> str:
        nonlocal pos
        if peek() == "(":
            pos += 1
            klass = either()
            if peek() != ")":
                raise ValueError("unbalanced")
            pos += 1
            return klass
        if pos >= len(tokens) or peek() in ("OR", "AND", "WITH", ")"):
            raise ValueError("missing licence")
        name = tokens[pos]
        pos += 1
        if peek() == "WITH":
            pos += 2
            return WEAK if _class_of_name(name) == STRONG else _class_of_name(name)
        return _class_of_name(name)

    def both() -> str:
        nonlocal pos
        klass = atom()
        while peek() == "AND":
            pos += 1
            klass = max(klass, atom(), key=_RANK.get)
        return klass

    def either() -> str:
        nonlocal pos
        klass = both()
        while peek() == "OR":
            pos += 1
            klass = min(klass, both(), key=_RANK.get)
        return klass

    try:
        klass = either()
    except (ValueError, IndexError):
        return None
    return klass if pos == len(tokens) else None


def licence_text_class(text: str) -> str:
    """The class of a licence named in words or as an SPDX expression: a model card's, a catalog
    entry's, KNOWN_LICENCES'."""
    text = str(text or "").strip()
    if not text:
        return UNKNOWN
    klass = _expression_class(text) if "\n" not in text else None
    return klass if klass not in (None, UNKNOWN) else _class_of_name(text)


def licence_class(dist) -> str:
    """The class of what a distribution's metadata says (or the metadata read out of a wheel or sdist):
    its SPDX expression when it has one; else its License field and licence classifiers, where a
    non-commercial term wins, a GPL counts unless an LGPL is offered beside it (as is_gpl reads it), and
    of the rest the freest is taken (they are the licences offered); else the licence files it carries."""
    meta = getattr(dist, "metadata", dist)
    expression = str(meta.get("License-Expression") or "").strip()
    if expression:
        klass = _expression_class(expression)
        if klass is not None and klass != UNKNOWN:
            return klass
    field = str(meta.get("License") or "").strip()
    if field and "\n" not in field and len(field) < 120:
        klass = _expression_class(field)
        if klass is not None and klass != UNKNOWN:
            return klass
    facts = [expression, field[:4000]] + [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    found = {_class_of_name(f) for f in facts if f} - {UNKNOWN}
    if not found and hasattr(dist, "read_text"):
        for name in meta.get_all("License-File") or []:
            for where in (f"licenses/{name}", name):
                text = dist.read_text(where)
                if text:
                    found |= {_class_of_name(text[:4000])} - {UNKNOWN}
                    break
    if NONCOMMERCIAL in found:
        return NONCOMMERCIAL
    if STRONG in found:
        return WEAK if WEAK in found and is_lgpl(facts) else STRONG
    return min(found, key=_RANK.get) if found else UNKNOWN


def native_breaches(names: Iterable[str]) -> List[Tuple[str, str]]:
    """(path, licence) of each of these paths whose file name is a native library or program NATIVE_LIBRARIES
    puts under a strong copyleft."""
    found = []
    for name in names:
        base = re.split(r"[\\/]", str(name))[-1]
        if base.lower().endswith(_NOT_NATIVE):
            continue
        family = native_family(base)
        if family is not None and family.klass == STRONG:
            found.append((str(name), family.licence))
    return found


def native_family(base: str) -> Optional[NativeFamily]:
    """The NATIVE_LIBRARIES entry a file name belongs to, or None."""
    if base.lower().endswith(_NOT_NATIVE):
        return None
    return next((f for f in NATIVE_LIBRARIES if re.search(f.pattern, base)), None)


def classify(dist: metadata.Distribution) -> Tuple[str, str]:
    """(class, what it is under) of an installed distribution as a release carries it: KNOWN_LICENCES
    first, else its metadata, and strong copyleft whatever that says when it carries a library
    NATIVE_LIBRARIES names (but for the files every build leaves out of it, LEFT_OUT_FILES)."""
    key = _key(str(dist.metadata.get("Name") or ""))
    if key in KNOWN_LICENCES:
        licence, where = KNOWN_LICENCES[key]
        klass, said = _class_of_name(licence), f"{licence} ({where})"
    else:
        klass, said = licence_class(dist), "; ".join(licence_facts(dist)) or "nothing in its metadata"
    left_out = LEFT_OUT_FILES.get(key)
    carried = native_breaches(f for f, _ in _record(dist) if not (left_out and left_out.matches(f)))
    if carried:
        return STRONG, f"{said}, and it carries {', '.join(sorted({Path(f).name for f, _ in carried}))} " \
                       f"({'; '.join(sorted({lic for _, lic in carried}))})"
    return klass, said


def breach_distributions(installed: Optional[Dict[str, metadata.Distribution]] = None,
                         root: Path = ROOT) -> List[Dict[str, str]]:
    """Every installed distribution a release built here would carry (all but the build's own tools and
    ELI itself) that is in BREACH, or whose licence nothing says (UNKNOWN), with why."""
    installed = installed_distributions() if installed is None else installed
    skip = build_only(installed, root) | {_key(_project(root)["name"])}
    found = []
    for key, dist in sorted(installed.items()):
        if key in skip:
            continue
        klass, said = classify(dist)
        if klass in BREACH or klass == UNKNOWN:
            found.append({"name": str(dist.metadata.get("Name")), "version": str(dist.version),
                          "class": klass, "licence": said})
    return found


def bundle_breaches(directory: Path) -> List[str]:
    """What a built bundle (dist/ELI, ELI.app, a portable tree) holds that no release carries: each native
    library or program under a strong copyleft (NATIVE_LIBRARIES) and each file LEFT_OUT_FILES names."""
    root = Path(directory)
    files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    native = native_breaches(files)
    found = [f"{rel}: {licence}" for rel, licence in native]
    for rel in sorted(set(files) - {rel for rel, _ in native}):
        parts = rel.split("/")
        for i in range(len(parts)):
            tail = "/".join(parts[i:])
            entry = next((e for e in LEFT_OUT_FILES.values() if e.matches(tail)), None)
            if entry is not None:
                found.append(f"{rel}: {entry.licence} ({entry.why})")
                break
    return sorted(found)


def lgpl_distributions() -> List[Dict[str, object]]:
    """Each installed LGPL distribution, with the repositories its metadata names and the Qt
    libraries among its files (Qt for Python's wheels carry Qt's)."""
    found: Dict[str, Dict[str, object]] = {}
    for dist in metadata.distributions():
        name = str(dist.metadata.get("Name") or "").strip()
        facts = licence_facts(dist)
        if name and is_lgpl(facts):
            files = [str(f).replace("\\", "/") for f in (dist.files or [])]
            found[name.lower()] = {"name": name, "version": str(dist.version), "licence": "; ".join(facts),
                                   "home": _home_page(dist), "repos": _project_urls(dist),
                                   "qt_libraries": [f for f in files if _QT_LIBRARY.search(f)],
                                   "qt_core": next((f for f in files if _QT_CORE.search(f)), "")}
    return [found[k] for k in sorted(found)]


def _project_urls(dist: metadata.Distribution) -> List[str]:
    urls = [str(dist.metadata.get("Home-page") or "")]
    urls += [u.split(",", 1)[-1].strip() for u in (dist.metadata.get_all("Project-URL") or [])]
    return [u for u in urls if u]


def _home_page(dist: metadata.Distribution) -> str:
    urls = _project_urls(dist)
    for url in urls:
        if "github.com/" in url:
            return url
    return next((u for u in urls if u), "")


def _answers(url: str) -> bool:
    """Whether the server hands out this file: its first byte comes back."""
    request = urllib.request.Request(url, headers={"User-Agent": "eli-release-gpl-sources", "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return bool(response.read(1))
    except Exception as exc:  # an address that does not answer is reported by the caller, with the others tried
        print(f"[lgpl-sources] {url}: {exc}", file=sys.stderr)
        return False


def first_answering(urls: List[str]) -> Optional[str]:
    return next((url for url in urls if _answers(url)), None)


def qt_version(package: str) -> str:
    """The Qt a binding package carries, asked of its QtCore in a process of its own."""
    code = "import importlib, sys; print(importlib.import_module(sys.argv[1] + '.QtCore').qVersion())"
    ran = subprocess.run([sys.executable, "-c", code, package], capture_output=True, text=True, timeout=300)
    found = re.fullmatch(r"\d+\.\d+\.\d+", ran.stdout.strip())
    if ran.returncode != 0 or not found:
        raise ValueError(f"{package}.QtCore did not say which Qt it is: {(ran.stderr or ran.stdout).strip()[-300:]}")
    return found.group(0)


def lgpl_source(pkg: Dict[str, object]) -> str:
    """Where this version's source is: PyPI's sdist with its digest, else the archive its project
    publishes (SOURCE_ARCHIVES, by the repository its metadata names), else its GitHub repository at
    the version's tag; each but PyPI's checked to answer."""
    name, version = str(pkg["name"]), str(pkg["version"])
    try:
        sdist, pypi = pypi_sdist(name, version), "no sdist on PyPI"
    except Exception as exc:  # not on PyPI at all, or PyPI did not answer: the other addresses decide
        sdist, pypi = None, f"PyPI: {exc}"
    if sdist:
        return f"{sdist['url']} (sha256 {sdist['sha256']})"
    repos = [str(u) for u in pkg.get("repos") or []] or [str(pkg.get("home") or "")]
    tried = [t.format(version=version) for repo in repos for key, templates in SOURCE_ARCHIVES.items()
             if key in repo for t in templates]
    tried += [u for repo in repos for tag in (f"v{version}", version) for u in [github_archive(repo, tag)] if u]
    tried = list(dict.fromkeys(tried))
    url = first_answering(tried)
    if not url:
        raise ValueError(f"{pypi}, and none of these answers: {', '.join(tried) or 'no address to try'}")
    return url


_SHARED = re.compile(r"\.(?:so(?:\.\d+)*|dylib|dll)$", re.I)
_VERSION = re.compile(r"\d+(?:\.\d+)+")


def _probe_version(path: Path, function: str) -> str:
    """The version string a library's own C function returns, asked in a process of its own with the
    library's folder on the loader's path (the neighbours it was built with)."""
    code = ("import ctypes, sys; f = getattr(ctypes.CDLL(sys.argv[1]), sys.argv[2]); "
            "f.restype = ctypes.c_char_p; print(f().decode())")
    var = "PATH" if sys.platform == "win32" else ("DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH")
    env = dict(os.environ)
    env[var] = os.pathsep.join(x for x in (str(Path(path).parent), env.get(var, "")) if x)
    try:
        ran = subprocess.run([sys.executable, "-c", code, str(path), function], capture_output=True, text=True,
                             timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[lgpl-sources] {path}: {function}() not asked: {exc}", file=sys.stderr)
        return ""
    found = _VERSION.search(ran.stdout) if ran.returncode == 0 else None
    return found.group(0) if found else ""


def native_lgpl(files: Iterable[Path], root: Path, skip: Iterable[str] = ()) -> List[Dict[str, object]]:
    """Each LGPL library family (NATIVE_LIBRARIES, WEAK) among `files` (paths under `root`), by folder: its
    project, licence, the version read from the library itself, its files and where that version's source
    is to be found. Libraries whose names are in `skip` (a package already listed carries them) are not."""
    skip = set(skip)
    groups: Dict[Tuple[str, str], Dict[str, object]] = {}
    for path in sorted(Path(f) for f in files):
        family = native_family(path.name)
        if family is None or family.klass != WEAK or path.name in skip:
            continue
        where = path.parent.relative_to(root).as_posix() if Path(root) in path.parents else str(path.parent)
        group = groups.setdefault((family.project, where), {"family": family, "where": where, "files": [], "version": ""})
        group["files"].append(path.name)
        if not group["version"]:
            # the library's own answer (FFmpeg's sonames number its libraries, not its release), else the
            # version in its file name (Qt's libQt5Core.so.5.15.18)
            if family.probe:
                group["version"] = _probe_version(path, family.probe)
            else:
                named = _VERSION.search(path.name.split(".so.", 1)[-1])
                group["version"] = named.group(0) if named else ""
    out = []
    for group in groups.values():
        family, version = group["family"], str(group["version"])
        minor = ".".join(version.split(".")[:2])
        out.append({"name": family.project, "version": version, "licence": family.licence, "where": group["where"],
                    "files": group["files"], "note": family.note,
                    "sources": [t.format(version=version, minor=minor) for t in family.sources] if version else []})
    return out


def _os_release() -> Dict[str, str]:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _copyright_text(package: str) -> str:
    """A Debian or Ubuntu package's copyright file ("" when it has none here)."""
    try:
        return Path(f"/usr/share/doc/{package}/copyright").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[lgpl-sources] no copyright file for {package}: {exc}", file=sys.stderr)
        return ""


def host_libraries(files: Iterable[Path], provided: Iterable[str], run: Callable = subprocess.run) -> List[Dict[str, object]]:
    """The shared libraries among `files` that no installed package provides (`provided`: file names
    from their RECORDs) and that the build machine's own packages do (Debian, Ubuntu: dpkg), whose
    package's copyright names the LGPL or the GPL: each source package, its version, its files and
    where the distribution publishes that version's source."""
    provided = set(provided)
    names = sorted({Path(f).name for f in files if _SHARED.search(Path(f).name) and Path(f).name not in provided})
    if not names or not shutil.which("dpkg"):
        return []
    owners = run(["dpkg", "-S", *[f"*/{n}" for n in names]], capture_output=True, text=True)
    by_package: Dict[str, set] = {}
    for line in (owners.stdout or "").splitlines():
        package, _, path = line.partition(": ")
        if path and Path(path.strip()).name in names:
            for pkg in package.split(", "):
                by_package.setdefault(pkg.strip(), set()).add(Path(path.strip()).name)
    distro = _os_release().get("ID", "").strip('"')
    out: Dict[str, Dict[str, object]] = {}
    for package, libs in sorted(by_package.items()):
        query = run(["dpkg-query", "-W", "-f", "${source:Package}\t${source:Version}\n", package],
                    capture_output=True, text=True)
        first = (query.stdout or "").splitlines()[:1]
        if query.returncode != 0 or not first or "\t" not in first[0]:
            continue
        source, version = first[0].split("\t", 1)
        copyright_text = _copyright_text(package.split(":")[0])
        if not (_LGPL.search(copyright_text) or _GPL.search(copyright_text)):
            continue
        licences = sorted({m.strip() for m in re.findall(r"^License:\s*(.+)$", copyright_text, re.M)
                           if _LGPL.search(m) or _GPL.search(m)}) or ["the LGPL or the GPL, per its copyright file"]
        url = (f"https://launchpad.net/ubuntu/+source/{source}/{version}" if distro == "ubuntu"
               else f"https://snapshot.debian.org/package/{source}/{version}/")
        entry = out.setdefault(source, {"name": source, "version": version, "licence": ", ".join(licences),
                                        "where": f"the build machine's {package.split(':')[0]}", "files": [],
                                        "note": "", "sources": [url]})
        entry["files"] = sorted(set(entry["files"]) | libs)
    return [out[k] for k in sorted(out)]


def lgpl_listing(packages: List[Dict[str, object]], native: Iterable[Dict[str, object]] = ()) -> Tuple[List[str], List[str]]:
    """The lines of LGPL_SOURCES.md, and what could not be found."""
    lines = ["# Where the source of the LGPL components in this release's builds is", "",
             "The builds carry these packages under the GNU Lesser General Public License, at the versions",
             "below. Their source is not in this archive: each line says where that version's source was",
             "when the release was built. See THIRD_PARTY_NOTICES.md for the licences.", ""]
    failed: List[str] = []
    for pkg in packages:
        try:
            lines.append(f"- {pkg['name']} {pkg['version']} ({pkg['licence']}): {lgpl_source(pkg)}")
        except Exception as exc:
            failed.append(f"{pkg['name']} {pkg['version']}: {exc}")
    carriers = [pkg for pkg in packages if pkg.get("qt_libraries")]
    core = next((str(pkg["qt_core"]) for pkg in carriers if pkg.get("qt_core")), "")
    if carriers:
        names = ", ".join(str(pkg["name"]) for pkg in carriers)
        try:
            if not core:
                raise ValueError("no Qt core library among their files")
            version = qt_version(core.split("/", 1)[0])
            minor = ".".join(version.split(".")[:2])
            tried = [t.format(minor=minor, version=version) for t in QT_SOURCE]
            url = first_answering(tried)
            if not url:
                raise ValueError(f"none of these answers: {', '.join(tried)}")
            lines.append(f"- Qt {version}, the libraries inside {names} (under their licence): {url}")
        except Exception as exc:
            failed.append(f"Qt, the libraries inside {names}: {exc}")
    native = list(native)
    if native:
        lines += ["", "Native libraries under the LGPL that the builds carry, each at the version read from the library",
                  "itself (or from its distribution's package), with where that version's source is:", ""]
    for lib in native:
        label = f"{lib['name']} {lib['version'] or '(version unknown)'} ({lib['licence']}), {', '.join(lib['files'])} in {lib['where']}"
        url = first_answering(list(lib["sources"])) if lib["sources"] else None
        if not url:
            failed.append(f"{label}: no source address answers ({', '.join(lib['sources']) or 'no version read'})")
            continue
        lines.append(f"- {label}: {url}" + (f" ({lib['note']})" if lib.get("note") else ""))
    return lines, failed


def cmake_fetches(text: str) -> List[Tuple[str, str, str]]:
    """("git", repository, ref) for each git source a CMake file's ExternalProject_Add or
    FetchContent_Declare fetches (ref "" when it names none), and ("url", url, "") for each archive."""
    out: List[Tuple[str, str, str]] = []
    for block in re.findall(r"(?:ExternalProject_Add|FetchContent_Declare)\s*\((.*?)\)\s*(?:\n|$)", text, re.S | re.I):
        repo = re.search(r"\bGIT_REPOSITORY\s+\"?([^\s\")]+)", block)
        ref = re.search(r"\bGIT_TAG\s+\"?([^\s\")]+)", block)
        url = re.search(r"\bURL\s+\"?(https?://[^\s\")]+)", block)
        if repo:
            out.append(("git", repo.group(1), ref.group(1) if ref else ""))
        elif url:
            out.append(("url", url.group(1), ""))
    return out


def github_archive(repo: str, ref: str) -> Optional[str]:
    """The tarball GitHub serves of `repo` at `ref` (a tag or a commit)."""
    m = re.match(r"(?:https?://|git@)github\.com[/:]([^/]+)/([^/\s]+?)(?:\.git)?/?$", repo.strip(), re.I)
    if not m or not ref:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/archive/{ref}.tar.gz"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "eli-release-gpl-sources"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def pypi_sdist(name: str, version: str) -> Optional[Dict[str, str]]:
    data = json.loads(_get(PYPI_JSON.format(name=name, version=version)).decode("utf-8"))
    for item in data.get("urls") or []:
        if item.get("packagetype") == "sdist":
            return {"url": item["url"], "filename": item["filename"], "sha256": item["digests"]["sha256"]}
    return None


_CMAKE = ("CMakeLists.txt", ".cmake")
_NATIVE = (".c", ".cc", ".cpp", ".cxx", ".pyx")


def _members(archive: bytes, filename: str, suffixes: Tuple[str, ...]) -> List[Tuple[str, bytes]]:
    """(name, content) of each file in a source archive whose name ends with one of `suffixes`."""
    found: List[Tuple[str, bytes]] = []
    if filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            found = [(n, z.read(n)) for n in z.namelist() if n.endswith(suffixes)]
    else:
        with tarfile.open(fileobj=io.BytesIO(archive)) as t:
            found = [(m.name, t.extractfile(m).read()) for m in t.getmembers()
                     if m.isfile() and m.name.endswith(suffixes)]
    return found


def _build_files(archive: bytes, filename: str) -> List[str]:
    """The CMake files inside a source archive."""
    return [body.decode("utf-8", "replace") for _name, body in _members(archive, filename, _CMAKE)]


def _tagged_archive(home: str, version: str) -> Tuple[bytes, str]:
    """The project's repository at the version's tag (v<version>, else <version>), and its address."""
    for url in [u for u in (github_archive(home, tag) for tag in (f"v{version}", version)) if u]:
        try:
            return _get(url), url
        except Exception:
            continue
    raise ValueError(f"no tag v{version} / {version} at {home or 'an unknown home page'}")


def bundle_native_lgpl(bundle: Optional[Path]) -> List[Dict[str, object]]:
    """The native LGPL libraries a built bundle carries (native_lgpl, host_libraries), none without one.
    Libraries an LGPL package already listed holds (Qt for Python's) are left to that package's line."""
    if bundle is None:
        return []
    installed = installed_distributions()
    listed = {Path(str(f)).name for pkg in lgpl_distributions()
              for f in (installed.get(_key(str(pkg["name"]))).files or [] if installed.get(_key(str(pkg["name"]))) else [])}
    root = Path(bundle)
    files = [p for p in root.rglob("*") if p.is_file() and _SHARED.search(p.name)]
    provided = {Path(str(f)).name for d in installed.values() for f in d.files or []}
    return native_lgpl(files, root, listed) + host_libraries(files, provided)


def fetch_all(out_dir: Path, bundle: Optional[Path] = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    carried = gpl_distributions()
    lines = ["# Source of the GPL components in this release's builds", ""]
    if carried:
        lines += ["Each package under the GPL that the builds carry, at the version they carry, and the",
                  "source file here with where it came from. See THIRD_PARTY_NOTICES.md for the licences.", ""]
    else:
        lines += ["This release's builds carry no package under the GPL, so there is no GPL source here.",
                  "The frozen apps start through PyInstaller's bootloader, which is under the GPL with an",
                  "exception that lets it go with any program. LGPL_SOURCES.md, beside this file, says where",
                  "the source of each LGPL package the builds carry is. See THIRD_PARTY_NOTICES.md.", ""]
    failed = []
    for pkg in carried:
        name, version = pkg["name"], pkg["version"]
        try:
            sdist = pypi_sdist(name, version)
            if sdist:
                body = _get(sdist["url"])
                if hashlib.sha256(body).hexdigest() != sdist["sha256"]:
                    raise ValueError(f"{sdist['filename']} does not match PyPI's sha256")
                filename, origin = sdist["filename"], sdist["url"]
            else:
                try:
                    body, origin = _tagged_archive(pkg["home"], version)
                except ValueError as exc:
                    raise ValueError(f"no sdist on PyPI and {exc}") from None
                filename = f"{name}-{version}.tar.gz"
            (out_dir / filename).write_bytes(body)
            lines.append(f"- {name} {version} ({pkg['licence']}): `{filename}`, from {origin}")
            build_files = _build_files(body, filename)
            if sdist and pkg.get("compiled") and not _members(body, filename, _CMAKE + _NATIVE):
                repo_body, repo_url = _tagged_archive(pkg["home"], version)
                repo_name = f"{name}-{version}-repository.tar.gz"
                (out_dir / repo_name).write_bytes(repo_body)
                lines.append(f"  - the build files and C source of its compiled part, which the sdist leaves "
                             f"out: `{repo_name}`, from {repo_url}")
                build_files += _build_files(repo_body, repo_name)
            for kind, source, ref in cmake_fetches("\n".join(build_files)):
                url = source if kind == "url" else github_archive(source, ref)
                if not url:
                    raise ValueError(f"its build fetches {source} {ref or '(no fixed commit)'}, which this cannot download")
                part = re.sub(r"[^A-Za-z0-9._-]+", "-", url.split("github.com/", 1)[-1].replace("/archive/", "-"))
                (out_dir / part).write_bytes(_get(url))
                lines.append(f"  - built into it: `{part}`, from {url} (fetched by its CMake build)")
        except Exception as exc:
            failed.append(f"{name} {version}: {exc}")
    (out_dir / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lgpl_lines, lgpl_failed = lgpl_listing(lgpl_distributions(), bundle_native_lgpl(bundle))
    (out_dir / LGPL_LISTING).write_text("\n".join(lgpl_lines) + "\n", encoding="utf-8")
    for problem in failed:
        print(f"::error::GPL source not fetched: {problem}", file=sys.stderr)
    for problem in lgpl_failed:
        print(f"::error::LGPL source not found: {problem}", file=sys.stderr)
    return 1 if failed or lgpl_failed else 0


def main(argv: List[str]) -> int:
    if argv == ["--leave-out"]:
        return leave_out()
    if argv[:1] == ["--leave-out"] and len(argv) == 2:
        return leave_out_of_wheels(Path(argv[1]))
    if argv[:1] == ["--check-wheels"] and len(argv) == 2:
        found = gpl_archives(Path(argv[1]))
        for line in found:
            print(f"::error::{argv[1]} holds what ELI v3 does not pass on: {line}", file=sys.stderr)
        if not found:
            print(f"[gpl] {argv[1]}: no package under the GPL, nothing left out")
        return 1 if found else 0
    if argv[:1] == ["--check-bundle"] and len(argv) == 2:
        found = bundle_breaches(Path(argv[1]))
        for line in found:
            print(f"::error::{argv[1]} carries what ELI v3 does not pass on: {line}", file=sys.stderr)
        if not found:
            print(f"[licences] {argv[1]}: no library or file under a strong copyleft or a non-commercial licence")
        return 1 if found else 0
    if argv[:1] == ["--kept-dependencies"] and len(argv) >= 4:
        print("\n".join(kept_dependencies(Path(argv[1]), argv[2], argv[3:])))
        return 0
    if argv[:1] == ["--list"]:
        carried = gpl_distributions()
        for pkg in carried:
            print(f"{pkg['name']}=={pkg['version']}  ({pkg['licence']})")
        if not carried:
            print("no package under the GPL")
        for pkg in breach_distributions():
            print(f"{pkg['name']}=={pkg['version']}  {pkg['class']}: {pkg['licence']}")
        for problem in stand_in_problems():
            print(f"stand-in: {problem}")
        for dist, files in left_out_files():
            print(f"left out but installed: {dist.metadata['Name']} {', '.join(files)}")
        for pkg in lgpl_distributions():
            qt = "  carries Qt libraries" if pkg["qt_libraries"] else ""
            print(f"{pkg['name']}=={pkg['version']}  ({pkg['licence']}, source not attached: {LGPL_LISTING}){qt}")
        return 0
    if len(argv) == 3 and argv[1] == "--bundle" and not argv[0].startswith("-"):
        return fetch_all(Path(argv[0]), Path(argv[2]))
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    return fetch_all(Path(argv[0]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
