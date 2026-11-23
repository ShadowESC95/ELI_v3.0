"""CLAIM: every download can put the licence in front of its user.

ELI v3 is proprietary, so the terms have to
be reachable from each shape it ships in — not just the Windows installer, which
is the only wrapper with a licence pane of its own. These pin the surfaces:

  * `LICENSE` / `NOTICE` / `THIRD_PARTY_NOTICES.md` exist and name the licence
  * the frozen bundles carry them (ELI.spec data manifest)
  * the dmg and AppImage stage them into the artifact
  * the Windows installer shows one at install time
  * `--license` resolves the text at runtime, on every platform
"""
from __future__ import annotations

from pathlib import Path

import pytest

from . import _helpers as H

REPO = H.REPO

DOCS = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")


@pytest.mark.parametrize("name", DOCS)
def test_license_doc_present(name):
    path = REPO / name
    assert path.is_file(), f"{name} missing from the repo root"
    assert path.stat().st_size > 200, f"{name} is suspiciously short"


def test_license_names_the_terms():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "PROPRIETARY COMMERCIAL SOFTWARE" in text


@pytest.mark.parametrize("name", DOCS + ("models/MODEL_LICENSES.md",))
def test_frozen_bundle_ships_license(name):
    """ELI.spec's data manifest must carry the terms into exe/zip/dmg/AppImage."""
    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    assert f'"{name}"' in spec, f"{name} not in the ELI.spec data manifest"


@pytest.mark.parametrize("script,label", [
    ("packaging/macos/build-dmg.sh", "dmg"),
    ("packaging/linux/build-appimage-pyinstaller.sh", "AppImage"),
])
def test_artifact_stages_license(script, label):
    """The dmg/AppImage have no installer step, so they stage the docs directly."""
    text = (REPO / script).read_text(encoding="utf-8")
    assert "LICENSE" in text, f"the {label} build does not stage LICENSE"
    assert "THIRD_PARTY_NOTICES.md" in text, \
        f"the {label} build does not stage THIRD_PARTY_NOTICES.md"


def test_windows_installer_shows_license():
    iss = (REPO / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "LicenseFile=" in iss, "Inno installer has no licence pane"
    assert "AppCopyright=" in iss, "Inno installer declares no copyright"


def test_an_option_this_program_does_not_take_starts_nothing(monkeypatch, capsys):
    """`python -m eli --bundel legal` (a typo, or another program's option) opened ELI v3's window as if
    nothing had been asked. An --option the command does not read is refused before anything starts;
    every one it reads still works, and what follows --bundle <id> is the bundle's own."""
    import re
    import sys
    import eli.__main__ as cli
    from eli.licensing.flags import LICENSE_FLAGS
    started = []
    monkeypatch.setattr("eli.gui.app.main", lambda: started.append("window") or 0)
    monkeypatch.setattr("eli.bundles.launch", lambda bundle, argv: started.append((bundle, list(argv))) or 0)
    monkeypatch.setattr("eli.licensing.launch_gate.launch_allowed", lambda **k: True)
    monkeypatch.setattr("eli.core.init_data.bootstrap_once", lambda: None)
    monkeypatch.setattr("eli.utils.log.keep_console_log", lambda *a, **k: None)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["eli", *args])
        return cli.main()

    assert run("--bundel", "legal") == 2 and not started
    assert "does not take --bundel" in capsys.readouterr().err
    assert run("--help") == 0 and "--no-bundles" in capsys.readouterr().out and not started
    assert run("--bundle", "legal", "--its-own-option") == 0 and started == [("legal", ["--its-own-option"])]

    # the options the command's path reads (eli/__main__.py, the licence flags, the window's start, the
    # bundle chooser) are the ones it takes
    read = set(LICENSE_FLAGS)
    for name in ("eli/__main__.py", "eli/licensing/flags.py", "eli/gui/app.py", "eli/bundles/chooser.py"):
        text = (REPO / name).read_text(encoding="utf-8")
        read |= set(re.findall(r'"(--[a-z-]+)"\s+(?:not\s+)?in\s+(?:args|argv|sys\.argv)\b', text))
    assert {"--setup", "--choose", "--no-bundles", "--bundle", "--headless"} <= read
    assert cli._unknown_options(sorted(read)) == []


def _gpl_sources():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gpl_sources", REPO / "packaging" / "gpl_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_release_attaches_the_source_of_the_gpl_packages_it_carries():
    """piper-tts and espeak-ng (GPL-3.0) were bundled, and no release said where their exact source was
    (THIRD_PARTY_NOTICES.md called piper-tts MIT): GPL-3.0 lets a binary be passed on only with its
    source, or directions to it, beside it. The mechanism stays for any GPL package a build carries;
    the voice is now Kokoro (Apache-2.0) and neither piper-tts nor espeak-ng is in v3."""
    import importlib.util
    gs = _gpl_sources()
    assert gs.is_gpl(["GPL-3.0-or-later"]) and gs.is_gpl(["GPLv2"])
    assert gs.is_gpl(["Copyright (c) 2014, a maintainer",
                      "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)"])
    assert not gs.is_gpl(["LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"])       # used under the LGPL
    assert not gs.is_gpl(["License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"])
    assert not gs.is_gpl(["Apache-2.0"]) and not gs.is_gpl(["MIT"]) and not gs.is_gpl(["EUPL-1.2"])
    cmake = (
        "include(ExternalProject)\n"
        "ExternalProject_Add(\n    speech_external\n"
        '    GIT_REPOSITORY "https://github.com/example-org/example-speech.git"\n'
        '    GIT_TAG "0123456789abcdef0123456789abcdef01234567"\n'
        "    CMAKE_ARGS -DCMAKE_INSTALL_PREFIX:PATH=${OUT}\n)\n"
        "FetchContent_Declare(data URL https://example.org/data-1.0.tar.gz)\n"
    )
    assert gs.cmake_fetches(cmake) == [
        ("git", "https://github.com/example-org/example-speech.git", "0123456789abcdef0123456789abcdef01234567"),
        ("url", "https://example.org/data-1.0.tar.gz", "")]
    assert gs.github_archive("https://github.com/example-org/example-speech.git", "v1.0") == \
        "https://github.com/example-org/example-speech/archive/v1.0.tar.gz"
    assert importlib.util.find_spec("piper") is None or "piper-tts" in {d["name"] for d in gs.gpl_distributions()}

    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    linux = release.split("\n  build-linux:", 1)[1].split("\n  build-source-artifacts:", 1)[0]
    assert "python packaging/gpl_sources.py dist/gpl-sources" in linux and "-gpl-sources.tar.gz" in linux
    assert "piper" not in linux.lower() and "espeak" not in linux.lower(), "the Linux build still carries Piper or espeak"
    notes = release.split("Write release notes stub", 1)[1].split("Verify:", 1)[0]
    assert "gpl-sources.tar.gz" in notes, "the release notes do not say where the GPL source is"
    assert "body_path: dist/RELEASE_NOTES.md" in release.split("\n  release:", 1)[1]
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "gpl-sources.tar.gz" in notices and "packaging/gpl_sources.py" in notices


def test_no_install_route_brings_piper_or_espeak():
    """The voice is Kokoro: misaki is declared without its [en] extra (espeakng-loader and
    phonemizer-fork are GPL), and nothing installs piper-tts."""
    for name in ("pyproject.toml", "requirements.txt", "requirements-full.txt", "requirements-windows.txt",
                 "requirements-macos.txt", "requirements.lock.txt"):
        text = (REPO / name).read_text(encoding="utf-8").lower()
        reqs = [line.split("#", 1)[0].strip() for line in text.splitlines()]
        for bad in ("piper-tts", "espeakng-loader", "phonemizer", "misaki[en]", "kokoro-onnx", "g2p-en", "g2p_en"):
            assert not any(r.startswith((bad, f'"{bad}')) or f'"{bad}' in r for r in reqs), f"{bad} in {name}"
    # OpenCV's macOS wheels link an FFmpeg with x264 and x265 (GPL) into cv2: no Mac route installs it,
    # and the macOS app leaves it out
    gs = _gpl_sources()
    mac = {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix", "extra": "",
           "python_version": "3.12", "python_full_version": "3.12.0", "platform_machine": "arm64"}
    declared = [gs._requirement(t) for t in gs._project(REPO)["dependencies"]]
    assert not [r for r in declared if r.name.startswith("opencv") and (r.marker is None or r.marker.evaluate(mac))]
    assert not [line for line in gs.requirement_lines(REPO / "requirements-macos.txt") if line.startswith("opencv")]
    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    darwin = [block.split("\n\n", 1)[0] for block in spec.split('\nif sys.platform == "darwin":\n')[1:]]
    assert any('excludes.append("cv2")' in block for block in darwin)


