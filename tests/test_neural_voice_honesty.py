"""Locks on ELI telling the truth about which voice is actually speaking. Ported from v2.

v3's natural and cloned voices are Qwen3-TTS's (Apache-2.0) on a GPU with room for it
beside the language model (eli/perception/tts_qwen3.py); Chatterbox (pykakasi, GPL) and
XTTS-v2 (non-commercial model) are engines no more, and the AppImage bundles no neural
engine. Where Qwen3-TTS cannot speak, the Kokoro default voice speaks and the fallback
state says so.

From a live session (v2.1.64): the user dropped in a voice clip and asked whether it
would be used. Settings reported ``Active voice: natural:sophia``, the diagnostics
listed ``Active model file: cs_CZ-jirka-medium.onnx``, and the log showed
``[TTS_FINAL_PIPER_ONLY] voice=en_US-amy-medium`` (the engine then) — three different answers to "what
voice is this?", none of which told the user the neural voice was not running at all.

Three defects, all fixed here:

1. **The fallback was silent.** ``synthesize_wav`` tried XTTS, got ``None`` when the
   neural engine was absent, and fell through to the default voice with no log line. Nothing
   anywhere said the selected voice was not being used.
2. **The diagnostics lied by omission.** ``active_model`` came from
   ``find_voice_model(active)``, which cannot resolve a ``natural:``/``clone:`` id and
   returned its fallback — the alphabetically first installed voice, hence the Czech
   one — while synthesis used ``_DEFAULT_VOICE``.
3. (v2-only) The create-voice dialog's remediation was impossible; On failure the create-voice dialog said
   ``pip install -e ".[natural]"  (from the ELI project root)``. In an AppImage there
   is no project root and no writable site-packages.
"""
from pathlib import Path

import pytest

from eli.perception import tts_router

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_fallback_state():
    tts_router._note_neural_fallback(None)
    yield
    tts_router._note_neural_fallback(None)


# ── 1. the fallback is observable ───────────────────────────────────────────
def test_fallback_state_starts_clean():
    assert tts_router.neural_fallback_state()["active"] is False


def test_a_neural_fallback_is_recorded_with_the_requested_voice():
    tts_router._note_neural_fallback("natural:sophia")
    st = tts_router.neural_fallback_state()
    assert st["active"] is True
    assert st["requested"] == "natural:sophia"


def test_a_fallback_always_carries_a_reason():
    """"It fell back" without why is what made this invisible for so long."""
    tts_router._note_neural_fallback("clone:alex")
    assert tts_router.neural_fallback_state()["reason"].strip()


def test_recovery_clears_the_state():
    tts_router._note_neural_fallback("natural:sophia")
    tts_router._note_neural_fallback(None)
    assert tts_router.neural_fallback_state()["active"] is False


def test_state_is_a_copy_not_the_live_dict():
    """A caller mutating the diagnostics must not corrupt the router's state."""
    tts_router._note_neural_fallback("natural:sophia")
    tts_router.neural_fallback_state()["requested"] = "tampered"
    assert tts_router.neural_fallback_state()["requested"] == "natural:sophia"


def test_the_synth_path_reports_the_fallback():
    """The bug: synthesize_wav fell through to the default voice with no signal at all."""
    src = Path(REPO / "eli" / "perception" / "tts_router.py").read_text(encoding="utf-8")
    j = src.index('if str(active).startswith("clone:"):')
    block = src[j:src.index("active = _DEFAULT_VOICE", j)]
    assert "_note_neural_fallback" in block, "clone: path still falls back silently"


# ── 2. the diagnostics agree with reality ───────────────────────────────────
def test_backends_expose_what_is_really_used():
    b = tts_router.available_backends()
    for key in ("engine", "active_voice", "speaking_voice", "default_voice", "active_model", "qwen3"):
        assert key in b, key
    assert "chatterbox" not in b and "xtts" not in b


