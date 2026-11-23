"""Voice library: Kokoro's pinned files, checksum-checked fetch, the voices a user can pick."""
from __future__ import annotations

import hashlib
import io
import re
import sys
import zipfile

import pytest

from eli.perception import tts_kokoro
from eli.runtime import voice_assets as va


# ── the pinned files ────────────────────────────────────────────────────────
def test_every_kokoro_file_is_pinned_to_a_revision_and_a_checksum():
    files = va.kokoro_manifest()
    assert len(files) == len(va.KOKORO_ENGINE_FILES) + len(tts_kokoro.VOICES)
    for f in files:
        assert re.fullmatch(r"[0-9a-f]{64}", f.sha256), f.rel
        assert f.size > 0 and f.url.startswith("https://"), f.rel
        assert "/main/" not in f.url and "/master/" not in f.url, f"{f.rel} follows a branch, not a revision"
    assert {f.rel.split("/")[1][:-4] for f in files if f.rel.startswith("voices/")} == set(tts_kokoro.VOICES)


def test_only_english_voices_whose_text_side_needs_no_espeak_are_offered():
    """misaki reads Kokoro's other languages through espeak-ng (GPL); ELI offers American and
    British English only."""
    assert {m["accent"] for m in tts_kokoro.VOICES.values()} == {"a", "b"}
    assert all(v[:2] in ("af", "am", "bf", "bm") for v in tts_kokoro.VOICES)


class _Net:
    """A stand-in for netguard: each URL answers with the bytes given, or refuses."""

    def __init__(self, bodies):
        self.bodies, self.asked = bodies, []

    def allow_network(self, _reason):
        class _C:
            def __enter__(self):
                return None

            def __exit__(self, *a):
                return False
        return _C()

    def guarded_urlopen(self, req, timeout=0):
        self.asked.append(req.full_url)
        if req.full_url not in self.bodies:
            raise OSError("refused")
        return io.BytesIO(self.bodies[req.full_url])


def _net(monkeypatch, bodies):
    # Patch the attribute on the package as well as sys.modules: `from eli.core import netguard`
    # reads the bound attribute once the real module was imported by an earlier test.
    import eli.core
    net = _Net(bodies)
    monkeypatch.setattr(eli.core, "netguard", net, raising=False)
    monkeypatch.setitem(sys.modules, "eli.core.netguard", net)
    return net


def _entry(body: bytes, rel="g2p_en.crf", unpack=""):
    return va.KokoroFile(rel, f"https://example.invalid/{rel}", hashlib.sha256(body).hexdigest(), len(body), unpack)


def test_a_download_that_does_not_match_its_checksum_is_refused_and_leaves_nothing(monkeypatch, tmp_path):
    good = _entry(b"the real file")
    _net(monkeypatch, {good.url: b"the real fil3"})
    with pytest.raises(ValueError, match="checksum"):
        va._fetch(good, tmp_path)
    assert list(tmp_path.iterdir()) == []
    _net(monkeypatch, {good.url: b"the real file"})
    va._fetch(good, tmp_path)
    assert (tmp_path / "g2p_en.crf").read_bytes() == b"the real file"


def test_a_refused_download_makes_no_folder(monkeypatch, tmp_path):
    """The folder is made once a download has started: a test run (or an offline machine) that asks
    for the voice leaves its models folder as it was."""
    _net(monkeypatch, {})
    with pytest.raises(OSError):
        va._fetch(_entry(b"x", rel="voices/af_heart.bin"), tmp_path / "kokoro")
    assert not (tmp_path / "kokoro").exists()