def test_the_gpl_source_archive_holds_each_sdist_and_what_its_build_fetches(tmp_path, monkeypatch, capsys):
    """The fetch, with the network replaced: a GPL package's sdist from PyPI, checked against PyPI's
    digest, and the source its CMake build fetches, at the commit the build names."""
    import hashlib
    import io
    import json
    import tarfile
    gs = _gpl_sources()
    cmake = (b'ExternalProject_Add(engine\n    GIT_REPOSITORY "https://github.com/example-org/example-engine.git"\n'
             b'    GIT_TAG "89abcdef0123456789abcdef0123456789abcdef"\n)\n')
    sdist = io.BytesIO()
    with tarfile.open(fileobj=sdist, mode="w:gz") as tar:
        info = tarfile.TarInfo("example_speech-1.0/CMakeLists.txt")
        info.size = len(cmake)
        tar.addfile(info, io.BytesIO(cmake))
    sdist = sdist.getvalue()
    engine = b"engine source"
    digest = {"value": hashlib.sha256(sdist).hexdigest()}
    served = {
        "https://files.example.org/example_speech-1.0.tar.gz": sdist,
        "https://github.com/example-org/example-engine/archive/89abcdef0123456789abcdef0123456789abcdef.tar.gz": engine,
    }

    def fake_get(url):
        if url == gs.PYPI_JSON.format(name="example-speech", version="1.0"):
            return json.dumps({"urls": [
                {"packagetype": "bdist_wheel", "url": "https://files.example.org/x.whl", "filename": "x.whl",
                 "digests": {"sha256": "0"}},
                {"packagetype": "sdist", "url": "https://files.example.org/example_speech-1.0.tar.gz",
                 "filename": "example_speech-1.0.tar.gz", "digests": {"sha256": digest["value"]}}]}).encode()
        return served[url]

    monkeypatch.setattr(gs, "_get", fake_get)
    monkeypatch.setattr(gs, "gpl_distributions", lambda: [{"name": "example-speech", "version": "1.0",
                                                           "licence": "GPL-3.0-or-later", "home": ""}])
    monkeypatch.setattr(gs, "lgpl_distributions", lambda: [])     # the next test covers those
    out = tmp_path / "gpl-sources"
    assert gs.fetch_all(out) == 0
    assert (out / "example_speech-1.0.tar.gz").read_bytes() == sdist
    fetched = [p for p in out.iterdir() if "example-engine" in p.name]
    assert len(fetched) == 1 and fetched[0].read_bytes() == engine
    listing = (out / "SOURCES.md").read_text(encoding="utf-8")
    assert "example-speech 1.0 (GPL-3.0-or-later)" in listing and "fetched by its CMake build" in listing

    digest["value"] = "f" * 64                      # PyPI's digest no longer matches the file
    assert gs.fetch_all(tmp_path / "again") == 1
    assert "does not match" in capsys.readouterr().err


def test_a_compiled_package_whose_sdist_lacks_its_build_files_gets_the_tagged_repository(tmp_path, monkeypatch):
    """piper-tts 1.4.2's sdist on PyPI holds neither its CMakeLists.txt nor the C source of its espeak
    bridge, so the archive came out without espeak-ng and the release stopped at its check. A package that
    carries compiled code its sdist cannot build gets the repository at the version's tag as well, and
    what that tree's CMake build fetches."""
    import hashlib
    import io
    import json
    import tarfile
    gs = _gpl_sources()

    def archive(files):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, body in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        return buf.getvalue()

    sdist = archive({"example_voice-2.0/setup.py": b"from skbuild import setup\n",
                     "example_voice-2.0/src/example_voice/__init__.py": b""})
    repo = archive({"example-voice-2.0/CMakeLists.txt":
                    b'ExternalProject_Add(phonemes\n    GIT_REPOSITORY https://github.com/example-org/phonemes.git\n'
                    b'    GIT_TAG 0123456789abcdef0123456789abcdef01234567  # pinned\n)\n',
                    "example-voice-2.0/src/example_voice/bridge.c": b"int main(void) { return 0; }\n"})
    served = {
        "https://files.example.org/example_voice-2.0.tar.gz": sdist,
        "https://github.com/example-org/example-voice/archive/v2.0.tar.gz": repo,
        "https://github.com/example-org/phonemes/archive/0123456789abcdef0123456789abcdef01234567.tar.gz": b"phonemes",
    }

    def fake_get(url):
        if url == gs.PYPI_JSON.format(name="example-voice", version="2.0"):
            return json.dumps({"urls": [{"packagetype": "sdist", "url": "https://files.example.org/example_voice-2.0.tar.gz",
                                         "filename": "example_voice-2.0.tar.gz",
                                         "digests": {"sha256": hashlib.sha256(sdist).hexdigest()}}]}).encode()
        if url not in served:
            raise OSError(f"404 {url}")
        return served[url]

    monkeypatch.setattr(gs, "_get", fake_get)
    package = {"name": "example-voice", "version": "2.0", "licence": "GPL-3.0-or-later",
               "home": "http://github.com/example-org/example-voice", "compiled": "yes"}
    monkeypatch.setattr(gs, "gpl_distributions", lambda: [package])
    monkeypatch.setattr(gs, "lgpl_distributions", lambda: [])     # the LGPL listing has its own test
    out = tmp_path / "gpl-sources"
    assert gs.fetch_all(out) == 0
    names = sorted(p.name for p in out.iterdir())
    assert "example_voice-2.0.tar.gz" in names and "example-voice-2.0-repository.tar.gz" in names
    assert any("phonemes" in n for n in names), names
    assert "leaves out" in (out / "SOURCES.md").read_text(encoding="utf-8")

    # pure Python under the GPL: the sdist is the whole source, and nothing else is fetched
    package["compiled"] = ""
    again = tmp_path / "pure"
    assert gs.fetch_all(again) == 0
    assert sorted(p.name for p in again.iterdir()) == [gs.LGPL_LISTING, "SOURCES.md", "example_voice-2.0.tar.gz"]


def test_the_release_says_where_the_source_of_each_lgpl_package_it_carries_is(tmp_path, monkeypatch, capsys):
    """Qt for Python and the Qt inside it, zeroconf and python-xlib (LGPL) had only an upstream
    project link. GPL-3.0 section 6(d), which LGPL-3.0 section 4 applies, lets their source stay on
    another server when directions beside the binaries say exactly where: LGPL_SOURCES.md, beside
    SOURCES.md in the release's GPL source archive, names each package, its version and the address of
    that version's source, read from the installed packages when the release is built."""
    import importlib.util
    import re
    gs = _gpl_sources()
    assert gs.is_lgpl(["LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"]) and gs.is_lgpl(["LGPLv2+"])
    assert not gs.is_lgpl(["GPL-3.0-or-later"]) and not gs.is_lgpl(["MIT"])
    for name in ("PySide6/Qt/lib/libQt6Core.so.6", "PySide6/Qt6Gui.dll", "PySide6/Qt/lib/QtCore.framework/QtCore"):
        assert gs._QT_LIBRARY.search(name), name
    for name in ("PySide6/QtCore.abi3.so", "PySide6/QtCore.pyi", "PySide6/QtCore.pyd", "shiboken6/libshiboken6.abi3.so.6.0"):
        assert not gs._QT_LIBRARY.search(name) and not gs._QT_CORE.search(name), name
    assert gs._QT_CORE.search("PySide6/Qt/lib/libQt6Core.so.6") and not gs._QT_CORE.search("PySide6/Qt/lib/libQt6Gui.so.6")

    # the network and the Qt binding replaced: invented packages, one of each kind
    qt_core = "ExampleQt/Qt/lib/libQt6Core.so.6"
    packages = [
        {"name": "example-mdns", "version": "1.2.0", "licence": "LGPL-2.1-or-later", "home": "", "repos": [],
         "qt_libraries": [], "qt_core": ""},
        {"name": "ExampleQt_Essentials", "version": "6.0.1", "licence": "LGPL-3.0-only OR GPL-3.0-only", "home": "",
         "repos": ["https://code.qt.io/cgit/pyside/pyside-setup.git/"], "qt_libraries": [qt_core], "qt_core": qt_core},
        {"name": "example-x11", "version": "0.9", "licence": "LGPLv2+", "home": "https://github.com/example-org/example-x11",
         "repos": ["https://github.com/example-org/example-x11"], "qt_libraries": [], "qt_core": ""},
    ]
    sdist = {"url": "https://files.example.org/example_mdns-1.2.0.tar.gz", "filename": "example_mdns-1.2.0.tar.gz",
             "sha256": "ab" * 32}
    pyside = gs.SOURCE_ARCHIVES["code.qt.io/cgit/pyside/pyside-setup.git"][0].format(version="6.0.1")
    qt = gs.QT_SOURCE[0].format(minor="6.0", version="6.0.2")
    x11 = "https://github.com/example-org/example-x11/archive/v0.9.tar.gz"
    answering = {pyside, qt, x11}
    monkeypatch.setattr(gs, "pypi_sdist", lambda name, version: sdist if (name, version) == ("example-mdns", "1.2.0") else None)
    monkeypatch.setattr(gs, "_answers", lambda url: url in answering)
    monkeypatch.setattr(gs, "qt_version", lambda package: {"ExampleQt": "6.0.2"}[package])
    monkeypatch.setattr(gs, "gpl_distributions", lambda: [])
    monkeypatch.setattr(gs, "lgpl_distributions", lambda: packages)
    out = tmp_path / "gpl-sources"
    assert gs.fetch_all(out) == 0, capsys.readouterr().err
    listing = (out / gs.LGPL_LISTING).read_text(encoding="utf-8").splitlines()
    assert f"- example-mdns 1.2.0 (LGPL-2.1-or-later): {sdist['url']} (sha256 {'ab' * 32})" in listing
    assert f"- ExampleQt_Essentials 6.0.1 (LGPL-3.0-only OR GPL-3.0-only): {pyside}" in listing
    assert f"- example-x11 0.9 (LGPLv2+): {x11}" in listing
    assert f"- Qt 6.0.2, the libraries inside ExampleQt_Essentials (under their licence): {qt}" in listing
    assert (out / "SOURCES.md").is_file() and pyside.endswith("pyside-setup-everywhere-src-6.0.1.tar.xz")

    answering.discard(qt)                           # Qt's source answers nowhere: the release stops
    assert gs.fetch_all(tmp_path / "again") == 1
    assert "LGPL source not found: Qt, the libraries inside ExampleQt_Essentials" in capsys.readouterr().err

    if importlib.util.find_spec("PySide6") is not None:   # the wheels as installed: which Qt they carry
        carriers = [p for p in gs.lgpl_distributions() if p["qt_core"]]
        assert carriers and re.fullmatch(r"\d+\.\d+\.\d+", gs.qt_version(carriers[0]["qt_core"].split("/", 1)[0]))

    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    linux = release.split("\n  build-linux:", 1)[1].split("\n  build-source-artifacts:", 1)[0]
    assert "cat dist/gpl-sources/LGPL_SOURCES.md" in linux
    notes = release.split("Write release notes stub", 1)[1].split("Verify:", 1)[0]
    assert "LGPL_SOURCES.md" in notes and "gpl-sources.tar.gz" in notes
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    lgpl = notices[notices.index("## Source code of the LGPL components"):]
    assert "LGPL_SOURCES.md" in lgpl and "gpl-sources.tar.gz" in lgpl and "packaging/gpl_sources.py" in lgpl