def test_a_neural_voice_is_reported_with_the_engine_that_speaks_it(monkeypatch):
    """The Czech-voice symptom: a natural: id was reported with an unrelated model file. Now the
    report names the engine: Qwen3-TTS where it runs, else 'neural' (not usable here)."""
    from eli.perception import tts_qwen3
    monkeypatch.setenv("ELI_TTS_VOICE", "natural:sophia")
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": False)
    assert tts_router.available_backends()["engine"] == "neural"
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": kind == "custom")
    assert tts_router.available_backends()["engine"] == "qwen3"


# ── 3. Qwen3-TTS only where a GPU has room for it ───────────────────────────
@pytest.fixture
def qwen(monkeypatch):
    from eli.perception import gpu_room, tts_qwen3
    tts_qwen3._reset_for_tests()
    monkeypatch.setattr(tts_qwen3, "installed", lambda: True)
    monkeypatch.delenv("ELI_QWEN3_TTS", raising=False)
    monkeypatch.delenv("ELI_QWEN3_TTS_DEVICE", raising=False)
    monkeypatch.setattr(gpu_room, "measured_footprint_mb", lambda table, card, model: None)
    yield tts_qwen3
    tts_qwen3._reset_for_tests()


def _cards(monkeypatch, rooms):
    """NVIDIA cards and the room each has after the language model's plan (None: unknown)."""
    from eli.perception import gpu_room
    cards = [{"name": f"Card {i}", "total_mb": 24576, "free_mb": 1, "vendor": "nvidia"} for i in range(len(rooms))]
    monkeypatch.setattr(gpu_room, "nvidia_cards", lambda: cards)
    monkeypatch.setattr(gpu_room, "room_mb", lambda card, position=0: rooms[position])
    return cards


def test_the_largest_model_a_card_has_room_for_is_taken_on_that_card(qwen, monkeypatch):
    big, small = (m["bytes"] / 1_048_576 for m in qwen.MODELS["custom"])
    _cards(monkeypatch, [small + 1, big + 1])
    entry, device = qwen.choose("custom")
    assert entry["repo"].endswith("1.7B-CustomVoice") and device == "cuda:1"
    qwen._reset_for_tests()
    _cards(monkeypatch, [small + 1, small - 1])
    entry, device = qwen.choose("custom")
    assert entry["repo"].endswith("0.6B-CustomVoice") and device == "cuda:0"


@pytest.mark.parametrize("rooms", [[], [None], [10.0]])
def test_no_room_or_no_way_to_tell_keeps_the_gpu_for_the_language_model(qwen, monkeypatch, rooms):
    _cards(monkeypatch, rooms)
    assert qwen.room_for("custom") is None and qwen.choose("custom") is None
    assert qwen.list_natural_voices() == []


def test_the_need_is_what_the_model_was_measured_holding_once_it_has_been(qwen, monkeypatch):
    from eli.perception import gpu_room
    big = qwen.MODELS["custom"][0]
    _cards(monkeypatch, [big["bytes"] / 1_048_576 + 1])
    monkeypatch.setattr(gpu_room, "measured_footprint_mb",
                        lambda table, card, model: 10 ** 6 if model == big["repo"] else None)
    entry, _device = qwen.choose("custom")
    assert entry["repo"].endswith("0.6B-CustomVoice"), "the measured footprint did not count"


def test_room_without_its_runtime_is_not_yet_a_voice_and_a_cpu_machine_never_has_one(qwen, monkeypatch):
    _cards(monkeypatch, [10 ** 6])
    monkeypatch.setattr(qwen, "installed", lambda: False)
    assert qwen.room_for("base") is not None and qwen.choose("base") is None, "used before its runtime is made"
    monkeypatch.setattr(qwen, "installed", lambda: True)
    assert qwen.choose("base") is not None, "asked again once the runtime is made"
    qwen._reset_for_tests()
    _cards(monkeypatch, [])
    assert qwen.room_for("base") is None, "no NVIDIA card"
    _cards(monkeypatch, [10 ** 6])
    monkeypatch.setenv("ELI_QWEN3_TTS", "0")
    assert qwen.room_for("base") is None, "switched off"


def test_the_choice_is_made_once_a_run(qwen, monkeypatch):
    rooms = [10 ** 6]
    _cards(monkeypatch, rooms)
    first = qwen.choose("custom")
    rooms[0] = 0.0                                # its own weights now fill the card
    assert qwen.choose("custom") == first


