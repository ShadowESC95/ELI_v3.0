"""Locks on ELI telling the truth about which voice is actually speaking. Ported from v2.

v3's natural and cloned voices are Qwen3-TTS's (Apache-2.0) on a GPU with room for it
beside the language model (eli/perception/tts_qwen3.py), then Chatterbox (MIT), with
XTTS-v2 (non-commercial model) as the legacy fallback; the AppImage bundles neither
Qwen3-TTS nor `natural`. Where none can speak, the Kokoro default voice speaks and the
fallback state says so.

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
    for key in ("engine", "active_voice", "speaking_voice", "default_voice", "active_model",
                "qwen3", "chatterbox", "xtts"):
        assert key in b, key


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
    monkeypatch.setattr(tts_qwen3, "_cuda_works", lambda: True)
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
    assert qwen.choose("custom") is None and qwen.list_natural_voices() == []


def test_the_need_is_what_the_model_was_measured_holding_once_it_has_been(qwen, monkeypatch):
    from eli.perception import gpu_room
    big = qwen.MODELS["custom"][0]
    _cards(monkeypatch, [big["bytes"] / 1_048_576 + 1])
    monkeypatch.setattr(gpu_room, "measured_footprint_mb",
                        lambda table, card, model: 10 ** 6 if model == big["repo"] else None)
    entry, _device = qwen.choose("custom")
    assert entry["repo"].endswith("0.6B-CustomVoice"), "the measured footprint did not count"


def test_a_cpu_only_machine_never_loads_it(qwen, monkeypatch):
    monkeypatch.setattr(qwen, "installed", lambda: False)
    assert qwen.choose("base") is None
    qwen._reset_for_tests()
    monkeypatch.setattr(qwen, "installed", lambda: True)
    _cards(monkeypatch, [10 ** 6])
    monkeypatch.setattr(qwen, "_cuda_works", lambda: False)
    assert qwen.choose("base") is None, "PyTorch without CUDA"
    qwen._reset_for_tests()
    monkeypatch.setattr(qwen, "_cuda_works", lambda: True)
    monkeypatch.setenv("ELI_QWEN3_TTS", "0")
    assert qwen.choose("base") is None, "switched off"


def test_the_choice_is_made_once_a_run(qwen, monkeypatch):
    rooms = [10 ** 6]
    _cards(monkeypatch, rooms)
    first = qwen.choose("custom")
    rooms[0] = 0.0                                # its own weights now fill the card
    assert qwen.choose("custom") == first


class _Model:
    def __init__(self):
        self.calls = []
        self.model = type("M", (), {"get_supported_languages": staticmethod(lambda: ["english", "auto"])})()

    def generate_custom_voice(self, **kw):
        self.calls.append(("custom", kw))
        return [[0.1] * 2400], 24000

    def generate_voice_clone(self, **kw):
        self.calls.append(("clone", kw))
        return [[0.1] * 2400], 24000


def test_a_natural_voice_is_a_speaker_steered_by_the_tone(qwen, monkeypatch):
    model = _Model()
    monkeypatch.setattr(qwen, "_model", lambda kind: model)
    monkeypatch.setattr(qwen, "_record_footprint", lambda kind: None)
    wav = qwen.synthesize_natural_wav("Hello.", "natural:sophia", instruct="Speak in a sad tone.")
    assert wav and model.calls == [("custom", {"text": "Hello.", "speaker": "Serena", "language": "auto",
                                               "instruct": "Speak in a sad tone."})]


def test_a_clone_without_its_transcript_takes_only_the_speakers_character(qwen, monkeypatch, tmp_path):
    model = _Model()
    monkeypatch.setattr(qwen, "_model", lambda kind: model)
    monkeypatch.setattr(qwen, "_record_footprint", lambda kind: None)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    assert qwen.synthesize_clone_wav("Hi.", str(ref))
    assert qwen.synthesize_clone_wav("Hi.", str(ref), ref_text="What the clip says.")
    (_, a), (_, b) = model.calls
    assert a["x_vector_only_mode"] is True and a["ref_text"] is None
    assert b["x_vector_only_mode"] is False and b["ref_text"] == "What the clip says."
    assert qwen.synthesize_clone_wav("Hi.", str(tmp_path / "missing.wav")) is None


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