def test_the_release_says_where_the_source_of_each_native_lgpl_library_it_carries_is(tmp_path, monkeypatch):
    """LGPL_SOURCES.md named the LGPL Python packages only, from their metadata, while the AppImage carried
    LGPL libraries those packages do not declare (OpenCV's FFmpeg and Qt 5, soundfile's libsndfile) and
    others its build machine gave it (GLib, alsa-lib, libgcrypt, libsystemd). Each LGPL family
    NATIVE_LIBRARIES knows is listed at the version the library itself reports, with its source, and each
    library from the build machine with its distribution's source package. Invented files; the version
    probe, dpkg and the network replaced."""
    import subprocess
    gs = _gpl_sources()
    bundle = tmp_path / "ELI" / "_internal"
    for rel in ("example_video.libs/libavutil-0a1b.so.60.8.100", "example_video.libs/libavcodec-0a1b.so.62.11.100",
                "example_video.libs/libQt5Core-0a1b.so.5.15.18", "_example_data/libsndfile_x86_64.so",
                "libglib-2.0.so.0", "libz.so.1", "PySide6/Qt/lib/libQt6Core.so.6", "examplemod.cpython-312.so"):
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_bytes(b"\x7fELF")
    probed = {"av_version_info": "8.0.1", "sf_version_string": "1.2.2"}
    monkeypatch.setattr(gs, "_probe_version", lambda path, function: probed[function] if "avcodec" not in path.name else "")
    native = {lib["name"]: lib for lib in gs.native_lgpl([p for p in bundle.rglob("*") if p.is_file()], bundle.parent,
                                                         skip={"libQt6Core.so.6"})}
    assert sorted(native) == ["FFmpeg", "Qt", "libsndfile"]
    assert native["FFmpeg"]["version"] == "8.0.1" and len(native["FFmpeg"]["files"]) == 2
    assert native["FFmpeg"]["sources"] == ["https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.xz"]
    assert native["Qt"]["version"] == "5.15.18" and "/5.15/5.15.18/" in native["Qt"]["sources"][0]
    assert native["libsndfile"]["version"] == "1.2.2"

    copyright_dir = tmp_path / "doc"
    def dpkg(cmd, capture_output, text):
        if cmd[:2] == ["dpkg", "-S"]:
            return subprocess.CompletedProcess(cmd, 1, "libglib2.0-0t64:amd64: /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0\n"
                                                       "zlib1g:amd64: /usr/lib/x86_64-linux-gnu/libz.so.1\n", "")
        return subprocess.CompletedProcess(cmd, 0, {"libglib2.0-0t64:amd64": "glib2.0\t2.80.0-6ubuntu3\n",
                                                    "zlib1g:amd64": "zlib\t1:1.3\n"}[cmd[-1]], "")
    monkeypatch.setattr(gs.shutil, "which", lambda name: "/usr/bin/dpkg")
    monkeypatch.setattr(gs, "_os_release", lambda: {"ID": "ubuntu"})
    texts = {"libglib2.0-0t64": "License: LGPL-2.1+\n", "zlib1g": "License: Zlib\n"}
    monkeypatch.setattr(gs, "_copyright_text", lambda package: texts.get(package, ""))
    hosts = gs.host_libraries([p for p in bundle.rglob("*") if p.is_file()],
                              provided={"examplemod.cpython-312.so"}, run=dpkg)
    assert [(h["name"], h["version"], h["files"]) for h in hosts] == [("glib2.0", "2.80.0-6ubuntu3", ["libglib-2.0.so.0"])]
    assert hosts[0]["sources"] == ["https://launchpad.net/ubuntu/+source/glib2.0/2.80.0-6ubuntu3"]

    monkeypatch.setattr(gs, "first_answering", lambda urls: urls[0] if urls else None)
    lines, failed = gs.lgpl_listing([], list(native.values()) + hosts)
    assert not failed and any("FFmpeg 8.0.1" in line and "ffmpeg-8.0.1.tar.xz" in line for line in lines)
    assert any(line.startswith("- glib2.0 2.80.0-6ubuntu3") for line in lines)
    _, failed = gs.lgpl_listing([], [dict(native["FFmpeg"], version="", sources=[])])
    assert failed and "no source address" in failed[0]                 # a library it cannot place stops the release
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "python packaging/gpl_sources.py dist/gpl-sources --bundle dist/ELI" in release


def _release_extras() -> list:
    import re
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    return sorted({e for line in re.findall(r'^\s*(?:REQUIRED|OPTIONAL)_EXTRAS:\s*"([^"]*)"', release, re.M)
                   for e in line.split()})


def test_a_release_carries_no_package_under_the_gpl():
    """ELI v3 is sold under its own licence, and a package under the GPL can be passed on only with its
    source and under the GPL's terms. PyAutoGUI (BSD) brought three into every build and wheelhouse:
    MouseInfo, PyMsgBox and python3-xlib. What a release installs is read from this environment:
    pyproject.toml's dependencies and the extras release.yml bundles, through each installed package's
    own dependencies, less what every build leaves out (LEFT_OUT). None of it is under the GPL, and no
    requirements file an install route reads, the lock among them, names a package that is."""
    gs = _gpl_sources()
    installed = gs.installed_distributions()
    bundled = gs.bundled_distributions(_release_extras(), installed=installed)
    assert {"pyside6", "pydantic"} <= set(bundled), sorted(bundled)       # the walk itself works
    carried = sorted(key for key, dist in bundled.items() if gs.is_gpl(gs.licence_facts(dist)))
    assert not carried, "a release would carry packages under the GPL:\n  " + "\n  ".join(
        f"{key} {bundled[key].version} ({'; '.join(gs.licence_facts(bundled[key]))})" for key in carried)
    assert not set(gs.LEFT_OUT) & set(bundled)
    classes = {key: gs.classify(dist) for key, dist in bundled.items()}
    wrong = sorted(f"{key} {bundled[key].version}: {klass} ({said})" for key, (klass, said) in classes.items()
                   if klass in gs.BREACH or klass == gs.UNKNOWN)
    assert not wrong, "a release would carry what a closed, paid product may not pass on:\n  " + "\n  ".join(wrong)

    tools = gs.build_only(installed)
    named: dict = {}
    for path in sorted(REPO.glob("requirements*.txt")):
        for line in gs.requirement_lines(path):
            named.setdefault(gs._key(gs._requirement(line).name), set()).add(path.name)
    wrong = sorted(f"{key} ({', '.join(sorted(where))})" for key, where in named.items()
                   if key in gs.LEFT_OUT or (
                       key in installed and key not in tools and gs.is_gpl(gs.licence_facts(installed[key]))))
    assert not wrong, "an install route's requirements name packages under the GPL:\n  " + "\n  ".join(wrong)


def _top_level(dist) -> set:
    return {str(f).split("/", 1)[0] for f in dist.files or []
            if str(f).endswith(".py") and "/" in str(f) and ".dist-info" not in str(f) and not str(f).startswith("..")}


