"""Locks on the 2.1.83 live session (13:33–13:37) — an attached image, twice broken.

**1. One stray ']' broke every attached image.** The GUI writes the marker

    [Attached image: shot.png — path: /home/u/Pictures/shot.png]

and the router's "everything from the first slash to end of string" extractor swept
up the closing bracket. That single character did two things: the path did not exist
(`Path not found: …13-36-09.png]`) and `endswith(".png")` was False, so an attached
IMAGE routed to SUMMARIZE_FILE at 0.92 instead of ANALYZE_IMAGE.

**2. Analysing an image unloaded the chat model and could not put it back.** The GUI
loads the GGUF itself and hands it over by assigning `gguf_inference._llm` and
publishing a live runtime override — it never writes the settings keys
`get_model_path()` reads. So the path was in the process the whole time and the
lookup could not see it:

    [GGUF][ADAPTIVE] load attempt 1..8 … error=No GGUF model path configured
    [VISION] text-model restore attempt 1 failed: No GGUF model path configured
    [ANALYZE_IMAGE] fusion skipped: No GGUF model path configured

24 failed load attempts, 27.3 seconds, and — because `force_reload=True` nulled
`_llm` BEFORE checking the path — a working model destroyed by a reload that could
never have succeeded. The visible reply degraded to a raw caption plus an OCR dump,
because fusion is the step that needs the text model.
"""
import os

import pytest

from eli.execution.router_enhanced import route, _attachment_marker_path
import eli.cognition.gguf_inference as gi


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "Screenshot from 2026-08-15 13-36-09.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


# ── 1. the attachment marker ────────────────────────────────────────────────
def test_marker_path_excludes_the_closing_bracket(image):
    msg = f"analyse [Attached image: {image.name} — path: {image}]"
    assert _attachment_marker_path(msg) == str(image)


def test_attached_image_routes_to_analyze_image_with_a_clean_path(image):
    r = route(f"analyse [Attached image: {image.name} — path: {image}]")
    assert r["action"] == "ANALYZE_IMAGE", "an attached image must not become a file summary"
    assert r["args"]["path"] == str(image)
    assert not r["args"]["path"].endswith("]")


def test_marker_survives_a_path_containing_spaces_and_dashes(tmp_path):
    p = tmp_path / "a folder with spaces" / "shot - final (2).png"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    r = route(f"analyse [Attached image: {p.name} — path: {p}]")
    assert r["action"] == "ANALYZE_IMAGE"
    assert r["args"]["path"] == str(p)


def test_no_marker_is_not_invented():
    assert _attachment_marker_path("analyse the latest screenshot") is None
    assert _attachment_marker_path("") is None


def test_a_real_path_ending_in_a_bracket_char_is_left_alone(tmp_path):
    """The delimiter trim must only fire when it helps — 'paper (2).pdf' is a file."""
    p = tmp_path / "paper (2).pdf"
    p.write_text("x", encoding="utf-8")
    r = route(f"analyse {p}")
    assert r["args"]["path"] == str(p)


def test_trailing_chatter_is_still_dropped(tmp_path):
    p = tmp_path / "plain.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    r = route(f"summarise {p} and tell me about it")
    assert r["action"] == "ANALYZE_IMAGE"
    assert r["args"]["path"] == str(p)


# ── 2. the model path the process is already running ────────────────────────
def _resolve():
    """Call get_model_path() as the app does: the fixture below turns test mode (ELI_TEST_MODE, the
    one switch) off, or this whole section would test the short-circuit."""
    return gi.get_model_path()


@pytest.fixture
def no_configured_model(monkeypatch, tmp_path):
    """A GUI-loaded install: nothing in settings, env or config points at a model."""
    for var in ("ELI_GGUF_MODEL_PATH", "ELI_GGUF_MODEL", "ELI_MODELS_DIR", "ELI_TEST_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(gi, "_load_runtime_settings", lambda *a, **k: {})

    import eli.core.config as cfg
    import eli.core.paths as paths_mod
    monkeypatch.setattr(cfg, "get", lambda *a, **k: None)

    class _NoModelPaths:
        model = None
        artifacts_dir = tmp_path / "artifacts"
    monkeypatch.setattr(paths_mod, "PATHS", _NoModelPaths())
    monkeypatch.setattr(paths_mod, "get_paths", lambda: _NoModelPaths())

    gi.set_live_runtime_override(None)
    yield
    gi.set_live_runtime_override(None)


def test_without_the_override_there_is_genuinely_nothing(no_configured_model):
    """Establishes the baseline, so the next test cannot pass by accident."""
    assert _resolve() is None


def test_the_running_model_is_found_through_the_live_override(no_configured_model, image):
    gi.set_live_runtime_override({"provider": "gguf", "loaded": True,
                                  "model_path": str(image)})
    assert _resolve() == image


def test_the_snapshot_on_disk_also_recovers_the_path(no_configured_model, image, tmp_path):
    """The in-memory override does not survive a module reload; the snapshot does."""
    import json
    from eli.core.paths import get_paths
    artifacts = get_paths().artifacts_dir
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "runtime_snapshot.json").write_text(
        json.dumps({"model_path": str(image), "loaded": True}), encoding="utf-8")
    assert _resolve() == image


def test_explicit_configuration_still_wins_over_whatever_is_loaded(
        no_configured_model, image, tmp_path):
    """The fallback is last on purpose — it must never shadow a configured model."""
    explicit = tmp_path / "explicitly-configured.gguf"
    explicit.write_bytes(b"GGUF")
    os.environ["ELI_GGUF_MODEL_PATH"] = str(explicit)
    try:
        gi.set_live_runtime_override({"model_path": str(image)})
        assert _resolve() == explicit
    finally:
        os.environ.pop("ELI_GGUF_MODEL_PATH", None)


