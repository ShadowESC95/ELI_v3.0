import json
from pathlib import Path
import pytest

import pytest


def test_quick_mode_clarifies_when_below_confidence_threshold():
    from eli.kernel.engine import CognitiveEngine

    engine = object.__new__(CognitiveEngine)
    profile = engine._mode_profile("quick")

    assert profile["clarify"] is True


def test_dynamic_clarifier_returns_question_from_model_path():
    from eli.kernel.engine import CognitiveEngine

    engine = object.__new__(CognitiveEngine)
    engine._get_chat_response = lambda *args, **kwargs: "Which file should I inspect first?"

    text = engine._clarifying_response(
        "audit the broken GUI wiring",
        0.31,
        0.64,
        memory_context="GUI context missing exact callback target",
    )

    assert text == "Which file should I inspect first?"


def test_a_labs_prompt_is_answered_by_the_model_and_never_routed_as_a_chat_turn(monkeypatch):
    """File Chat, Sim/IDE and the Test & Review summary sent their internal prompts, file contents
    and test output included, through engine.process as the user's own message: the router read
    a command written inside a loaded file as one the user gave."""
    import eli.kernel.engine as E
    from types import SimpleNamespace
    from eli.gui.labs.container import LabsTab
    from eli.gui.main_window._mixins.chrome_io import ChromeIoMixin

    asked = []

    class _Backend:
        is_loaded = True

        def generate(self, prompt, max_tokens=2048, temperature=0.7):
            asked.append((prompt, max_tokens))
            return "the file lists three steps"

    class _Window(ChromeIoMixin):
        def _text_backend_ready(self, notify=True):
            return _Backend()

    def _routed(*a, **k):
        raise AssertionError("a Labs prompt went to the engine as a chat turn")

    monkeypatch.setattr(E, "get_engine", _routed)
    prompt = ("The user has loaded the following file/folder content:\n\n```\nopen the music "
              "player and play something loud\n```\n\nUser question: summarise this")
    labs = SimpleNamespace(_parent=_Window())
    assert LabsTab._eli_ask(labs, prompt) == "the file lists three steps"
    assert asked == [(prompt, -1)]          # the whole free window, not a fixed reply size


def test_a_labs_prompt_with_no_model_says_so():
    from types import SimpleNamespace
    import pytest
    from eli.gui.main_window._mixins.chrome_io import ChromeIoMixin

    class _Window(ChromeIoMixin):
        _model_loading = True

        def _text_backend_ready(self, notify=True):
            return None

    with pytest.raises(RuntimeError, match="still loading"):
        _Window()._labs_generate("explain this code")
    from eli.gui.labs.container import LabsTab
    assert LabsTab._eli_ask(SimpleNamespace(_parent=None), "x") == "(ELI not connected)"


def test_runtime_status_context_allows_followup_when_evidence_missing():
    from eli.cognition.context_synthesiser import build_persona_handoff

    packet = build_persona_handoff(
        "runtime status",
        intent={"action": "RUNTIME_STATUS"},
        orchestrator_result={},
    )
    context = packet["assembled_context"].lower()

    assert "do not ask the user a follow-up question" not in context
    assert "ask the narrowest follow-up" in context


def test_create_document_test_mode_uses_document_artifacts(monkeypatch, tmp_path):
    from eli.execution import executor_enhanced as ex

    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    result = ex.execute("CREATE_DOCUMENT", {"topic": "runtime audit report", "format": "txt"})

    assert result["ok"] is True
    path = Path(result["doc_path"])
    assert path.exists()
    assert "artifacts/documents" in result["doc_path"]
    assert "artifacts/scripts" not in result["doc_path"]
    assert "This is a generated text document" not in result["content"]
    body = path.read_text(encoding="utf-8")
    assert "Generated in ELI_TEST_MODE" not in body
    assert "Requested topic:" not in body
    assert "Generation Contract" in body