class _FakeWorker:
    def __init__(self):
        self.asked = []

    def ask(self, req):
        import wave
        self.asked.append({k: v for k, v in req.items() if k != "out"})
        with wave.open(req["out"], "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x01\x00" * 2400)
        return {"ok": True, "seconds": 0.1, "rate": 24000, "reserved_mb": 3210.0}


def test_a_natural_voice_is_a_speaker_steered_by_the_tone(qwen, monkeypatch):
    from eli.perception import gpu_room
    worker = _FakeWorker()
    _cards(monkeypatch, [10 ** 6])
    monkeypatch.setattr(qwen, "_model", lambda kind: worker)
    recorded = []
    monkeypatch.setattr(gpu_room, "record_footprint", lambda table, card, model, mb: recorded.append((model, mb)))
    qwen.choose("custom")
    wav = qwen.synthesize_natural_wav("Hello.", "natural:sophia", instruct="Speak in a sad tone.", language="english")
    assert wav and worker.asked == [{"op": "custom", "text": "Hello.", "speaker": "Serena", "language": "english",
                                     "instruct": "Speak in a sad tone."}]
    assert recorded and recorded[0][1] == 3210.0, "the first synthesis did not record the footprint"


def test_a_clone_is_spoken_from_its_recording_and_transcript(qwen, monkeypatch, tmp_path):
    worker = _FakeWorker()
    monkeypatch.setattr(qwen, "_model", lambda kind: worker)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    assert qwen.synthesize_clone_wav("Hi.", str(ref), ref_text="What the clip says.")
    assert worker.asked[0]["op"] == "clone" and worker.asked[0]["ref_text"] == "What the clip says."
    assert qwen.synthesize_clone_wav("Hi.", str(tmp_path / "missing.wav")) is None


def test_the_worker_speaks_json_lines_from_a_runtime_of_its_own(qwen, monkeypatch, tmp_path):
    """ELI starts the runtime's Python on qwen3_worker.py and talks to it one JSON line at a time; here
    a stand-in worker on this Python answers, and what it printed on stdout first does not get in."""
    import sys
    from eli.perception import qwen3_runtime
    fake = tmp_path / "worker.py"
    fake.write_text(
        "import json, sys, wave\n"
        "sys.path.insert(0, %r)\n"
        "from eli.perception.qwen3_worker import _protocol_stream\n"
        "out = _protocol_stream()\n"
        "print('a library notice on stdout')\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req['op'] == 'load':\n"
        "        out.write(json.dumps({'ok': True}) + '\\n'); continue\n"
        "    w = wave.open(req['out'], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)\n"
        "    w.writeframes(b'\\x01\\x00' * 2400); w.close()\n"
        "    out.write(json.dumps({'ok': True, 'seconds': 0.1}) + '\\n')\n" % str(REPO), encoding="utf-8")
    monkeypatch.setattr(qwen3_runtime, "python_path", lambda root=None: Path(sys.executable))
    monkeypatch.setattr(qwen3_runtime, "WORKER", fake)
    monkeypatch.setattr(qwen3_runtime, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(qwen, "_local_snapshot", lambda entry: tmp_path)
    monkeypatch.setenv("ELI_QWEN3_TTS_DEVICE", "cpu")
    try:
        wav = qwen.synthesize_natural_wav("Hello.", "natural:aiden")
        assert wav and wav[:4] == b"RIFF", qwen.last_error()
    finally:
        if qwen._WORKER is not None:
            qwen._WORKER.proc.kill()
            qwen._WORKER.proc.wait()


def test_a_cloned_voice_is_spoken_by_qwen3_first_with_the_registry_recording(monkeypatch, tmp_path):
    from eli.perception import tts_qwen3, tts_xtts
    monkeypatch.setattr(tts_xtts, "get_clone", lambda name: {"ref_wav": str(tmp_path / "r.wav"), "ref_text": "hi"})
    asked = []
    monkeypatch.setattr(tts_qwen3, "synthesize_clone_wav",
                        lambda text, ref, ref_text="": asked.append((ref, ref_text)) or b"RIFF-wav")
    monkeypatch.setattr(tts_router, "_apply_tone_pitch", lambda wav: wav)
    assert tts_router.synthesize_wav("Say this.", "clone:alex") == b"RIFF-wav"
    assert asked == [(str(tmp_path / "r.wav"), "hi")]
    assert tts_router.neural_fallback_state()["active"] is False


# ── 4. the AppImage actually bundles the engine ─────────────────────────────
def _release_yaml() -> dict:
    import yaml
    return yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())


