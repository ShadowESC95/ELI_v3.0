"""When another program holds the GPU, ELI says which.

Live (2026-10-06): Ollama kept a model in 5 GB of an 8 GB card. ELI saw 1 GB free, spent
109 s probing 40 GPU layers whose weights alone needed 1.3 GB, loaded on the CPU and answered
"Hi" in 108 s. Nothing on screen or in its own answers said why, and the operator re-downloaded
a GPU pack that was working.
"""
from __future__ import annotations

import os
import subprocess
import types

import pytest

import eli.core.hardware_profile as hp


# ── who holds the card ───────────────────────────────────────────────────────

def _smi(monkeypatch, stdout):
    monkeypatch.setattr(hp, "nvidia_smi_path", lambda: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=stdout))
    monkeypatch.setattr(hp, "_service_of", lambda pid: "ollama.service" if pid == 4242 else "")


def test_the_programs_holding_vram_are_named_largest_first(monkeypatch):
    _smi(monkeypatch, "4242, /usr/local/lib/ollama/llama-server, 5146\n"
                      "777, /opt/google/chrome/chrome --type=gpu-process, 66\n"
                      f"{os.getpid()}, /usr/app/ELI, 900\n")
    holders = hp.vram_holders()
    assert [h["pid"] for h in holders] == [4242, 777]             # ELI itself is not listed
    assert holders[0]["name"] == "llama-server" and holders[0]["service"] == "ollama.service"
    text = hp.describe_vram_holders(holders, total_mb=8192)
    assert text == "llama-server (ollama.service, pid 4242) 5146 MB"   # 66 MB is under 1% of the card


def test_a_driver_that_does_not_report_per_process_memory_still_names_the_process(monkeypatch):
    _smi(monkeypatch, "4242, C:\\Program Files\\Ollama\\ollama.exe, [N/A]\n")
    holders = hp.vram_holders()
    assert holders[0]["mb"] is None
    assert "pid 4242" in hp.describe_vram_holders(holders, total_mb=8192)


# ── what ELI can say afterwards ──────────────────────────────────────────────

def test_a_load_that_fell_to_the_cpu_is_reported_with_its_cause():
    from eli.runtime.truth_report import runtime_load_facts
    snap = {"requested": {"n_ctx": 12000, "n_gpu_layers": 40, "n_batch": 256},
            "effective": {"n_ctx": 2048, "n_gpu_layers": 0, "n_batch": 128},
            "vram_free_mb_at_load": 1039,
            "vram_held_by_others": "llama-server (ollama.service, pid 4242) 5146 MB"}
    facts = runtime_load_facts(snap, {})
    layers = [d for d in facts["differences"] if d.startswith("GPU layers")]
    assert layers and "requested 40, loaded 0" in layers[0]
    assert "1039 MB of VRAM was free at load" in layers[0] and "ollama.service" in layers[0]


def test_the_reinstall_question_says_the_pack_already_works(monkeypatch):
    try:
        from eli.gui.panels import startup
    except Exception as exc:  # Qt bindings are stubbed in the headless test env
        pytest.skip(f"GUI panels unavailable here: {exc}")
    monkeypatch.setattr(hp, "get_live_gpu_telemetry", lambda: {"free_mb": 1039, "total_mb": 8192})
    monkeypatch.setattr(hp, "vram_holders", lambda **k: [
        {"pid": 4242, "name": "llama-server", "mb": 5146.0, "service": "ollama.service"}])
    text = startup._install_question("CUDA", reinstall=True)
    assert "installed and was verified" in text and "does not change" in text
    assert "1039 MB of this card's 8192 MB is free" in text and "ollama.service" in text
    assert "downloaded again only when an ELI update" in startup._install_question("CUDA", reinstall=False)


def test_programs_drawing_on_a_card_are_listed_with_their_card(monkeypatch):
    """The compute list leaves out what draws on a card: a headset's runtime and ELI VR itself, which
    took the card after the model loaded (2026-11-04). nvidia-smi's full report has them, per card."""
    report = """<?xml version="1.0" ?><nvidia_smi_log><gpu id="00000000:01:00.0"><processes>
      <process_info><pid>5100</pid><type>G</type><process_name>/usr/bin/monado-service</process_name><used_memory>612 MiB</used_memory></process_info>
      <process_info><pid>5200</pid><type>G</type><process_name>/tmp/.mount_ELI_VR/godot</process_name><used_memory>1480 MiB</used_memory></process_info>
      <process_info><pid>5200</pid><type>C</type><process_name>/tmp/.mount_ELI_VR/godot</process_name><used_memory>20 MiB</used_memory></process_info>
    </processes></gpu><gpu id="00000000:03:00.0"><processes>
      <process_info><pid>4242</pid><type>C</type><process_name>/usr/local/lib/ollama/llama-server</process_name><used_memory>5146 MiB</used_memory></process_info>
    </processes></gpu></nvidia_smi_log>"""
    _smi(monkeypatch, report)
    # ELI itself is left out by pid: on a Windows runner the test process was pid 5200 once, and the
    # headset's entry vanished with it, so ELI's own pid is given as one the report does not hold
    holders = {h["pid"]: h for h in hp.vram_holders(exclude_pid=1)}
    assert holders[5200]["mb"] == 1500.0 and holders[5200]["gpus"] == {0: 1500.0} and holders[5200]["type"] == "G"
    assert holders[4242]["gpus"] == {1: 5146.0} and holders[4242]["service"] == "ollama.service"
    assert holders[5100]["name"] == "monado-service"
    from eli.core import load_plan
    assert [load_plan.is_xr_program(h["name"]) for h in (holders[5100], holders[5200], holders[4242])] == [True, True, False]