def _wheel(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_the_tagger_is_unpacked_from_its_wheel_and_nothing_else(monkeypatch, tmp_path):
    prefix = "en_core_web_sm/en_core_web_sm-3.8.0/"
    body = _wheel({prefix + "config.cfg": "[nlp]", prefix + "tagger/model": "w",
                   "en_core_web_sm/__init__.py": "import os", "en_core_web_sm-3.8.0.dist-info/RECORD": ""})
    entry = _entry(body, rel="en_core_web_sm-3.8.0", unpack=prefix)
    _net(monkeypatch, {entry.url: body})
    va._fetch(entry, tmp_path)
    got = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert got == ["en_core_web_sm-3.8.0/.eli-sha256", "en_core_web_sm-3.8.0/config.cfg",
                   "en_core_web_sm-3.8.0/tagger/model"]
    assert va._present_in(tmp_path, entry) == tmp_path / "en_core_web_sm-3.8.0"


def test_a_wheel_that_writes_outside_its_folder_is_refused(monkeypatch, tmp_path):
    prefix = "en_core_web_sm/en_core_web_sm-3.8.0/"
    body = _wheel({prefix + "config.cfg": "[nlp]", prefix + "../../escape.txt": "x"})
    entry = _entry(body, rel="en_core_web_sm-3.8.0", unpack=prefix)
    _net(monkeypatch, {entry.url: body})
    with pytest.raises(ValueError, match="unsafe"):
        va._fetch(entry, tmp_path)
    assert not (tmp_path.parent / "escape.txt").exists() and list(tmp_path.iterdir()) == []


def test_files_already_here_are_not_fetched_again(monkeypatch, tmp_path):
    net = _net(monkeypatch, {})
    monkeypatch.setattr(va, "_locate", lambda entry: tmp_path)
    assert va.ensure_kokoro(["af_heart"]) == {"ok": True, "asset": "kokoro", "already_present": True, "fetched": []}
    assert net.asked == []
    assert va.ensure_kokoro(["no_such_voice"])["ok"] is False


def test_a_release_build_reads_the_files_where_the_bundle_keeps_them(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle" / "tts_kokoro"
    for key, entry in va.KOKORO_ENGINE_FILES.items():
        path = bundle / entry.rel
        if entry.unpack:
            path.mkdir(parents=True)
            (path / va._MARKER).write_text(entry.sha256, encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as fh:
                fh.truncate(entry.size)           # the size is what is checked on the way in
    monkeypatch.setattr(va, "kokoro_dir", lambda: tmp_path / "user" / "models" / "tts" / "kokoro")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    files = va.kokoro_files()
    assert files and all(str(p).startswith(str(bundle)) for p in files.values())
    assert not (tmp_path / "user").exists(), "the bundle's files were copied out"


# ── the voices a user can pick ──────────────────────────────────────────────
def test_the_library_lists_every_voice_with_its_grade_and_whether_it_is_here(monkeypatch):
    monkeypatch.setattr(va, "kokoro_voice_file", lambda vid: "here" if vid == "bm_george" else None)
    rows = va.list_available_voices()
    assert {r["id"] for r in rows} == set(tts_kokoro.VOICES)
    george = next(r for r in rows if r["id"] == "bm_george")
    assert george["present"] and george["language"] == "en_GB" and george["quality"] == "C"
    assert not any(r["restricted"] for r in rows)
    assert {r["id"] for r in va.list_available_voices(language="en_US")} == {
        v for v, m in tts_kokoro.VOICES.items() if m["accent"] == "a"}
    assert [r["id"] for r in va.list_available_voices(installed_only=True)] == ["bm_george"]


def test_download_voice_fetches_that_voice_and_says_so(monkeypatch):
    asked = []
    monkeypatch.setattr(va, "ensure_kokoro", lambda voices=None, dest=None: asked.append(voices)
                        or {"ok": True, "asset": "kokoro", "already_present": False, "fetched": ["voices/bf_emma.bin"]})
    res = va.download_voice("kokoro:bf_emma")
    assert res["ok"] and res["voice"] == "bf_emma" and asked == [["bf_emma"]]
    assert va.download_voice("en_US-amy-medium")["ok"] is False, "a voice of the earlier engine is not fetched"


@pytest.mark.parametrize("query,voice,kind", [
    ("use the alan voice", "bm_george", "name"),          # a name of the earlier engine's voices
    ("switch your voice to amy", "af_heart", "name"),
    ("install en_GB-alan-medium", "bm_george", "id"),
    ("use the emma voice", "bf_emma", "name"),
    ("a british male voice", "bm_fable", "accent"),
    ("a us voice", "af_heart", "accent"),
    ("use the calm voice", "char:calm", "character"),
    ("use the natural voice", "natural:sophia", "natural"),
    ("use a funny voice", "", "none"),
    ("add a french voice", "", "none"),
])
def test_a_spoken_voice_request_resolves_to_a_voice(monkeypatch, query, voice, kind):
    monkeypatch.setattr(va, "kokoro_voice_file", lambda vid: None)
    got = va.resolve_voice_query(query)
    assert (got["voice"], got["kind"]) == (voice, kind)


# ── style presets on Kokoro voices ──────────────────────────────────────────
def test_every_style_preset_is_built_on_a_kokoro_voice():
    from eli.perception import voice_fx
    for c in voice_fx.list_characters():
        spec = voice_fx.get_preset(c["name"])
        assert tts_kokoro.voice_id(spec["base"]), c["name"]
        assert all(tts_kokoro.voice_id(v) for v in spec["fallback"]), c["name"]


def test_a_style_base_walks_its_chain_and_never_picks_the_wrong_gender():
    from eli.perception import voice_fx
    installed = {"af_heart", "am_michael"}
    for char in ("calm", "robotic", "energetic", "refined"):
        assert voice_fx.resolve_base_voice(voice_fx.get_preset(char), installed) == "am_michael", char
    assert voice_fx.resolve_base_voice(voice_fx.get_preset("synthetic"), installed) == "af_heart"
    assert voice_fx.resolve_base_voice(voice_fx.get_preset("calm"), set(), default="af_heart") == "af_heart"


def test_a_preset_saved_with_a_voice_of_the_earlier_engine_uses_its_kokoro_voice():
    from eli.perception import voice_fx
    spec = {"base": "en_GB-alan-medium", "fallback": "en_US-amy-medium"}
    assert voice_fx.resolve_base_voice(spec, {"bm_george", "af_heart"}) == "bm_george"
    assert voice_fx.resolve_base_voice({"base": "missing", "fallback": "en_US-amy-medium"}, {"af_heart"}) == "af_heart"


def test_voice_display_name_labels_styles_and_kokoro_voices():
    from eli.perception.tts_router import voice_display_name, resolve_voice_id
    assert voice_display_name("char:robotic") == "Style: Robotic assistant"
    assert voice_display_name("bm_george") == "George (UK male) [bm_george]"
    assert resolve_voice_id("Style: Robotic assistant") == "char:robotic"
    assert resolve_voice_id("kokoro:bf_emma") == "bf_emma"