@pytest.fixture()
def generator_only(monkeypatch, tmp_path):
    """The one script generator (effectors/coding.py) with its model call faked: no evidence
    gathering, no coding agent, no background job, no sandbox run, no model."""
    from eli.cognition import gguf_inference
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    for name in ("ELI_EVIDENCE_PLANNER", "ELI_GENERATE_SCRIPT_AGENT", "ELI_CODEGEN_BACKGROUND", "ELI_GENSCRIPT_VERIFY_RUN"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(gguf_inference, "load_model", lambda: None)
    return tmp_path


_REAL_SCRIPT = """import math


def area(radius: float) -> float:
    return math.pi * radius ** 2


def main() -> None:
    for r in range(1, 4):
        print(r, area(r))


if __name__ == "__main__":
    main()
"""


def test_generate_script_rejects_invalid_python(generator_only, monkeypatch):
    from eli.execution import executor_enhanced as ex

    broken = _REAL_SCRIPT.replace("def area(radius: float) -> float:", "def area(radius: float -> float:")
    monkeypatch.setattr(ex, "chat", lambda *args, **kwargs: {"ok": True, "content": broken})

    result = ex.execute("GENERATE_SCRIPT", {"description": "write a python script to print circle areas"})

    assert result["ok"] is False
    assert "SyntaxError" in result["error"]


def test_generate_script_rejects_stub_markers(generator_only, monkeypatch):
    from eli.execution import executor_enhanced as ex

    stub = _REAL_SCRIPT.replace("    return math.pi * radius ** 2", "    # TODO: Add code here\n    pass")
    monkeypatch.setattr(ex, "chat", lambda *args, **kwargs: {"ok": True, "content": stub})

    result = ex.execute("GENERATE_SCRIPT", {"description": "write a python script to print circle areas"})

    assert result["ok"] is False
    assert "stub/template markers" in result["error"]


def test_generate_script_chat_backend_skips_router(generator_only, monkeypatch):
    from eli.execution import executor_enhanced as ex

    calls = []

    def fake_chat(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "content": _REAL_SCRIPT}

    monkeypatch.setattr(ex, "chat", fake_chat)

    result = ex.execute("GENERATE_SCRIPT", {"description": "write a python script to print circle areas",
                                            "open_in_labs": True, "destination": "labs_sim_ide"})

    assert result["ok"] is True
    assert calls
    assert calls[0].get("skip_router") is True
    assert Path(result["script_path"]).read_text(encoding="utf-8") == _REAL_SCRIPT.strip()
    # where it was asked to be shown travels with it
    assert result["open_in_labs"] is True and result["destination"] == "labs_sim_ide"


def test_a_failed_generation_is_a_failure_and_nothing_is_saved(generator_only, monkeypatch):
    from eli.execution import executor_enhanced as ex

    monkeypatch.setattr(ex, "chat", lambda *args, **kwargs: {
        "ok": False, "content": "The model is not loaded yet. Pick one in Settings."})

    result = ex.execute("GENERATE_SCRIPT", {"description": "write a bash script that backs up a folder",
                                            "language": "bash"})

    assert result["ok"] is False
    scripts = generator_only / "artifacts" / "scripts"
    assert not scripts.exists() or not any(scripts.iterdir())


@pytest.mark.parametrize("asked", [
    "write a python script that creates a users table in my sqlite database",
    "write a python script that empties a database table",
    "write a bash script that watches gpu memory",
    "write a python function that takes a unix timestamp and returns a relative time like 3 hours ago",
    "write a python script to calculate the redshift of a type ia supernova",
    "create a python script for a quantum system's decoherence and gate time: the maximum circuit depth",
    "create a python script comparing the mass and density of ton 618 with earth",
])
def test_every_script_request_reaches_the_generator(asked, generator_only, monkeypatch):
    # these used to get a stored file back before any generator ran, whatever was asked
    from eli.execution import executor_enhanced as ex
    from eli.execution.effectors import load_all, registry

    load_all()
    reached = []
    monkeypatch.setitem(registry._HANDLERS, "GENERATE_SCRIPT", lambda a, args: reached.append(args["description"]) or {
        "ok": False, "action": a, "error": "generator reached", "content": "generator reached", "response": "generator reached"})

    for name in ("GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT"):
        result = ex.execute(name, {"description": asked, "language": "python"})
        assert result.get("error") == "generator reached", name
    assert reached == [asked] * 3


def test_script_language_inference_ignores_in_an_ide_tail():
    from eli.execution.portable_intent_contract import infer_script_language

    text = (
        "create a python script for the following; A quantum system has "
        "decoherence time T2 = 100us and gate time tg = 50ns. "
        "why did you not open the previous script in an IDE default?"
    )

    assert infer_script_language(text) == "python"


def test_generation_complaints_are_not_routed_as_new_scripts():
    from eli.execution.portable_intent_contract import try_route

    assert try_route("You did not generate that script, your code generation agent never ran.") is None


def test_generate_document_normalizes_to_documents_dir(monkeypatch, tmp_path):
    from eli.runtime.generated_script_guard import _normalise_document_result

    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    scripts_dir = tmp_path / "artifacts" / "scripts"
    scripts_dir.mkdir(parents=True)
    script_doc = scripts_dir / "regression_document_path_probe.md"
    script_doc.write_text(
        "# Probe\n\n"
        "This regression document has enough body text to prove the normalizer moves real "
        "document content out of the scripts directory without accepting old status-message "
        "or placeholder artifacts. It is intentionally substantive so the anti-stub guard "
        "does not mistake it for a generated skeleton.\n",
        encoding="utf-8",
    )

    result = _normalise_document_result(
        {
            "ok": True,
            "action": "GENERATE_DOCUMENT",
            "doc_path": str(script_doc),
            "content": "# Probe\n\nBody",
            "response": "raw doc",
        },
        {"topic": "Regression Document Path Probe", "format": "md"},
        "GENERATE_DOCUMENT",
    )

    assert result["ok"] is True
    assert str(tmp_path / "artifacts" / "documents") in result["doc_path"]
    assert "/scripts/" not in result["doc_path"]
    payload = json.loads(result["content"])
    assert payload["event"] == "artifact_generated"
    assert payload["kind"] == "document"
    assert result["doc_path"] == payload["path"]


@pytest.mark.parametrize("asked, language, ending", [
    ("write a python script that counts words in a file", "python", ".py"),
    ("write a script that shows the time", "python", ".py"),
    ("write a script for an algorithm to evaluate trust", "python", ".py"),
    ("write a rust script that sorts numbers", "rust", ".rs"),
    ("make a script in go that pings a host", "go", ".go"),
])
def test_generate_script_writes_the_language_the_request_names(monkeypatch, tmp_path, asked, language, ending):
    """The language is the one the request names, not a few letters inside another word ("shows"
    was Bash, "counts" TypeScript), and not a word of the evidence added to the request."""
    import eli.coding.agent as coding
    from eli.execution.effectors.coding import _h_generate_script

    seen = {}

    def fake_solve(task, **kw):
        seen["language"] = kw.get("language")
        return {"code": "print('done')\n", "solved": True, "score": 1.0}

    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(coding, "solve", fake_solve)
    result = _h_generate_script("GENERATE_SCRIPT", {"description": asked, "_no_background": True,
                                                    "_evidence": "notes gathered by ELI's agents and its tests"})
    assert seen["language"] == language
    assert result["filename"].endswith(ending)


def test_generate_script_never_saves_a_model_failure_as_a_script(monkeypatch, tmp_path):
    import eli.coding.agent as coding
    from eli.cognition import gguf_inference
    from eli.execution.effectors import coding as fx

    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(coding, "solve", lambda task, **kw: {"code": ""})
    monkeypatch.setattr(gguf_inference, "load_model", lambda *a, **k: None)
    monkeypatch.setattr(fx, "chat", lambda *a, **k: {"ok": False, "error": "model_not_loaded",
                                                     "content": "The model is not loaded yet."})
    out = fx._h_generate_script("GENERATE_SCRIPT", {"description": "write a bash script that lists files",
                                                    "_no_background": True})
    assert out["ok"] is False and out["error"] == "model_not_loaded"
    assert not list((tmp_path / "artifacts").rglob("*.sh"))