def test_a_failed_reload_does_not_destroy_a_loaded_model(no_configured_model, monkeypatch):
    """force_reload used to null _llm and THEN discover it had no path to reload
    from, so a reload that could never succeed still took the model down."""
    sentinel = object()
    monkeypatch.setattr(gi, "_llm", sentinel, raising=False)
    with pytest.raises(FileNotFoundError):
        gi.load_model(force_reload=True)
    assert gi._llm is sentinel, "a working model was destroyed by a reload that could not run"


def _loaded(monkeypatch, path):
    """A model loaded in this process from `path`, as _load_model_now leaves it."""
    monkeypatch.setattr(gi, "_llm", object(), raising=False)
    monkeypatch.setattr(gi, "_ELI_EFFECTIVE_RUNTIME_REPORT", {"model_path": str(path)}, raising=False)


def test_a_missing_configured_path_still_answers_with_the_model_that_is_loaded(
        no_configured_model, image, tmp_path, monkeypatch):
    """ELI_GGUF_MODEL_PATH named a file in a folder the model is not in, the model was loaded from
    where it is kept, and every 'which model' question had None for the whole session."""
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", str(tmp_path / "elsewhere" / "gone.gguf"))
    _loaded(monkeypatch, image)
    assert _resolve() == image
    assert gi.loaded_model_path() == image


def test_a_missing_configured_path_is_said_once_not_on_every_lookup(
        no_configured_model, image, tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", str(tmp_path / "said-once.gguf"))
    _loaded(monkeypatch, image)
    with caplog.at_level(logging.WARNING, logger=gi.log.name):
        for _ in range(5):
            _resolve()
    said = [r for r in caplog.records if "said-once.gguf" in r.getMessage()]
    assert len(said) == 1


def test_with_nothing_loaded_a_missing_configured_path_is_no_model(no_configured_model, tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", str(tmp_path / "gone.gguf"))
    monkeypatch.setattr(gi, "_llm", None, raising=False)
    assert _resolve() is None


def test_a_load_of_a_missing_configured_model_fails_and_does_not_reload_the_old_one(
        no_configured_model, image, tmp_path, monkeypatch):
    """A swap to a file that is not there must fail with its name, not reload what is loaded and
    call that done."""
    missing = tmp_path / "chosen-but-gone.gguf"
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", str(missing))
    _loaded(monkeypatch, image)
    with pytest.raises(FileNotFoundError, match="chosen-but-gone.gguf"):
        gi._load_model_now()


def test_an_attached_image_is_looked_at_whatever_verb_leads_the_message(image, monkeypatch):
    """'This is the screenshot you took, analyse it' with the marker after it went to chat: only a
    leading document verb reached the analysis, and the resolver that also reads the marker was
    behind the model-trust gate, so nothing looked at the image."""
    from eli.cognition import action_trust
    from eli.kernel.engine import CognitiveEngine
    monkeypatch.setattr(action_trust, "may_choose_actions", lambda: False)
    eng = CognitiveEngine.__new__(CognitiveEngine)
    msg = f"This is the screenshot you took earlier, analyse it [Attached image: {image.name} — path: {image}]"
    intent = eng._parse_intent(msg, [])
    assert intent["action"] == "ANALYZE_IMAGE" and intent["args"]["path"] == str(image)
    assert intent["args"]["instruction"] == "This is the screenshot you took earlier, analyse it"


def test_the_latest_screenshot_is_the_capture_reader_s(monkeypatch, tmp_path):
    """One owner of "the latest screenshot" (os_controller.latest_screenshot); the router kept its own scan."""
    import eli.perception.os_controller as oc
    from eli.execution.router_enhanced import route
    shot = tmp_path / "Screenshot from today.png"
    shot.write_bytes(b"\x89PNG")
    monkeypatch.setattr(oc, "latest_screenshot", lambda *a, **k: str(shot), raising=False)
    got = route("analyse the recent screenshot")
    assert got["action"] == "ANALYZE_IMAGE" and got["args"]["path"] == str(shot)


def test_the_intent_model_does_not_take_an_attached_image_marker_as_a_request():
    """The engine sends "[Attached image: ... path: ...]" to image analysis itself; the intent model's
    plain-statement test had a branch of its own for the marker."""
    from eli.cognition.llm_intent import is_plain_statement
    assert is_plain_statement("love this one [Attached image: shot.png — path: /home/u/Pictures/shot.png]")


def test_a_pdf_named_alone_is_found_in_the_users_own_folders_and_nowhere_else(tmp_path, monkeypatch):
    """The router named two folders of one machine's owner as places to look, and searched the whole home
    folder and the working folder while routing a message. A bare PDF name is looked for in the working
    folder, home, the user's Desktop, Documents and Downloads (with their subfolders) and ELI's documents
    folder, and no deeper elsewhere; the words before it ("summarise ...") are not part of its name."""
    from eli.execution.routing._helpers import _extract_pdf_paths
    home = tmp_path / "home"
    nested = home / "Desktop" / "Projects" / "Reading"
    nested.mkdir(parents=True)
    (nested / "orbit_paper.pdf").write_bytes(b"%PDF-1.4\n")
    elsewhere = home / "work" / "archive"
    elsewhere.mkdir(parents=True)
    (elsewhere / "harbour_notes.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    assert _extract_pdf_paths("summarise orbit_paper.pdf") == [str((nested / "orbit_paper.pdf").resolve())]
    assert _extract_pdf_paths("summarise harbour_notes.pdf") == []