def _defined(path) -> set:
    """The names a module (or each class in it, as Class.name) defines at its top level."""
    import ast
    names = set()
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                names |= {f"{node.name}.{n.name}" for n in node.body if isinstance(n, ast.FunctionDef)}
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def test_what_every_release_leaves_out_eli_runs_without():
    """LEFT_OUT holds only while ELI uses nothing that needs those packages, PyAutoGUI still imports them
    as optional (in their place a function that raises only when it is called), and each stand-in is in
    pyproject.toml's dependencies wherever the package asks for the left-out one, and provides what
    PyAutoGUI's X11 backend takes from it."""
    import ast
    import importlib.util
    gs = _gpl_sources()
    needs: dict = {}
    for entry in gs.LEFT_OUT.values():
        needs.setdefault(entry.of, set()).update(entry.needs)
    modules = set(gs.left_out_modules())
    used = []
    for root in ("eli", "api"):
        for path in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            alias = {a.asname or a.name: a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                     for a in n.names if a.name in needs}
            for node in ast.walk(tree):
                where = f"{H.rel(path)}:{getattr(node, 'lineno', 0)}"
                if isinstance(node, ast.Import):
                    used += [f"{where} imports {a.name}" for a in node.names if a.name.split(".")[0] in modules]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    top = node.module.split(".")[0]
                    if top in modules:
                        used.append(f"{where} imports {node.module}")
                    used += [f"{where} takes {top}.{a.name}" for a in node.names if a.name in needs.get(top, ())]
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                        and node.attr in needs.get(alias.get(node.value.id, ""), ()):
                    used.append(f"{where} {node.value.id}.{node.attr}")
    assert not used, "ELI uses what only the packages every release leaves out provide:\n  " + "\n  ".join(used)

    installed = gs.installed_distributions()
    for name, entry in sorted(gs.LEFT_OUT.items()):
        spec = importlib.util.find_spec(entry.of.replace("-", "_"))
        if entry.held_by:
            _check_held(gs, entry)
        elif spec is not None and spec.submodule_search_locations and entry.modules:
            # read, not imported: on Linux importing PyAutoGUI opens the X display
            guarded, bound, imports = set(), set(), []
            for path in sorted(Path(next(iter(spec.submodule_search_locations))).glob("*.py")):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.Try) and any(
                            isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError")
                            for h in node.handlers):
                        guarded |= {id(n) for s in node.body for n in ast.walk(s)}
                        for handler in node.handlers:
                            for n in ast.walk(handler):
                                if isinstance(n, ast.FunctionDef):
                                    bound.add(n.name)
                                elif isinstance(n, ast.Assign):
                                    bound |= {t.id for t in n.targets if isinstance(t, ast.Name)}
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                        if any(m.split(".")[0] in entry.modules for m in names):
                            imports.append(node)
            assert imports and all(id(n) in guarded for n in imports), \
                f"{entry.of} imports {name} without an ImportError fallback: leaving it out would break it"
            assert set(entry.needs) <= bound, f"{entry.of} has no stand-in for {sorted(set(entry.needs) - bound)}"
        if name in installed and entry.modules:
            assert _top_level(installed[name]) == set(entry.modules), (name, _top_level(installed[name]))
        if not entry.stand_in:
            continue
        declared = [gs._requirement(text) for text in gs._project(REPO)["dependencies"]]
        stand_in = [r for r in declared if gs._key(r.name) == gs._key(entry.stand_in)]
        assert stand_in, f"{entry.stand_in} stands in for {name} and is not in pyproject.toml's dependencies"
        of = installed.get(gs._key(entry.of))
        for system, platform, os_name in (("linux", "Linux", "posix"), ("win32", "Windows", "nt"),
                                          ("darwin", "Darwin", "posix")):
            env = {"sys_platform": system, "platform_system": platform, "os_name": os_name, "extra": "",
                   "python_version": "3.12", "python_full_version": "3.12.0"}
            asked = of is not None and any(
                gs._key(gs._requirement(line).name) == name and (
                    gs._requirement(line).marker is None or gs._requirement(line).marker.evaluate(env))
                for line in of.requires or [])
            assert not asked or any(r.marker is None or r.marker.evaluate(env) for r in stand_in), \
                f"{entry.of} asks for {name} on {platform}, where pyproject.toml does not bring {entry.stand_in}"
        kept = installed.get(gs._key(entry.stand_in))
        if name in installed and kept is not None:
            assert _top_level(installed[name]) == _top_level(kept), "the stand-in provides another package"
        if kept is None or spec is None or not spec.submodule_search_locations or not gs.intact(kept):
            continue
        # what the package takes from the stand-in's import package (PyAutoGUI's X11 backend from Xlib),
        # read against the stand-in's own files
        packages = _top_level(kept)
        provided = {str(f)[:-3].replace("/", "."): Path(kept.locate_file(f)) for f in kept.files or []
                    if str(f).endswith(".py")}
        for backend in sorted(Path(next(iter(spec.submodule_search_locations))).glob("*.py")):
            tree = ast.parse(backend.read_text(encoding="utf-8"))
            taken = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in packages:
                    for a in node.names:
                        sub = f"{node.module}.{a.name}"
                        taken.append((sub, None) if sub in provided or f"{sub}.__init__" in provided
                                     else (node.module, a.name))
                elif isinstance(node, ast.Import):
                    taken += [(a.name, None) for a in node.names if a.name.split(".")[0] in packages]
            if taken:
                _check_taken(backend, tree, taken, provided, kept)


def _check_held(gs, entry):
    """A package that imports a left-out module unguarded (faster-whisper imports PyAV at the top of its
    audio module) is imported in eli/ and api/ only through ELI's function for it, which puts a
    placeholder where the module is missing: the package imports, nothing else sees the placeholder, and
    what needs the module raises, saying why."""
    import ast
    import subprocess
    import sys
    holder, function = entry.held_by.split(":")
    package = entry.of.replace("-", "_")
    direct = []
    for root in ("eli", "api"):
        for path in sorted((REPO / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
                if any(n.split(".")[0] == package for n in names):
                    direct.append(f"{H.rel(path)}:{node.lineno}")
    assert not direct, f"{package} is imported without {entry.held_by}: {direct}"
    pytest.importorskip(package)
    code = (f"import sys\nfor m in {list(entry.modules)!r}: sys.modules[m] = None\n"
            f"from {holder} import {function}\nmod = {function}()\n"
            f"assert not any(m in sys.modules for m in {list(entry.modules)!r}), 'the placeholder stayed'\n"
            f"for need in {list(entry.needs)!r}:\n"
            f"    try:\n        getattr(mod, need)('example.webm')\n    except ImportError as e:\n"
            f"        assert 'no release' in str(e), e\n    else:\n        raise SystemExit(need + ' ran')\n"
            f"print('held')\n")
    ran = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO), timeout=600)
    assert ran.returncode == 0 and "held" in ran.stdout, ran.stderr[-2000:]
    frozen = (REPO / "packaging" / "pyinstaller" / "eli_entry.py").read_text(encoding="utf-8")
    assert f"from {holder} import {function}" in frozen, "the frozen app's selftest does not import it that way"


def _check_taken(backend, tree, taken, provided, kept):
    """Every module and name `backend` takes from the stand-in's package, each attribute it reads off
    those modules, and each method it calls on an object made from a class it takes (the X11 backend's
    `_display = Display(...)`), defined in the stand-in's own source."""
    import ast
    uses = {(n.value.id, n.attr) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    uses |= {(f"{n.value.value.id}.{n.value.attr}", n.attr) for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute) and isinstance(n.value.value, ast.Name)}
    missing, classes = [], {}
    for module, attr in taken:
        path = provided.get(module) or provided.get(f"{module}.__init__")
        if path is None:
            missing.append(module)
            continue
        defined = _defined(path)
        if attr:
            if attr not in defined:
                missing.append(f"{module}.{attr}")
            else:
                classes[attr] = {n.split(".", 1)[1] for n in defined if n.startswith(f"{attr}.")}
            continue
        # a module taken whole: read as `X.MotionNotify` (from Xlib import X) or `Xlib.XK.string_to_keysym`
        names = {module} | ({module.rsplit(".", 1)[-1]} if module.count(".") == 1 else set())
        missing += [f"{module}.{a}" for owner, a in uses if owner in names and a not in defined and not a.startswith("_")]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) and node.value.func.id in classes:
            made = node.value.func.id
            for target in (t for t in node.targets if isinstance(t, ast.Name)):
                missing += [f"{made}.{a}" for owner, a in uses if owner == target.id and a not in classes[made]]
    assert not missing, f"{kept.metadata['Name']} lacks what {backend.name} uses: {sorted(set(missing))}"