# ── the free VRAM with ELI's own model unloaded ──────────────────────────────
# enforce_hardware_authority recommends for the VRAM there would be with no model of ELI's loaded. It
# took that as the card's total less a fixed 500 MB "for the display server", so whatever else held
# the card (another model server, a browser, a headset) was counted as freed by unloading ELI's.

def _card(free_mb, total_mb=8192):
    hw = hp.HardwareProfile()
    hw.has_gpu, hw.free_vram_mb, hw.total_vram_mb = True, free_mb, total_mb
    return hw


def _no_kfd(monkeypatch, tmp_path):
    monkeypatch.setattr(hp, "_KFD_PROCESSES", tmp_path / "no-kfd")


def _loaded_at(monkeypatch, free_at_load):
    import eli.cognition.gguf_inference as gi
    monkeypatch.setattr(gi, "get_runtime_snapshot", lambda: {"vram_free_mb_at_load": free_at_load})


def test_nvidia_on_any_os_counts_what_eli_itself_holds(monkeypatch, tmp_path):
    _no_kfd(monkeypatch, tmp_path)
    _loaded_at(monkeypatch, None)
    _smi(monkeypatch, "4242, /usr/local/lib/ollama/llama-server, 5146\n"
                      f"{os.getpid()}, /usr/app/ELI, 900\n")
    assert hp.process_vram_mb() == 900.0
    assert hp.cold_free_vram_mb(_card(1100)) == 2000, "the other server's 5146 MB is not ELI's to free"


def test_a_driver_that_does_not_say_falls_back_to_what_was_free_before_the_load(monkeypatch, tmp_path):
    """Windows' display driver mode lists ELI with no figure; Intel and Apple list nothing. The load
    plan read the free VRAM itself just before loading, on whatever backend llama.cpp runs."""
    _no_kfd(monkeypatch, tmp_path)
    _smi(monkeypatch, f"{os.getpid()}, C:\\ELI\\ELI.exe, [N/A]\n")
    _loaded_at(monkeypatch, 3000)
    assert hp.process_vram_mb() is None
    assert hp.cold_free_vram_mb(_card(1100)) == 3000
    monkeypatch.setattr(hp, "nvidia_smi_path", lambda: None)       # no NVIDIA tool at all
    assert hp.cold_free_vram_mb(_card(1100)) == 3000
    _loaded_at(monkeypatch, None)
    assert hp.cold_free_vram_mb(_card(1100)) is None, "nothing measured: no figure is made up"


def test_amd_on_linux_reads_the_kernels_per_process_counters(monkeypatch, tmp_path):
    monkeypatch.setattr(hp, "nvidia_smi_path", lambda: None)
    _loaded_at(monkeypatch, None)
    own = tmp_path / "kfd" / str(os.getpid())
    own.mkdir(parents=True)
    (own / "vram_0").write_text(str(1536 * 1_048_576))
    monkeypatch.setattr(hp, "_KFD_PROCESSES", tmp_path / "kfd")
    real_read = hp.Path.read_text
    monkeypatch.setattr(hp.Path, "read_text", lambda self, *a, **k: "ELI" if self.name == "comm"
                        else real_read(self, *a, **k))
    assert hp.process_vram_mb() == 1536.0
    assert hp.cold_free_vram_mb(_card(1000, 16384)) == 2536


def test_the_recommendation_with_a_model_loaded_is_made_for_the_measured_cold_card(monkeypatch, tmp_path):
    import eli.cognition.gguf_inference as gi
    import eli.core.runtime_settings as rs
    _no_kfd(monkeypatch, tmp_path)
    _smi(monkeypatch, "4242, /usr/local/lib/ollama/llama-server, 5146\n"
                      f"{os.getpid()}, /usr/app/ELI, 900\n")
    monkeypatch.setattr(gi, "_llm", object())
    monkeypatch.setattr(rs, "load_settings", lambda: {})
    monkeypatch.setattr(hp, "detect_hardware", lambda: _card(1100))
    monkeypatch.setattr(hp, "discover_models", lambda: [])
    monkeypatch.setattr(hp, "_user_model_path_is_valid", lambda settings: True)
    seen = {}

    def recommend(hw, models):
        seen["free"] = hw.free_vram_mb
        return hp.ModelRecommendation(n_ctx=2048, batch_size=128)

    monkeypatch.setattr(hp, "recommend", recommend)
    hp.enforce_hardware_authority()
    assert seen["free"] == 2000, "8192 − 500 was taken as free with another model server on the card"