def test_linux_bundles_the_neural_extra():
    d = _release_yaml()
    for job in ("build-linux", "build-windows", "build-macos"):
        extras = (d["jobs"][job].get("env") or {}).get(
            "OPTIONAL_EXTRAS", d["env"]["OPTIONAL_EXTRAS"])
        assert "natural" not in extras.split(), f"{job} bundles the neural engine again"
        assert "chatterbox" not in extras.split(), f"{job} bundles Chatterbox, which brings pykakasi (GPL)"


@pytest.mark.parametrize("job", ["build-windows", "build-macos"])
def test_other_platforms_do_not_pull_the_torch_stack(job):  # noqa: D401
    """Only the linux job installs CPU torch first and guards against CUDA wheels.
    Adding `natural` globally makes windows/macos resolve torch from PyPI and blows
    the 2 GiB per-asset limit — which fails the whole release, not just one job."""
    d = _release_yaml()
    extras = (d["jobs"][job].get("env") or {}).get(
        "OPTIONAL_EXTRAS", d["env"]["OPTIONAL_EXTRAS"])
    assert "chatterbox" not in extras.split(), f"{job} would pull the CUDA torch stack"


def _linux_deps() -> str:
    """The Linux build's dependency install: the build-linux job's own step."""
    step = next(s for s in _release_yaml()["jobs"]["build-linux"]["steps"]
                if "Install dependencies" in str(s.get("name", "")))
    return step["run"]


def test_linux_installs_the_cpu_torch_stack_before_the_extras_loop():
    run = _linux_deps()
    cpu_at = run.index("download.pytorch.org/whl/cpu")
    loop_at = run.index("for extra in $OPTIONAL_EXTRAS")
    assert cpu_at < loop_at, "extras resolve torch before the CPU index is used"
    assert "torchaudio" in run[:cpu_at + 200], "torchaudio must come from the CPU index too"


def test_a_cuda_regression_fails_the_linux_build():
    assert "nvidia-" in _linux_deps(), "no guard against CUDA wheels re-entering the bundle"


def test_the_two_gib_asset_guard_is_still_in_place():
    """Bundling the engine only stays safe while this guard exists."""
    raw = (REPO / ".github" / "workflows" / "release.yml").read_text()
    assert "2147483648" in raw, "the GitHub 2 GiB asset-size guard is gone"


# ── 5. the runtime Qwen3-TTS runs in ─────────────────────────────────────────
def _tar_with(name: str, body: bytes) -> bytes:
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(f"uv-x/{name}")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _serve(monkeypatch, body: bytes):
    import io
    import sys
    import eli.core

    class _Net:
        asked = []

        def allow_network(self, reason):
            import contextlib
            return contextlib.nullcontext()

        def guarded_urlopen(self, req, timeout=0):
            _Net.asked.append(req.full_url)
            return io.BytesIO(body)

        def should_block_network(self):
            return False
    net = _Net()
    monkeypatch.setattr(eli.core, "netguard", net, raising=False)
    monkeypatch.setitem(sys.modules, "eli.core.netguard", net)
    return net