def test_every_build_leaves_them_out_and_stops_on_a_gpl_package():
    """pip installs what a package declares, so each route that bundles packages takes LEFT_OUT out again:
    each frozen build, and the full suite, which installs what a release ships, after its last install
    and before PyInstaller; ELI.spec stops on any GPL package still installed (the build's own tools
    aside) and excludes the left-out modules; every wheelhouse a release ships is checked, and PyAutoGUI
    goes into the Windows one without its declared dependencies; the installers take them out of the
    environment they make."""
    import re
    step = "python packaging/gpl_sources.py --leave-out"
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for job in ("build-windows", "build-macos", "build-linux"):
        text = release.split(f"\n  {job}:", 1)[1].split("\n  build-", 1)[0]
        build = text.index("pyinstaller --noconfirm")
        installs = [m.start() for m in re.finditer(r"\bpip install\b", text[:build])]
        assert step in text and max(installs) < text.index(step) < build, job
    suite = (REPO / ".github" / "workflows" / "full-suite.yml").read_text(encoding="utf-8")
    assert step in suite and suite.rindex("pip install") < suite.index(step) < suite.rindex("python -m pytest")

    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    before = spec[:spec.index("a = Analysis(")]
    for needed in ('ROOT / "packaging" / "gpl_sources.py"', "_gpl = gpl_sources.gpl_distributions()",
                   "gpl_sources.stand_in_problems()", "gpl_sources.left_out_files()",
                   "excludes += gpl_sources.left_out_modules()"):
        assert needed in before, needed
    assert re.search(r"if _gpl:\s*\n\s*_fail\(", before)

    def joined(name):        # each command on one line
        return re.sub(r"\\\n\s*", " ", (REPO / name).read_text(encoding="utf-8"))

    build = joined("build_packages.sh")
    wheelhouse = build.split("build_windows_wheelhouse_for() {", 1)[1].split("\n}\n", 1)[0]
    assert '--check-wheels "$OUT_DIR"' in wheelhouse and "--kept-dependencies" in wheelhouse
    assert wheelhouse.index('--leave-out "$OUT_DIR"') < wheelhouse.index('--check-wheels "$OUT_DIR"')
    commands = [c for line in build.splitlines() if not line.lstrip().startswith("#") for c in line.split("&&")]
    pyautogui = [c for c in commands if re.search(r"pip (?:wheel|download)\b.*PyAutoGUI", c)]
    assert pyautogui and all("--no-deps" in c for c in pyautogui), pyautogui
    stage = build.split("_stage_windows_common() {", 1)[1].split("\n}\n", 1)[0]
    assert stage.index('--leave-out "$STAGING/wheelhouse"') < stage.index('--check-wheels "$STAGING/wheelhouse"')
    portable = joined("scripts/package_desktop_app.sh")
    assert portable.index('--leave-out "$STAGING/wheelhouse"') < portable.index('--check-wheels "$STAGING/wheelhouse"')
    lock = [line for line in portable.splitlines()
            if not line.lstrip().startswith("#") and re.search(r"pip download\b.*requirements\.lock\.txt", line)]
    assert lock and all("--no-deps" in line for line in lock), lock

    for name in ("install.sh", "install.ps1"):
        text = (REPO / name).read_text(encoding="utf-8-sig")
        assert "gpl_sources.py" in text and text.index("--leave-out") > text.rindex("install-each"), name


