"""Locks on ELI telling the truth about which voice is actually speaking. Ported from v2.

v3 differs deliberately: its preferred clone backend is Chatterbox (MIT,
commercial-clean) with XTTS-v2 (non-commercial model) as the legacy fallback, so the
AppImage bundles `chatterbox`, NOT `natural`. v3 also has no create-voice drop-zone UI,
so the dialog tests do not apply here.

From a live session (v2.1.64): the user dropped in a voice clip and asked whether it
would be used. Settings reported ``Active voice: natural:sophia``, the diagnostics
listed ``Active model file: cs_CZ-jirka-medium.onnx``, and the log showed
``[TTS_FINAL_PIPER_ONLY] voice=en_US-amy-medium`` — three different answers to "what
voice is this?", none of which told the user the neural voice was not running at all.

Three defects, all fixed here:

1. **The fallback was silent.** ``synthesize_wav`` tried XTTS, got ``None`` when the
   neural engine was absent, and fell through to Piper with no log line. Nothing
   anywhere said the selected voice was not being used.
2. **The diagnostics lied by omission.** ``active_model`` came from
   ``find_voice_model(active)``, which cannot resolve a ``natural:``/``clone:`` id and
   returned its fallback — the alphabetically first installed voice, hence the Czech
   one — while synthesis used ``_DEFAULT_VOICE``.
3. (v2-only) The create-voice dialog's remediation was impossible; On failure the create-voice dialog said
   ``pip install -e ".[natural]"  (from the ELI project root)``. In an AppImage there
   is no project root and no writable site-packages.
"""
import re
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
    """The bug: synthesize_wav fell through to Piper with no signal at all."""
    src = Path(REPO / "eli" / "perception" / "tts_router.py").read_text(encoding="utf-8")
    j = src.index('if str(active).startswith("clone:"):')
    block = src[j:src.index("active = _DEFAULT_VOICE", j)]
    assert "_note_neural_fallback" in block, "clone: path still falls back silently"


# ── 2. the diagnostics agree with reality ───────────────────────────────────
def test_backends_expose_what_is_really_used():
    b = tts_router.available_backends()
    for key in ("active_voice", "default_voice", "active_model", "chatterbox", "xtts"):
        assert key in b, key


def test_neural_voice_does_not_report_an_unrelated_model_file():
    """The Czech-voice symptom: a natural: id resolved to the alphabetically first
    installed Piper model, which is neither selected nor spoken."""
    src = Path(REPO / "eli" / "perception" / "tts_router.py").read_text(encoding="utf-8")
    i = src.index("def available_backends()")
    block = src[i:i + 1200]
    assert "_DEFAULT_VOICE if _is_neural else active" in block, (
        "available_backends still resolves the model from the neural id"
    )


def test_clone_backends_are_real_probes():
    """v3 reports each clone backend separately: Chatterbox (preferred) and XTTS."""
    b = tts_router.available_backends()
    assert isinstance(b["chatterbox"], bool)
    assert isinstance(b["xtts"], bool)
# ── 4. the AppImage actually bundles the engine ─────────────────────────────
def _release_yaml() -> dict:
    import yaml
    return yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())


def test_linux_bundles_the_neural_extra():
    d = _release_yaml()
    extras = (d["jobs"]["build-linux"].get("env") or {}).get(
        "OPTIONAL_EXTRAS", d["env"]["OPTIONAL_EXTRAS"])
    assert "chatterbox" in extras.split(), "the AppImage no longer bundles the clone engine"


@pytest.mark.parametrize("job", ["build-windows", "build-macos"])
def test_other_platforms_do_not_pull_the_torch_stack(job):
    """Only the linux job installs CPU torch first and guards against CUDA wheels.
    Adding `natural` globally makes windows/macos resolve torch from PyPI and blows
    the 2 GiB per-asset limit — which fails the whole release, not just one job."""
    d = _release_yaml()
    extras = (d["jobs"][job].get("env") or {}).get(
        "OPTIONAL_EXTRAS", d["env"]["OPTIONAL_EXTRAS"])
    assert "chatterbox" not in extras.split(), f"{job} would pull the CUDA torch stack"


def test_linux_installs_the_cpu_torch_stack_before_the_extras_loop():
    d = _release_yaml()
    step = next(s for s in d["jobs"]["build-linux"]["steps"]
                if "Install dependencies" in str(s.get("name", "")))
    run = step["run"]
    cpu_at = run.index("download.pytorch.org/whl/cpu")
    loop_at = run.index("for extra in $OPTIONAL_EXTRAS")
    assert cpu_at < loop_at, "extras resolve torch before the CPU index is used"
    assert "torchaudio" in run[:cpu_at + 200], "torchaudio must come from the CPU index too"


def test_a_cuda_regression_fails_the_linux_build():
    d = _release_yaml()
    step = next(s for s in d["jobs"]["build-linux"]["steps"]
                if "Install dependencies" in str(s.get("name", "")))
    assert "nvidia-" in step["run"], "no guard against CUDA wheels re-entering the bundle"


def test_the_two_gib_asset_guard_is_still_in_place():
    """Bundling the engine only stays safe while this guard exists."""
    raw = (REPO / ".github" / "workflows" / "release.yml").read_text()
    assert "2147483648" in raw, "the GitHub 2 GiB asset-size guard is gone"