def test_the_pinned_uv_is_refused_when_its_checksum_does_not_match_and_kept_when_it_does(monkeypatch, tmp_path):
    import hashlib
    from eli.perception import qwen3_runtime as R
    body = _tar_with("uv", b"#!/bin/sh\necho uv\n")
    monkeypatch.setattr(R, "_platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(R, "_windows", lambda: False)
    monkeypatch.setitem(R.UV_ASSETS, "linux-x86_64", ("uv-x86_64-unknown-linux-gnu.tar.gz", "0" * 64))
    _serve(monkeypatch, body)
    with pytest.raises(ValueError, match="checksum"):
        R._fetch_uv(tmp_path)
    assert not (tmp_path / "bin").exists()
    monkeypatch.setitem(R.UV_ASSETS, "linux-x86_64",
                        ("uv-x86_64-unknown-linux-gnu.tar.gz", hashlib.sha256(body).hexdigest()))
    uv = R._fetch_uv(tmp_path)
    assert uv.read_bytes().startswith(b"#!/bin/sh") and uv.stat().st_mode & 0o100
    assert R.UV_VERSION in R._UV_RELEASE and all(len(sha) == 64 for _name, sha in R.UV_ASSETS.values())


def test_the_runtime_is_made_with_its_own_python_cuda_torch_its_pins_and_qwen_without_gradio(monkeypatch, tmp_path):
    from eli.perception import qwen3_runtime as R
    commands = []
    monkeypatch.setattr(R, "_fetch_uv", lambda root: root / "bin" / "uv")
    monkeypatch.setattr(R, "_windows", lambda: False)

    def _uv(uv, root, *args):
        commands.append(args)
        if args[0] == "venv":
            py = R.python_path(root)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")
    monkeypatch.setattr(R, "_uv", _uv)
    monkeypatch.setattr(R, "check", lambda root=None: {"ok": True, "cuda": True, "qwen-tts": "0.1.1"})
    _serve(monkeypatch, b"")
    res = R.make(tmp_path)
    assert res["ok"] and R.ready(tmp_path)["qwen-tts"] == "0.1.1"
    assert commands[0][:3] == ("venv", "--python", R.PYTHON)
    assert ("--torch-backend", "auto") == commands[1][4:6] and commands[1][-2:] == ("torch", "torchaudio")
    assert commands[2][-2:] == ("-r", str(R._REQUIREMENTS)) and commands[3][-2:] == ("--no-deps", R.QWEN_TTS)
    pins = [line.split("#", 1)[0].strip() for line in R._REQUIREMENTS.read_text(encoding="utf-8").splitlines()]
    assert "transformers==4.57.3" in pins and "accelerate==1.12.0" in pins
    assert not any(p.startswith(("gradio", "torch", "qwen")) for p in pins), "pinned twice or brought back"
    assert R.make(tmp_path).get("already") is True, "made again"


def test_a_runtime_that_does_not_see_cuda_is_not_ready(monkeypatch, tmp_path):
    from eli.perception import qwen3_runtime as R
    monkeypatch.setattr(R, "_fetch_uv", lambda root: root / "bin" / "uv")
    monkeypatch.setattr(R, "_uv", lambda uv, root, *args: R.python_path(root).parent.mkdir(parents=True, exist_ok=True)
                        or R.python_path(root).write_text(""))
    monkeypatch.setattr(R, "check", lambda root=None: {"ok": True, "cuda": False})
    _serve(monkeypatch, b"")
    res = R.make(tmp_path)
    assert res["ok"] is False and "CUDA" in res["error"] and R.ready(tmp_path) is None


def test_a_machine_without_room_makes_no_runtime_and_fetches_nothing(monkeypatch, tmp_path):
    from eli.perception import qwen3_runtime as R, tts_qwen3
    monkeypatch.setattr(R, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(tts_qwen3, "room_for", lambda kind: None)
    net = _serve(monkeypatch, b"")
    res = R.make_if_room()
    assert res["ok"] is False and "room" in res["skipped"] and net.asked == []
    assert not (tmp_path / "runtime").exists()


def test_the_worker_keeps_its_answers_apart_from_what_libraries_print(tmp_path):
    import subprocess
    import sys
    code = ("import sys, json; sys.path.insert(0, %r)\n"
            "from eli.perception.qwen3_worker import _protocol_stream\n"
            "out = _protocol_stream()\nprint('noise from a library')\nout.write(json.dumps({'ok': True}) + '\\n')\n"
            % str(REPO))
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert got.stdout.strip() == '{"ok": true}' and "noise from a library" in got.stderr