def _invented_dist(site, name, version, files, meta=""):
    """An installed distribution, invented: its files, and the RECORD pip writes for them."""
    import base64
    import hashlib
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{meta}", encoding="utf-8")
    rows = []
    for rel, body in files.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
        rows.append(f"{rel},sha256={digest},{len(body)}")
    rows += [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _invented_left_out(gs):
    return {"example-boxes": gs.LeftOut("example-gui", ("exampleboxes",), ("box",), ""),
            "example-x11-old": gs.LeftOut("example-gui", (), (), "example-x11")}


def test_leaving_them_out_puts_back_what_shared_their_files(tmp_path, monkeypatch, capsys):
    """python3-xlib and python-xlib install the same Xlib package, and pip's uninstall of the GPL copy
    takes the files of the other with it. Whatever shared a file with a left-out package is reinstalled
    and checked against its RECORD. Invented packages, with pip replaced."""
    import platform
    import shutil
    import subprocess
    gs = _gpl_sources()
    monkeypatch.setattr(gs, "LEFT_OUT", _invented_left_out(gs))
    site = tmp_path / "site"
    x11 = {"examplex11/__init__.py": b"new = True\n", "examplex11/display.py": b"class Display: pass\n"}
    _invented_dist(site, "example-gui", "1.0", {"examplegui/__init__.py": b""},
                   "Requires-Dist: example-boxes\nRequires-Dist: example-x11-old; platform_system == 'Linux'\n")
    _invented_dist(site, "example-boxes", "2.0", {"exampleboxes/__init__.py": b""}, "License: GPLv3+\n")
    _invented_dist(site, "example-x11", "0.3", x11, "License: LGPLv2+\n")
    _invented_dist(site, "example-x11-old", "0.1", {"examplex11/__init__.py": b"old = True\n"}, "License: GPLv2\n")
    path = [str(site)]
    assert gs.stand_in_problems(path) and "not its own" in gs.stand_in_problems(path)[0]
    ran = []

    def pip(cmd, check, restore=True):
        ran.append(cmd[3:])
        if cmd[3] == "uninstall":             # each file its RECORD lists goes, the shared ones too
            for name in cmd[5:]:
                dist = gs.installed_distributions(path)[gs._key(name)]
                info = site / f"{name.replace('-', '_')}-{dist.version}.dist-info"
                for f in dist.files:
                    Path(dist.locate_file(f)).unlink(missing_ok=True)
                shutil.rmtree(info)
        elif restore:                         # --force-reinstall writes its own files back
            for rel, body in x11.items():
                (site / rel).write_bytes(body)
        return subprocess.CompletedProcess(cmd, 0)

    assert gs.leave_out(path, run=pip) == 0
    assert ran == [["uninstall", "-y", "example-boxes", "example-x11-old"],
                   ["install", "--no-deps", "--force-reinstall", "example-x11==0.3"]]
    assert sorted(gs.installed_distributions(path)) == ["example-gui", "example-x11"]
    assert gs.stand_in_problems(path) == [] and gs.intact(gs.installed_distributions(path)["example-x11"])
    assert gs.leave_out(path, run=pip) == 0 and len(ran) == 2        # nothing left to do

    _invented_dist(site, "example-x11-old", "0.1", {"examplex11/__init__.py": b"old = True\n"}, "License: GPLv2\n")
    assert gs.leave_out(path, run=lambda cmd, check: pip(cmd, check, restore=False)) == 1
    assert "not intact after reinstalling: example-x11" in capsys.readouterr().err

    shutil.rmtree(site / "example_x11-0.3.dist-info")                    # no stand-in where one is needed
    needed = platform.system() == "Linux"                                 # where example-gui asks for one
    assert gs.stand_in_problems(path) == (
        ["example-x11 is not installed: example-gui needs it in place of example-x11-old"] if needed else [])


def test_a_wheelhouse_holding_a_gpl_package_is_refused_and_pyautogui_goes_in_without_them(tmp_path, monkeypatch, capsys):
    """The Windows wheelhouse took PyAutoGUI with everything it declares (`pip wheel PyAutoGUI` on the
    Linux build machine, python3-xlib included). It now takes PyAutoGUI alone and what it needs on the
    platform less LEFT_OUT, and a wheel or sdist under the GPL in any wheelhouse a release ships stops
    the build. Invented packages."""
    import io
    import tarfile
    import zipfile
    gs = _gpl_sources()

    def wheel(where, name, version, meta):
        path = where / f"{name}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(f"{name}/__init__.py", "")
            z.writestr(f"{name}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{meta}")
        return path

    house = tmp_path / "wheelhouse"
    house.mkdir()
    wheel(house, "example_plain", "1.0", "License: MIT\n")
    wheel(house, "example_qt", "6.0", "License-Expression: LGPL-3.0-only OR GPL-3.0-only\n")
    assert gs.gpl_archives(house) == [] and gs.main(["--check-wheels", str(house)]) == 0
    wheel(house, "example_boxes", "2.0",
          "Classifier: License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)\n")
    body = b"Metadata-Version: 1.1\nName: example-speech\nVersion: 1.0\nLicense: GPL-3.0-or-later\n"
    with tarfile.open(house / "example-speech-1.0.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("example-speech-1.0/PKG-INFO")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    assert [line.split(" ", 1)[0] for line in gs.gpl_archives(house)] == \
        ["example-speech-1.0.tar.gz", "example_boxes-2.0-py3-none-any.whl"]
    assert gs.main(["--check-wheels", str(house)]) == 1
    assert "example_boxes-2.0-py3-none-any.whl" in capsys.readouterr().err

    monkeypatch.setattr(gs, "LEFT_OUT", _invented_left_out(gs))
    gui = wheel(tmp_path, "example_gui", "1.0",
                "Requires-Dist: example-boxes\nRequires-Dist: example-tween>=1.0\n"
                'Requires-Dist: example-x11-old; platform_system == "Linux" and python_version >= "3.0"\n'
                'Requires-Dist: example-objc; platform_system == "Darwin"\n'
                'Requires-Dist: example-legacy; python_version < "3.0"\n')
    assert gs.kept_dependencies(gui, "win_amd64", ["3.11", "3.12"]) == ["example-tween>=1.0"]
    assert gs.kept_dependencies(gui, "macosx_11_0_arm64", ["3.12"]) == ["example-tween>=1.0", "example-objc"]
    assert gs.kept_dependencies(gui, "manylinux2014_x86_64", ["3.12"]) == ["example-tween>=1.0"]


def test_the_frozen_builds_carry_none_of_openwakewords_model_files():
    """ELI.spec collected all of openwakeword's data, so its pre-trained models (CC BY-NC-SA 4.0,
    non-commercial, by its own note, the two feature models among them) went into every bundle. ELI runs
    its own two feature models (eli/perception/wake_features/, packaging/wake_features.py), and every
    build and installer leaves all of the package's model files out."""
    import inspect
    import re
    from eli.perception import wakeword

    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    code = [line for line in spec.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if '"openwakeword"' in line], "ELI.spec collects openwakeword's data again"
    gs = _gpl_sources()
    entry = gs.LEFT_OUT_FILES["openwakeword"]
    assert entry.keep == ()
    for stem in ("melspectrogram", "embedding_model", "alexa_v0.1", "hey_jarvis_v0.1", "silero_vad"):
        assert entry.matches(f"openwakeword/resources/models/{stem}.onnx"), stem
    assert all((wakeword.FEATURE_DIR / f"{stem}.onnx").is_file() for stem in wakeword.FEATURE_MODELS)
    assert "FEATURE_DIR" in inspect.getsource(wakeword._audio_features)
    oww = pytest.importorskip("openwakeword")
    from openwakeword.utils import AudioFeatures
    loaded = set(re.findall(r'"(\w+)\.(?:onnx|tflite)"', inspect.getsource(AudioFeatures.__init__)))
    assert loaded and set(wakeword.FEATURE_MODELS) == loaded, "FEATURE_MODELS is not what the extractor runs"
    assert {Path(p).stem for p in oww.get_pretrained_model_paths()}


def test_what_a_kept_package_holds_under_such_a_licence_eli_never_uses():
    """SpeechRecognition's FLAC programs (GPL-2.0) run only for AudioData.get_flac_data(), which only its
    online recognisers call; ELI calls none of them and records and transcribes locally."""
    import ast
    gs = _gpl_sources()
    flac = gs.LEFT_OUT_FILES["speechrecognition"]
    for name in ("flac-linux-x86", "flac-linux-x86_64", "flac-mac", "flac-win32.exe"):
        assert flac.matches(f"speech_recognition/{name}")
    assert not flac.matches("speech_recognition/audio.py") and not flac.matches("speech_recognition/__init__.py")
    used = []
    for root in ("eli", "api"):
        for path in sorted((REPO / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute) and (node.attr == "get_flac_data" or node.attr.startswith("recognize_")):
                    used.append(f"{H.rel(path)}:{node.lineno} .{node.attr}")
    assert not used, "ELI uses what needs SpeechRecognition's FLAC programs:\n  " + "\n  ".join(used)
    sr = gs.installed_distributions().get("speechrecognition")
    if sr is not None:
        held = [f for f, _ in gs._record(sr) if flac.matches(f)]
        assert all(Path(f).name.startswith("flac-") for f in held), held


def test_the_files_left_out_go_from_the_environment_and_the_wheels(tmp_path, monkeypatch, capsys):
    """openWakeWord's wheel holds its non-commercial wake-word models beside the two models ELI loads.
    --leave-out deletes such files from the installed package and their rows from its RECORD (it stays
    whole as installed), rewrites a wheelhouse's wheels without them, and --check-wheels refuses a wheel
    or sdist that still holds them. Invented packages."""
    import io
    import tarfile
    import zipfile
    gs = _gpl_sources()
    entry = gs.LeftOutFiles(r"^examplewake/models/[^/]+$", ("features",), "CC-BY-NC-SA-4.0", "the head is ELI's own")
    monkeypatch.setattr(gs, "LEFT_OUT_FILES", {"example-wake": entry})
    monkeypatch.setattr(gs, "LEFT_OUT", {})
    site = tmp_path / "site"
    files = {"examplewake/__init__.py": b"", "examplewake/models/features.onnx": b"kept",
             "examplewake/models/hey_example.onnx": b"non-commercial", "examplewake/models/other.tflite": b"nc"}
    _invented_dist(site, "example-wake", "0.4", files, "License: Apache-2.0\n")
    path = [str(site)]
    assert [sorted(f) for _d, f in gs.left_out_files(path)] == \
        [["examplewake/models/hey_example.onnx", "examplewake/models/other.tflite"]]
    assert gs.leave_out(path) == 0
    assert gs.left_out_files(path) == [] and (site / "examplewake/models/features.onnx").is_file()
    assert not (site / "examplewake/models/hey_example.onnx").exists()
    dist = gs.installed_distributions(path)["example-wake"]
    assert gs.intact(dist) and not [f for f, _ in gs._record(dist) if "hey_example" in f]
    assert "leaving out 2 file(s) of example-wake 0.4" in capsys.readouterr().out
    assert gs.leave_out(path) == 0                                   # nothing left to do

    house = tmp_path / "wheelhouse"
    house.mkdir()
    wheel = house / "example_wake-0.4-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for rel, body in files.items():
            z.writestr(rel, body)
        z.writestr("example_wake-0.4.dist-info/METADATA", "Metadata-Version: 2.1\nName: example-wake\nVersion: 0.4\n")
        z.writestr("example_wake-0.4.dist-info/RECORD", "".join(f"{rel},,\n" for rel in files))
    assert gs.main(["--check-wheels", str(house)]) == 1
    assert "holds 2 file(s) under CC-BY-NC-SA-4.0" in capsys.readouterr().err
    assert gs.main(["--leave-out", str(house)]) == 0
    with zipfile.ZipFile(wheel) as z:
        assert sorted(n for n in z.namelist() if "/models/" in n) == ["examplewake/models/features.onnx"]
        assert "hey_example" not in z.read("example_wake-0.4.dist-info/RECORD").decode()
    assert gs.main(["--check-wheels", str(house)]) == 0
    body = b"Metadata-Version: 1.1\nName: example-wake\nVersion: 0.4\n"
    with tarfile.open(house / "example-wake-0.4.tar.gz", "w:gz") as tar:
        for name, data in (("example-wake-0.4/PKG-INFO", body), ("example-wake-0.4/examplewake/models/x.onnx", b"nc")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    assert gs.main(["--check-wheels", str(house)]) == 1               # an sdist is refused, not rewritten


def test_each_package_and_file_is_sorted_by_what_a_closed_paid_product_may_pass_on(tmp_path, monkeypatch, capsys):
    """Only the GPL was looked for, in each package's own metadata: PyAV (BSD-3-Clause) carried x264 and
    x265 (GPL-2.0) inside its FFmpeg into every bundle, and a non-commercial licence or none at all went
    through. Every package is sorted into a licence class (permissive, vendor terms, weak copyleft,
    unknown, strong copyleft, non-commercial) from its SPDX expression, its License field, its
    classifiers or its licence file, and the libraries it carries; ELI.spec stops on the last two and on
    unknown, and each build checks what it built for such libraries and the files left out. Invented
    packages and file names."""
    import re
    gs = _gpl_sources()

    class Meta(dict):
        def get_all(self, key):
            value = self.get(key)
            return value if isinstance(value, list) else ([value] if value else [])

    def of(expression="", licence="", classifiers=()):
        return gs.licence_class(Meta({"License-Expression": expression, "License": licence,
                                      "Classifier": [f"License :: {c}" for c in classifiers]}))

    assert of("MIT") == of("Apache-2.0 OR BSD-3-Clause") == of(licence="BSD") == gs.PERMISSIVE
    assert of("EPL-2.0 OR BSD-3-Clause") == gs.PERMISSIVE                  # the freer of two offered
    assert of("MPL-2.0 AND MIT") == of(licence="MPL-2.0 AND (Apache-2.0 OR MIT)") == gs.WEAK
    assert of("LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only") == gs.WEAK  # Qt for Python, used under the LGPL
    assert of("GPL-2.0-or-later WITH Classpath-exception-2.0") == gs.WEAK
    assert of(licence="GPLv2-or-later with a special exception which allows to use it in non-free programs") == gs.WEAK
    assert of("GPL-3.0-or-later") == of(licence="GPLv2") == of("AGPL-3.0-only") == gs.STRONG
    assert of(licence="Copyright (c) 2014, a maintainer",
              classifiers=["OSI Approved :: GNU General Public License v3 or later (GPLv3+)"]) == gs.STRONG
    assert of("CC-BY-NC-SA-4.0") == of(licence="Example Research License Agreement") == gs.NONCOMMERCIAL
    assert of(licence="Free for non-commercial use") == of("LicenseRef-Example-NC-1.0") == gs.NONCOMMERCIAL
    assert of("LicenseRef-NVIDIA-Proprietary") == gs.VENDOR
    assert of(licence="Permission is hereby granted, free of charge, to any person obtaining a copy") == gs.PERMISSIVE
    assert of(licence="", classifiers=["Other/Proprietary License"]) == of() == gs.UNKNOWN
    assert of("CC-BY-4.0") == gs.PERMISSIVE and of("CC-BY-SA-4.0") == gs.WEAK

    names = ["av.libs/libx264-0123abcd.so.165", "av/.dylibs/libx265.215.dylib", "av.libs/libx264-165-0123.dll",
             "_internal/libreadline.so.8", "_internal/libdb-5.3.so", "cv2/.dylibs/libpostproc.58.dylib",
             "speech_recognition/flac-linux-x86_64", "example/flac-win32.exe"]
    clean = ["_internal/libdbus-1.so.3", "_internal/libFLAC.so.12", "pyttsx3/drivers/espeak.py",
             "_internal/libavcodec-0123.so.61", "_internal/libsndfile.so.1"]
    assert [n for n, _ in gs.native_breaches(names + clean)] == names

    site = tmp_path / "site"
    _invented_dist(site, "example-video", "1.0", {"examplevideo/__init__.py": b"",
                   "examplevideo.libs/libx264-0123.so.165": b"\x7fELF"}, "License-Expression: BSD-3-Clause\n")
    _invented_dist(site, "example-weights", "1.0", {"exampleweights/__init__.py": b""},
                   "License: Example Research License (non-commercial)\n")
    _invented_dist(site, "example-silent", "1.0", {"examplesilent/__init__.py": b""})
    _invented_dist(site, "example-known", "1.0", {"exampleknown/__init__.py": b""})
    _invented_dist(site, "example-qt", "6.0", {"exampleqt/__init__.py": b""},
                   "License-Expression: LGPL-3.0-only OR GPL-3.0-only\n")
    _invented_dist(site, "example-plain", "1.0", {"exampleplain/__init__.py": b""}, "License-Expression: MIT\n")
    installed = gs.installed_distributions([str(site)])
    monkeypatch.setattr(gs, "KNOWN_LICENCES", {"example-known": ("MIT", "read by hand from its repository")})
    assert gs.classify(installed["example-video"])[0] == gs.STRONG
    assert "libx264-0123.so.165" in gs.classify(installed["example-video"])[1]
    assert gs.classify(installed["example-known"]) == (gs.PERMISSIVE, "MIT (read by hand from its repository)")
    found = {p["name"]: p["class"] for p in gs.breach_distributions(installed)}
    assert found == {"example-video": gs.STRONG, "example-weights": gs.NONCOMMERCIAL, "example-silent": gs.UNKNOWN}

    bundle = tmp_path / "ELI"
    for rel in ("_internal/av.libs/libx264-0123.so.165", "_internal/libdbus-1.so.3", "_internal/examplegui.py",
                "_internal/eli/perception/wake_features/melspectrogram.onnx",
                "_internal/openwakeword/resources/models/hey_example_v0.1.onnx"):
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_bytes(b"")
    assert [line.split(":", 1)[0] for line in gs.bundle_breaches(bundle)] == \
        ["_internal/av.libs/libx264-0123.so.165", "_internal/openwakeword/resources/models/hey_example_v0.1.onnx"]
    assert gs.main(["--check-bundle", str(bundle)]) == 1 and "libx264-0123.so.165" in capsys.readouterr().err
    for rel in ("_internal/av.libs/libx264-0123.so.165", "_internal/openwakeword/resources/models/hey_example_v0.1.onnx"):
        (bundle / rel).unlink()
    assert gs.main(["--check-bundle", str(bundle)]) == 0

    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    before = spec[:spec.index("a = Analysis(")]
    assert re.search(r"_breach = gpl_sources\.breach_distributions\(\)\s*\nif _breach:\s*\n\s*_fail\(", before)
    assert '"readline",' in spec[spec.index("excludes = ["):spec.index("a = Analysis(")]
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for job, built in (("build-windows", "dist/ELI"), ("build-macos", "dist/ELI.app"), ("build-linux", "dist/ELI")):
        text = release.split(f"\n  {job}:", 1)[1].split("\n  build-", 1)[0]
        check = text.index(f"python packaging/gpl_sources.py --check-bundle {built}\n")
        assert text.index("pyinstaller --noconfirm") < check < text.index("Smoke check"), job
    # Ubuntu's PortAudio links JACK, and JACK links Berkeley DB (Sleepycat): the Linux build compiles
    # PyAudio against a PortAudio of its own, built for ALSA alone from a checked source archive
    linux = release.split("\n  build-linux:", 1)[1].split("\n  build-source-artifacts:", 1)[0]
    assert "portaudio19-dev" not in linux and "--without-jack" in linux and "sha256sum -c" in linux
    assert linux.index("--without-jack") < linux.index('pip install ".[$(echo $REQUIRED_EXTRAS')


def test_every_model_eli_bundles_or_fetches_carries_its_own_licence_beside_it(tmp_path, monkeypatch):
    """The models ELI passes on or fetches (the voice, the tagger and G2P model, the embedder, the wake
    word's feature models, Silero's VAD, Whisper, Qwen3-TTS) each had a licence written only in
    models/MODEL_LICENSES.md, far from the files. Each has an entry under a licence a closed, paid
    product may pass on, with its licence's own text, written beside its files wherever they are kept:
    by the release builds (ELI.spec, which stops on a model file without one), by voice_assets and
    tts_qwen3 when they fetch, and by the package builder."""
    from eli.perception import tts_qwen3, wakeword
    from eli.runtime import model_licences as ml, voice_assets as va
    from tests._sparse import sparse_file
    gs = _gpl_sources()
    for model in ml.MODELS.values():
        assert model.licence == ml.OWN or gs.licence_text_class(model.licence) == gs.PERMISSIVE, (model.key, model.licence)
        assert (model.source.startswith("https://") or model.licence == ml.OWN) and model.files, model.key
        if model.text:
            text = (ml.TEXTS / model.text).read_text(encoding="utf-8")
            assert gs.licence_text_class(text[:4000]) == gs.PERMISSIVE, model.text
            if model.licence == "MIT":
                assert "Copyright (c)" in text and "Permission is hereby granted" in text, model.text
    covered = {f"tts_kokoro/{e.rel}" for e in va.kokoro_manifest() if not e.unpack}
    covered |= {f"tts_kokoro/{e.rel}/meta.json" for e in va.kokoro_manifest() if e.unpack}
    covered |= {f"eli/perception/wake_features/{stem}.onnx" for stem in wakeword.FEATURE_MODELS}
    covered |= {f"models--{e['repo'].replace('/', '--')}/snapshots/{e['revision']}/{rel}"
                for kind in tts_qwen3.MODELS.values() for e in kind for rel in e["weights"]}
    covered |= {"models/whisper/models--Systran--faster-whisper-small.en/snapshots/x/model.bin",
                "faster_whisper/assets/silero_vad_v6.onnx", "speech_recognition/pocketsphinx-data/en-US/mdef"}
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    covered |= set(__import__("re").findall(r'dest="(models/embeddings/[^"]+)"', release))
    assert len(covered) > 40 and not [rel for rel in covered if ml.for_file(rel) is None]

    folder = tmp_path / "kokoro"
    for entry in va.kokoro_manifest():
        path = folder / entry.rel
        if entry.unpack:
            path.mkdir(parents=True)
            (path / va._MARKER).write_text(entry.sha256, encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            sparse_file(path, entry.size)
    assert va.ensure_kokoro(dest=folder)["ok"]
    for key in ("kokoro-82m", "en-core-web-sm", "gruut-en-g2p"):
        notice = (folder / ml.licence_file(key)).read_text(encoding="utf-8")
        assert notice.startswith(ml.MODELS[key].name) and f"Licence: {ml.MODELS[key].licence}" in notice
    before = (folder / ml.licence_file("kokoro-82m")).stat().st_mtime_ns
    assert va.ensure_kokoro(dest=folder)["ok"] and (folder / ml.licence_file("kokoro-82m")).stat().st_mtime_ns == before

    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    after = spec[spec.index("a = Analysis("):spec.index("pyz = PYZ(a.pure)")]
    for needed in ("_model_licences.for_file(_dest)", "_model_licences.licensed_in(", "a.datas.append(",
                   "gpl_sources.licence_text_class(_model.licence) in gpl_sources.BREACH", "_fail("):
        assert needed in after, needed
    portable = (REPO / "scripts" / "package_desktop_app.sh").read_text(encoding="utf-8")
    assert '"$ROOT/scripts/asset_release_policy.py" --licences "$STAGING"' in portable
    assert "model_licences.place(\"qwen3-tts\"" in (REPO / "eli" / "perception" / "tts_qwen3.py").read_text(encoding="utf-8")


def test_a_package_or_an_asset_release_passes_on_no_model_a_closed_product_may_not(tmp_path, monkeypatch):
    """scripts/package_desktop_app.sh bundled Qwen2.5-3B, under the non-commercial Qwen Research
    License, whenever models/ had it, as the starter of a package that goes to every user, and the asset
    release published every model the catalog names. Both now take only a catalog model whose licence
    (the entry's own, else the one ELI records for a model its builds carry: the embedder) lets ELI v3
    pass it on; the starter is the smallest of those that models/ holds. Invented catalog entries."""
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    import asset_release_policy as policy
    from eli.core import model_download as md
    entries = [
        {"key": "example-small-nc", "filename": "small-nc.gguf", "url": "https://example.invalid/a", "size_gb": 1.0,
         policy.CATALOG_LICENCE_FIELD: "Example Research License (non-commercial)"},
        {"key": "example-medium", "filename": "medium.gguf", "url": "https://example.invalid/b", "size_gb": 4.0,
         policy.CATALOG_LICENCE_FIELD: "Apache-2.0"},
        {"key": "example-large", "filename": "large.gguf", "url": "https://example.invalid/c", "size_gb": 9.0,
         policy.CATALOG_LICENCE_FIELD: "MIT"},
        {"key": "example-custom", "filename": "custom.gguf", "url": "https://example.invalid/d", "size_gb": 2.0,
         policy.CATALOG_LICENCE_FIELD: "Example Model License with an acceptable-use policy"},
        {"key": "example-silent", "filename": "silent.gguf", "url": "https://example.invalid/e", "size_gb": 0.5},
    ]
    monkeypatch.setattr(md, "CATALOG", entries)
    monkeypatch.setattr(md, "list_catalog", lambda: list(entries))
    monkeypatch.setattr(md, "list_aux", lambda: [{"key": "embedder", "filename": "nomic-embed-text-v1.5.Q4_K_M.gguf",
                                                 "url": "https://example.invalid/f"}])
    policy.catalog_model_filenames.cache_clear()
    try:
        assert policy.catalog_model_filenames() == {"medium.gguf", "large.gguf", "nomic-embed-text-v1.5.Q4_K_M.gguf"}
        for entry in entries:
            (tmp_path / entry["filename"]).write_bytes(b"")
        assert policy.starter_model(tmp_path)["filename"] == "medium.gguf"
        (tmp_path / "medium.gguf").unlink()
        assert policy.starter_model(tmp_path)["filename"] == "large.gguf"
        (tmp_path / "large.gguf").unlink()
        assert policy.starter_model(tmp_path) is None
        package = tmp_path / "package"
        (package / "models" / "embeddings").mkdir(parents=True)
        (package / "models" / "large.gguf").write_bytes(b"")
        (package / "models" / "embeddings" / "nomic-embed-text-v1.5.Q4_K_M.gguf").write_bytes(b"")
        assert policy.place_licences(package) == []
        assert "Licence: MIT" in (package / "models" / "LICENSE.example-large.txt").read_text(encoding="utf-8")
        assert (package / "models" / "embeddings" / "LICENSE.nomic-embed-text-v1.5.txt").is_file()
        (package / "models" / "mystery.gguf").write_bytes(b"")
        assert policy.place_licences(package) == ["models/mystery.gguf"]
    finally:
        policy.catalog_model_filenames.cache_clear()
    script = (REPO / "scripts" / "package_desktop_app.sh").read_text(encoding="utf-8")
    assert '"$ROOT/scripts/asset_release_policy.py" --starter "$ASSET_ROOT/models"' in script
    assert not [e["filename"] for e in md.CATALOG if e["filename"] in script], "a model is named in the script"
    assert 'cp -a "$ASSET_ROOT/models/." "$STAGING/models/"' not in script, "models/ is copied whole again"


def test_a_wheelhouse_installs_offline_without_what_is_left_out(tmp_path, monkeypatch, capsys):
    """With PyAV taken out of the Windows full wheelhouse, pip installing faster-whisper from it offline
    stopped on faster-whisper's `av>=11`, as PyAutoGUI's install did on MouseInfo and PyMsgBox. --leave-out DIR
    puts a placeholder in the place of each left-out package whose declaring package is there: a wheel of
    metadata alone, above any version the project publishes, which every install's --leave-out uninstalls
    again. pip resolves the set offline. Invented packages; pip itself, offline."""
    import subprocess
    import sys
    import zipfile
    gs = _gpl_sources()
    monkeypatch.setattr(gs, "LEFT_OUT", {"example-codec": gs.LeftOut("example-speech", ("examplecodec",), ("decode",), "")})
    house = tmp_path / "wheelhouse"
    house.mkdir()

    def wheel(name, version, requires=""):
        dist = name.replace("-", "_")
        path = house / f"{dist}-{version}-py3-none-any.whl"
        meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{requires}"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(f"{dist}/__init__.py", "")
            z.writestr(f"{dist}-{version}.dist-info/METADATA", meta)
            z.writestr(f"{dist}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            z.writestr(f"{dist}-{version}.dist-info/RECORD", "")
        return path

    wheel("example-speech", "1.2", "Requires-Dist: example-codec>=11\nRequires-Dist: example-tokens\n")
    wheel("example-tokens", "0.2")
    wheel("example-codec", "17.0", "License: BSD-3-Clause\n")
    assert gs.main(["--leave-out", str(house)]) == 0
    names = sorted(p.name for p in house.iterdir())
    assert names == [f"example_codec-{gs.PLACEHOLDER_VERSION}-py3-none-any.whl", "example_speech-1.2-py3-none-any.whl",
                     "example_tokens-0.2-py3-none-any.whl"], names
    assert gs.main(["--check-wheels", str(house)]) == 0
    capsys.readouterr()
    ran = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-index", "--find-links", str(house),
                          "--target", str(tmp_path / "target"), "--disable-pip-version-check", "example-speech"],
                         capture_output=True, text=True, timeout=300)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert f"example-codec-{gs.PLACEHOLDER_VERSION}" in ran.stdout and "example-speech-1.2" in ran.stdout
    assert gs.main(["--leave-out", str(house)]) == 0 and len(list(house.iterdir())) == 3     # kept, not doubled
    build = (REPO / "build_packages.sh").read_text(encoding="utf-8")
    loop = build.split("build_windows_wheelhouse_for() {", 1)[1].split("torch \\", 1)[0]
    assert '--abi "$abi" --abi abi3 --abi none' in loop, "the wheelhouse leaves out abi3 wheels (tokenizers) again"


def test_the_source_archive_says_plainly_when_the_builds_carry_no_gpl_package(tmp_path, monkeypatch):
    """With PyAutoGUI's three left out, SOURCES.md listed nothing and said nothing. The build's own tools
    (PyInstaller, under the GPL with its bootloader exception) were fetched as if a build carried them."""
    gs = _gpl_sources()
    site = tmp_path / "site"
    _invented_dist(site, "pyinstaller", "6.0", {"PyInstaller/__init__.py": b""},
                   "License: GPLv2-or-later with a special exception\n")
    _invented_dist(site, "example-speech", "1.0", {"examplespeech/__init__.py": b""}, "License: GPL-3.0-or-later\n")
    installed = gs.installed_distributions([str(site)])
    monkeypatch.setattr(gs, "installed_distributions", lambda path=None: dict(installed))
    assert [p["name"] for p in gs.gpl_distributions()] == ["example-speech"]
    installed.pop("example-speech")
    assert gs.gpl_distributions() == []
    monkeypatch.setattr(gs, "lgpl_distributions", lambda: [])
    out = tmp_path / "gpl-sources"
    assert gs.fetch_all(out) == 0
    assert sorted(p.name for p in out.iterdir()) == [gs.LGPL_LISTING, "SOURCES.md"]
    assert "carry no package under the GPL" in (out / "SOURCES.md").read_text(encoding="utf-8")


def test_the_third_party_notices_name_this_product():
    """THIRD_PARTY_NOTICES.md, which every download carries, was headed "ELI v2.0", another product."""
    product = (REPO / "NOTICE").read_text(encoding="utf-8").splitlines()[0].strip()
    title = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").splitlines()[0]
    assert product == "ELI v3" and title == f"# Third-Party Notices — {product}", title


def test_license_flag_wired_into_both_entry_points():
    src = (REPO / "eli" / "__main__.py").read_text(encoding="utf-8")
    frozen = (REPO / "packaging" / "pyinstaller" / "eli_entry.py").read_text(encoding="utf-8")
    for name, text in (("eli/__main__.py", src), ("eli_entry.py", frozen)):
        assert "--license" in text, f"{name} does not handle --license"
        assert "license_info" in text, f"{name} does not call the licence printer"


def test_license_resolves_at_runtime():
    from eli.runtime.license_info import license_path, license_text

    assert license_path() is not None, "LICENSE not resolvable from a source checkout"
    text = license_text()
    assert "PROPRIETARY COMMERCIAL SOFTWARE" in text
    assert "Jason Fitzgibbon Bridgeman (GitHub: ShadowESC95) <jaybridgeman0095@gmail.com>" in text


def test_who_made_eli_is_read_from_the_licence_it_ships_with():
    """Asked who made it, the model invented a company; the licence names the maker."""
    from eli.runtime.license_info import license_text, made_by
    maker = made_by()
    assert maker and maker in license_text() and "rights" not in maker.lower()
    assert maker == "Jason Fitzgibbon Bridgeman", maker   # the name, never the handle or a cut-off email
