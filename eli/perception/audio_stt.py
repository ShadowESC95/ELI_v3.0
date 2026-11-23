"""
ELI Audio STT — safer, dependency-aware version.

Exports:
- get_audio_stt()
- start_audio_listening(callback=None)
- stop_audio_listening()
- listen_for_command(timeout=5)

Key fixes:
- Bare wake word arms a guarded-command window instead of being emitted as chat
- Guarded commands (open/run/delete/etc.) require wake word unless wake is disabled
- Safe direct commands (pause/play/volume/etc.) work without wake word
- Short ambient garbage is ignored instead of being forwarded into the LLM
- Repeated phrase collapse for cases like: "open spotify open spotify open spotify"
- Single listener thread / single microphone context; no nested microphone reuse
- Clear diagnostics when SpeechRecognition / PyAudio are unavailable
"""
from __future__ import annotations

import os

# libpulse reads PULSE_SOURCE, not ELI_MIC_PULSE_SOURCE — map early so frozen
# builds and v2.3.30 docs that used the ELI_* name still work.
_ps = os.environ.get("ELI_MIC_PULSE_SOURCE", "").strip()
if _ps and not os.environ.get("PULSE_SOURCE", "").strip():
    os.environ["PULSE_SOURCE"] = _ps

import atexit
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional

# numpy is required by the acoustic wake-word scoring and tone classification
# paths; guarded so a missing numpy degrades those features instead of
# breaking STT entirely (their call sites already catch exceptions).
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


# When the wake word is heard, lower playback volume while the user speaks the command, restore
# it after dispatch, and suppress the assistant/media echo the mic hears afterwards. Doesn't
# change mic device selection, the STT backend (Whisper, Google, Vosk), sample rate or
# recognizer model.

import os as _eli_os
import re as _eli_re
import subprocess as _eli_subprocess
_ELI_ECHO_GATE_DEFAULT_S = float(_eli_os.environ.get("ELI_STT_POST_COMMAND_ECHO_GATE", "0.5"))
_ELI_DUCK_LEVEL = _eli_os.environ.get("ELI_WAKE_DUCK_LEVEL", "18%")
_ELI_DUCK_ENABLED = _eli_os.environ.get("ELI_WAKE_DUCK_ENABLED", "1") != "0"


def _eli_run_quiet(argv, timeout=1.5):
    try:
        cp = _eli_subprocess.run(
            argv,
            stdout=_eli_subprocess.PIPE,
            stderr=_eli_subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return cp.returncode == 0, (cp.stdout or cp.stderr or "").strip()
    except Exception as exc:
        return False, str(exc)


def _eli_get_sink_volume_snapshot():
    # Prefer wpctl because your system is PipeWire.
    ok, out = _eli_run_quiet(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=1.0)
    if ok and out:
        return ("wpctl", out)

    ok, out = _eli_run_quiet(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=1.0)
    if ok and out:
        return ("pactl", out)

    # Windows / macOS / Linux without wpctl+pactl — use platform_compat.
    try:
        from eli.utils import platform_compat as _pc
        vol = _pc.get_volume()
        if vol is not None:
            return ("platform", str(int(vol)))
    except Exception:
        log.debug("[AUDIO_DUCK] platform volume snapshot unavailable", exc_info=True)

    return None


def _eli_parse_wpctl_volume(snapshot):
    # Example: "Volume: 0.65" or "Volume: 0.65 [MUTED]"
    if not snapshot:
        return None
    m = _eli_re.search(r"Volume:\s*([0-9.]+)", str(snapshot))
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


# The output level ELI holds low while it listens: the snapshot to put back, and the one a volume command
# already put back (release_output_duck), which the listener's own put-back then leaves alone.
_OUTPUT_HOLD = {"snapshot": None, "given_back": None}
_OUTPUT_HOLD_LOCK = threading.RLock()


def _hold_output(snap):
    with _OUTPUT_HOLD_LOCK:
        _OUTPUT_HOLD["snapshot"], _OUTPUT_HOLD["given_back"] = snap, None
    return snap


def release_output_duck() -> bool:
    """Put the output level back now, ending the hold while ELI listens: a volume command (and a question
    about the level) is about the level in force once listening ends. True when there was a hold."""
    with _OUTPUT_HOLD_LOCK:
        snap = _OUTPUT_HOLD["snapshot"]
        if snap is None:
            return False
        _eli_restore_output(snap)
        _OUTPUT_HOLD["given_back"] = snap
    log.debug("[AUDIO_DUCK] output level given back for a volume command")
    return True


def _eli_duck_output():
    if not _ELI_DUCK_ENABLED:
        return None

    snap = _eli_get_sink_volume_snapshot()

    if snap and snap[0] == "wpctl":
        _eli_run_quiet(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", _ELI_DUCK_LEVEL], timeout=1.0)
        log.debug(f"[AUDIO_DUCK] ducked output to {_ELI_DUCK_LEVEL}; snapshot={snap[1]!r}")
        return _hold_output(snap)

    if snap and snap[0] == "pactl":
        _eli_run_quiet(["pactl", "set-sink-volume", "@DEFAULT_SINK@", _ELI_DUCK_LEVEL], timeout=1.0)
        log.debug(f"[AUDIO_DUCK] ducked output to {_ELI_DUCK_LEVEL}; snapshot=pactl")
        return _hold_output(snap)

    if snap and snap[0] == "platform":
        try:
            from eli.utils import platform_compat as _pc
            # Match the ~18% spirit of _ELI_DUCK_LEVEL on a 0-100 scale.
            target = max(5, min(30, int(str(_ELI_DUCK_LEVEL).rstrip("%") or "18")))
            if _pc.set_volume(target):
                log.debug(f"[AUDIO_DUCK] platform volume ducked to {target}; snapshot={snap[1]!r}")
                return _hold_output(snap)
        except Exception:
            log.debug("[AUDIO_DUCK] platform duck failed", exc_info=True)

    return None


def _eli_restore_output(snapshot):
    if not snapshot:
        return
    with _OUTPUT_HOLD_LOCK:
        if snapshot is _OUTPUT_HOLD["given_back"]:
            # a volume command put the level back and changed it since; putting it back again undid that
            _OUTPUT_HOLD["given_back"] = None
            log.debug("[AUDIO_DUCK] output level already given back; left as it is")
            return
        if snapshot is _OUTPUT_HOLD["snapshot"]:
            _OUTPUT_HOLD["snapshot"] = None
        _eli_put_back(snapshot)


def _eli_put_back(snapshot):
    backend, raw = snapshot

    if backend == "wpctl":
        vol = _eli_parse_wpctl_volume(raw)
        if vol is not None:
            _eli_run_quiet(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", str(vol)], timeout=1.0)
            log.debug(f"[AUDIO_DUCK] restored output volume to {vol}")
        return

    if backend == "platform":
        try:
            from eli.utils import platform_compat as _pc
            _pc.set_volume(int(raw))
            log.debug(f"[AUDIO_DUCK] restored platform volume to {raw}")
        except Exception:
            log.debug("[AUDIO_DUCK] platform restore failed", exc_info=True)
        return

    if backend == "pactl":
        # "Volume: front-left: 42598 /  65% / -11.23 dB, ...": the level was left ducked for good where
        # only pactl answered
        m = _eli_re.search(r"(\d+)%", str(raw))
        if m:
            _eli_run_quiet(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{m.group(1)}%"], timeout=1.0)
            log.debug(f"[AUDIO_DUCK] restored output volume to {m.group(1)}%")
            return
    log.debug(f"[AUDIO_DUCK] could not read the level to put back from {raw!r}")


_MEDIA_DUCKED = False
_MEDIA_DUCK_LOCK = threading.Lock()


def _media_duck_enabled() -> bool:
    return os.environ.get("ELI_MEDIA_DUCK", "1").strip().lower() not in ("0", "false", "no", "off")


def media_player_duck_pause() -> None:
    """Pause the active MPRIS player while ELI listens (Linux desktop media)."""
    global _MEDIA_DUCKED
    if not _media_duck_enabled():
        return
    with _MEDIA_DUCK_LOCK:
        if _MEDIA_DUCKED:
            return
        try:
            from eli.integrations.mpris.playerctl_backend import get_player_status, pause
            st = get_player_status()
            if isinstance(st, dict) and st.get("ok") and str(st.get("status", "")).lower() == "playing":
                pause(st.get("player"))
                _MEDIA_DUCKED = True
                log.debug("[AUDIO_DUCK] paused active media player for wake-word listen")
        except Exception:
            log.debug("[AUDIO_DUCK] media player pause skipped", exc_info=True)


def media_player_duck_resume() -> None:
    """Resume media paused by media_player_duck_pause()."""
    global _MEDIA_DUCKED
    if not _media_duck_enabled():
        return
    with _MEDIA_DUCK_LOCK:
        if not _MEDIA_DUCKED:
            return
        try:
            from eli.integrations.mpris.playerctl_backend import play
            play(None)
            log.debug("[AUDIO_DUCK] resumed paused media player")
        except Exception:
            log.debug("[AUDIO_DUCK] media player resume skipped", exc_info=True)
        finally:
            _MEDIA_DUCKED = False


def _eli_restore_duck(snapshot) -> None:
    """Restore system volume and any paused media after a wake-word listen window."""
    media_player_duck_resume()
    if snapshot:
        _eli_restore_output(snapshot)


def _eli_echo_like_assistant_output(text):
    t = _eli_re.sub(r"\s+", " ", str(text or "").lower()).strip(" .,!?:;")
    if not t:
        return False

    # These are not normal user commands. They are typical assistant/TTS echo.
    echo_prefixes = (
        "yes i can help",
        "i can help you",
        "to help you",
        "to help you out",
        "based on our recent conversation",
        "here's why",
        "heres why",
        "it seems that",
        "it seems you're",
        "it seems you are",
        "the user asked me",
        "the user is asking",
        "the user is referring",
        "let's start with",
        "lets start with",
        "as for spotify",
        "i hope this information",
        "if that doesn't help",
        "if that doesnt help",
        "open your web browser",
        "search for the artist",
        "once you've found",
        "once you found",
        "click the play button",
    )

    if any(t.startswith(x) for x in echo_prefixes):
        return True

    # Long instructional fragments captured from ELI output.
    if len(t.split()) >= 12 and any(x in t for x in (
        "open your web browser",
        "click the play button",
        "search for",
        "i hope this",
        "let me know",
        "recent conversation",
    )):
        return True

    return False


# Silence ALSA/Jack/PulseAudio stderr noise during microphone init
import ctypes
import ctypes.util

from eli.utils.log import get_logger
log = get_logger(__name__)

try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c"))
    _devnull_fd = os.open(os.devnull, os.O_WRONLY)
    _saved_stderr = os.dup(2)
    def _suppress_alsa():
        os.dup2(_devnull_fd, 2)
    def _restore_stderr():
        os.dup2(_saved_stderr, 2)
except Exception:
    def _suppress_alsa(): pass
    def _restore_stderr(): pass

try:
    import speech_recognition as sr  # type: ignore
    _SR_IMPORT_ERROR: Optional[Exception] = None
except Exception as e:  # pragma: no cover
    sr = None
    _SR_IMPORT_ERROR = e

WAKE_WORDS = [
    "hey eli", "hey computer", "eli", "computer",
]


# Echo cancellation: a private PipeWire client process running PipeWire's own echo canceller in
# monitor mode, so what the default sink plays (ELI's voice, its music, anything) is taken out of the
# microphone. It publishes one extra source for ELI alone; nothing of the desktop's is changed, and
# it goes when ELI goes (it is ELI's child, told to end with it).
AEC_SOURCE = "eli_echo_cancelled"
_AEC = {"proc": None, "reason": "", "pinned": False}


def _echo_cancel_wanted() -> bool:
    env = os.environ.get("ELI_STT_ECHO_CANCEL", "").strip().lower()
    if env:
        return env in ("1", "true", "yes", "on")
    try:
        from eli.core.runtime_settings import load_settings
        return bool((load_settings() or {}).get("stt_echo_cancel", False))
    except Exception:
        log.debug("[AUDIO_AEC] setting unreadable", exc_info=True)
        return False


def _aec_config(capture_target: str = "") -> str:
    """The PipeWire client configuration: the echo canceller (webrtc) in monitor mode, the default
    sink's monitor as what it takes out, the microphone (`capture_target`, else the default source)
    as what it cleans, and the clean source for ELI, at the lowest priority so it never becomes the
    desktop's default."""
    target = f' target.object = "{capture_target}"' if capture_target else ""
    return (
        "context.properties = { log.level = 0 }\n"
        "context.spa-libs = {\n"
        "    audio.convert.* = audioconvert/libspa-audioconvert\n"
        "    support.*       = support/libspa-support\n"
        "}\n"
        "context.modules = [\n"
        "    { name = libpipewire-module-rt args = { } flags = [ ifexists nofail ] }\n"
        "    { name = libpipewire-module-protocol-native }\n"
        "    { name = libpipewire-module-client-node }\n"
        "    { name = libpipewire-module-adapter }\n"
        "    { name = libpipewire-module-echo-cancel\n"
        "      args = {\n"
        "        library.name = aec/libspa-aec-webrtc\n"
        "        monitor.mode = true\n"
        f"        capture.props = {{ node.name = \"eli_echo_capture\" node.passive = true{target} }}\n"
        f"        source.props = {{ node.name = \"{AEC_SOURCE}\" node.description = \"ELI echo-cancelled microphone\"\n"
        "                         priority.session = 0 priority.driver = 0 }\n"
        "      }\n"
        "    }\n"
        "]\n")


def _end_with_parent() -> None:
    """In the child, before it runs: end when ELI ends, however it ends (Linux prctl
    PR_SET_PDEATHSIG). Nothing is caught here: a failure fails the start, which says so."""
    import ctypes
    import signal
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)


def _aec_source_listed(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True,
                                 timeout=3).stdout
            if AEC_SOURCE in (out or ""):
                return True
        except Exception:
            log.debug("[AUDIO_AEC] source list unreadable", exc_info=True)
        if _AEC["proc"] is not None and _AEC["proc"].poll() is not None:
            return False
        time.sleep(0.1)
    return False


def _unload_echo_cancel() -> None:
    """Stop ELI's echo canceller, if it runs, and its source with it."""
    proc, _AEC["proc"] = _AEC["proc"], None
    if _AEC.get("pinned"):
        for name in ("ELI_MIC_PULSE_SOURCE", "PULSE_SOURCE"):
            if os.environ.get(name) == AEC_SOURCE:
                os.environ.pop(name, None)
        _AEC["pinned"] = False
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.debug("[AUDIO_AEC] echo cancellation stopped")


def echo_cancel_state() -> dict:
    """{active, reason} for the settings page and the diagnostics."""
    proc = _AEC["proc"]
    return {"active": bool(proc is not None and proc.poll() is None), "reason": _AEC["reason"]}


def ensure_echo_cancel() -> bool:
    """Take what the default sink plays out of the microphone, where the sound server allows it
    without changing the desktop: PipeWire's echo canceller in monitor mode, in a client process of
    ELI's own (pipewire -c), its clean source read by ELI alone (PULSE_SOURCE for this process).

    The earlier way loaded PulseAudio's module-echo-cancel into the server: it cancels only what is
    played into its own sink, and every application plays to the default sink, so it cancelled
    nothing of ELI's voice or music; making its sink the default would have changed the desktop
    for every program, and left it so after a crash. On PulseAudio without PipeWire that is still the
    only way, so it is not done there (echo_cancel_state says why).

    On with Settings ▸ Audio (stt_echo_cancel) or ELI_STT_ECHO_CANCEL=1. True when it runs."""
    from eli.utils import platform_compat as _pc
    if not _pc.LINUX or not _echo_cancel_wanted():
        _AEC["reason"] = "" if _pc.LINUX else "echo cancellation here is the system's own (Windows, macOS)"
        return False
    if _AEC["proc"] is not None and _AEC["proc"].poll() is None:
        return True
    server = _pc.sound_server()
    if server != "pipewire":
        _AEC["reason"] = ("PulseAudio cancels only what plays into its own sink; cancelling the default "
                          "sink would change the desktop's routing, so it is not done" if server
                          else "no sound server runs")
        log.info(f"[AUDIO_AEC] not started: {_AEC['reason']}")
        return False
    if not shutil.which("pipewire"):
        _AEC["reason"] = "the pipewire program is not installed"
        return False
    try:
        from eli.core.paths import temp_path
        conf = temp_path("echo_cancel.conf")
        pinned = os.environ.get("ELI_MIC_PULSE_SOURCE", "").strip()
        conf.write_text(_aec_config("" if pinned in ("", AEC_SOURCE) else pinned), encoding="utf-8")
        _AEC["proc"] = subprocess.Popen(["pipewire", "-c", str(conf)], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, preexec_fn=_end_with_parent)
    except Exception as exc:
        _AEC["reason"] = f"the echo canceller did not start ({exc})"
        log.warning(f"[AUDIO_AEC] {_AEC['reason']}", exc_info=True)
        return False
    atexit.register(_unload_echo_cancel)
    if not _aec_source_listed(timeout=5.0):
        _AEC["reason"] = "the echo canceller started but published no source"
        log.warning(f"[AUDIO_AEC] {_AEC['reason']}")
        _unload_echo_cancel()
        return False
    os.environ["ELI_MIC_PULSE_SOURCE"] = os.environ["PULSE_SOURCE"] = AEC_SOURCE
    _AEC["pinned"], _AEC["reason"] = True, ""
    log.info("[AUDIO_AEC] echo cancellation on: the default sink's sound is taken out of the microphone")
    return True


def _merge_custom_wake_words() -> None:
    """Fold any user-configured custom wake phrase(s) into WAKE_WORDS so the
    transcription matcher recognises them too (the acoustic model is trained on
    them separately). Safe/no-op if none configured."""
    try:
        from eli.perception import wakeword as _ww
        for p in _ww.get_wake_phrases():
            p = str(p).strip().lower()
            if p and p not in WAKE_WORDS:
                WAKE_WORDS.append(p)
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def primary_wake_word() -> str:
    """The phrase to inject when the acoustic detector fires — the user's custom
    wake word if set, else the default."""
    try:
        from eli.perception import wakeword as _ww
        ph = _ww.get_wake_phrases()
        if ph:
            return ph[0]
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return "computer"

SAFE_DIRECT_COMMANDS = {
    "play", "pause", "resume", "stop",
    "next", "previous", "skip", "back",
    "next song", "previous song", "next track", "previous track",
    "mute", "unmute",
    "volume up", "volume down",
    "turn volume up", "turn volume down",
    "increase volume", "decrease volume",
    "louder", "quieter",
}

GUARDED_PREFIXES = (
    "open ", "launch ", "start ", "run ", "execute ",
    "close ", "quit ", "kill ", "stop process ",
    "delete ", "remove ", "rm ", "move ", "copy ", "rename ",
    "mkdir ", "create folder", "create directory",
    "open folder", "open directory", "browse ",
    "go to ", "change directory", "cd ",
    "install ", "uninstall ", "sudo ",
    "shutdown", "reboot", "power off",
    "open browser", "open chrome", "open firefox",
    "open settings", "open spotify", "open terminal",
)

GUARDED_EXACT = {
    "settings", "spotify", "terminal", "browser", "chrome", "firefox",
}

MAIN_TIMEOUT = float(os.environ.get("ELI_STT_MAIN_TIMEOUT", "1.2"))
ARMED_MAIN_TIMEOUT = float(os.environ.get("ELI_STT_ARMED_MAIN_TIMEOUT", "4.0"))
PHRASE_TIME_LIMIT = float(os.environ.get("ELI_STT_PHRASE_TIME_LIMIT", "6.0"))
WAKE_ARM_TIMEOUT = float(os.environ.get("ELI_STT_WAKE_ARM_TIMEOUT", "18.0"))
# Shorter pause when unarmed — wake word is one word, safe-direct is 1-3 words.
# 0.20s of silence is enough to know a short command ended.
UNARMED_PAUSE_S = float(os.environ.get("ELI_STT_UNARMED_PAUSE", "0.20"))
WAKE_DEBOUNCE_S = float(os.environ.get("ELI_STT_WAKE_DEBOUNCE", "0.7"))
ELI_DISABLE_WAKE_WORD = os.environ.get("ELI_DISABLE_WAKE_WORD", "0").lower() in ("1", "true", "yes", "on")
def _allow_direct_chat() -> bool:
    """Runtime check so the GUI can flip ELI_STT_ALLOW_DIRECT_CHAT without a restart."""
    return os.environ.get("ELI_STT_ALLOW_DIRECT_CHAT", "0").lower() in ("1", "true", "yes", "on")

ALLOW_DIRECT_CHAT_WITHOUT_WAKE = _allow_direct_chat()  # kept for backwards compat
MIN_DIRECT_CHAT_WORDS = int(os.environ.get("ELI_STT_MIN_DIRECT_CHAT_WORDS", "2"))

# Most-recently-created STT instance, so a chat action (WAKE_ENROLL) can drive
# wake-word enrollment through the already-running mic loop (no second device).
_ACTIVE_STT = None


def get_active_stt():
    return _ACTIVE_STT


def _require_running_stt():
    stt = _ACTIVE_STT
    if stt is None or not getattr(stt, "is_listening", False):
        return None
    return stt


def begin_wake_enrollment(n: int = 5) -> dict:
    """Capture `n` real-mic samples of the user saying the WAKE WORD, then retrain the
    wake model on their voice. (Standalone re-tune; the full 'train my voice' flow
    below also does this.)"""
    stt = _require_running_stt()
    if stt is None:
        return {"ok": False, "error": "the microphone is not running — enable voice first, then try again"}
    from eli.perception import wakeword as _ww
    ph = (_ww.get_wake_phrases() or ["computer"])[0]
    _sink = lambda pcm, sr: _ww.save_enrollment_clip(pcm, sr=sr)

    def _done():
        try:
            r = _ww.train_model()
            stt._speak_prompt("Your wake word is trained and ready."
                              if r.get("ok") else "Wake-word training could not finish.")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    n = max(1, min(20, int(n)))
    steps = [{"prompt": (f"Say your wake word, {ph}." if i == 0 else f"{ph}, again."),
              "sink": _sink} for i in range(n)]
    stt.begin_capture_script(steps, _done,
                             prompt_start=f"Let's tune your wake word to your voice — {n} times, pausing between each.")
    return {"ok": True, "samples": n,
            "message": f"Listening — say \"{ph}\" {n} times (pause between each); I'll retrain automatically."}


# Emotion prompts: the user says the SAME neutral line in each feeling, so the
# classifier learns prosody (not words). Extend by adding to voice_profile.EMOTIONS
# + a line here. The first rep of each gives the full cue; reps say "again".
_EMOTION_CUES = {
    "neutral": "In your normal voice, say: the meeting is at noon.",
    "happy":   "Now happily, like good news: the meeting is at noon!",
    "angry":   "Now annoyed, irritated: the meeting is at noon.",
    "excited": "Now excited and energetic: the meeting is at noon!",
    "sad":     "Now sadly, low and flat: the meeting is at noon.",
}


def begin_voice_training(per_emotion: int = 2, wake_reps: int = 3) -> dict:
    """Unified setup: capture wake-word reps (→ wake model) AND labelled-emotion samples
    (→ the tone/emotion classifier + voice baseline), in one guided session. This is
    what 'train my voice' runs. Modules stay separate; the user does one flow."""
    stt = _require_running_stt()
    if stt is None:
        return {"ok": False, "error": "the microphone is not running — enable voice first, then try again"}
    from eli.perception import wakeword as _ww
    from eli.perception import voice_profile as _vp
    ph = (_ww.get_wake_phrases() or ["computer"])[0]

    steps = []
    _wake_sink = lambda pcm, sr: _ww.save_enrollment_clip(pcm, sr=sr)
    for i in range(max(0, int(wake_reps))):
        steps.append({"prompt": (f"First, your wake word — say {ph}." if i == 0 else f"{ph}, again."),
                      "sink": _wake_sink})

    def _emo_sink(emo):
        def _s(pcm, sr):
            uid = None
            try:
                from eli.perception.speaker_id import get_active_speaker
                uid = str((get_active_speaker() or {}).get("user_id") or "").strip() or None
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            _vp.add_labelled_sample(pcm, sr, emo, user_id=uid)
            if emo == "neutral":
                _vp.add_sample(pcm, sr, user_id=uid)          # neutral also seeds the voice baseline
        return _s

    for emo in _vp.EMOTIONS:
        for j in range(max(1, int(per_emotion))):
            steps.append({"prompt": (_EMOTION_CUES.get(emo, f"Say it {emo}.") if j == 0
                                     else "Again, same feeling."),
                          "sink": _emo_sink(emo)})

    def _done():
        try:
            _ww.train_model()
            uid = None
            try:
                from eli.perception.speaker_id import get_active_speaker
                uid = str((get_active_speaker() or {}).get("user_id") or "").strip() or None
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            r = _vp.build_profile(user_id=uid)
            ok = bool(r.get("ok")) and bool((r.get("emotion_model") or {}).get("ok"))
            # Speaker ID: enroll from the active user's neutral samples
            try:
                from eli.perception.speaker_id import enroll_speaker, get_active_speaker
                import soundfile as sf
                sp = get_active_speaker()
                name = str(sp.get("name") or "default")
                uid = str(sp.get("user_id") or "")
                samples_dir = _vp._samples_dir(uid or None)
                for f in sorted(samples_dir.glob("voice_*.wav"))[:3]:
                    data, sr = sf.read(str(f), dtype="float32")
                    if data.ndim > 1:
                        data = data.mean(axis=1)
                    enroll_speaker((data * 32767).astype(np.int16), sr, name=name, user_id=uid or None)
                    break
            except Exception:
                log.debug("speaker enroll after voice training skipped", exc_info=True)
            stt._speak_prompt("Done — I've learned your wake word, your voice, and your tone."
                              if ok else "Voice setup finished — wake word and baseline are set.")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    stt.begin_capture_script(steps, _done,
                             prompt_start=("Let's set up your voice. I'll cue you each time — "
                                           "speak right after each prompt."))
    return {"ok": True, "samples": len(steps), "emotions": list(_vp.EMOTIONS),
            "message": (f"Voice setup started ({len(steps)} short prompts): your wake word, then "
                        f"the same line said neutral / happy / angry / excited / sad so I learn "
                        f"your tone. Just follow the cues.")}


def begin_speaker_enrollment(name: str = "", n: int = 4, *, include_emotions: bool = True) -> dict:
    """Enroll a named speaker: speaker ID fingerprint + per-user tone/emotion baseline."""
    stt = _require_running_stt()
    if stt is None:
        return {"ok": False, "error": "the microphone is not running — enable voice first, then try again"}
    display = str(name or "Speaker").strip() or "Speaker"
    from eli.perception import speaker_id as _sid
    from eli.perception import voice_profile as _vp

    enrolled_uid: dict = {"value": ""}

    def _speaker_sink(pcm, sr):
        r = _sid.enroll_speaker(
            pcm, sr, name=display,
            user_id=enrolled_uid["value"] or None,
        )
        if r.get("user_id"):
            enrolled_uid["value"] = str(r["user_id"])
        uid = enrolled_uid["value"] or None
        _vp.add_sample(pcm, sr, user_id=uid)

    n = max(2, min(8, int(n)))
    steps = [
        {"prompt": (f"Say: Hello ELI, this is {display}." if i == 0 else "Again, same voice."),
         "sink": _speaker_sink}
        for i in range(n)
    ]

    if include_emotions:
        def _emo_sink(emo):
            def _s(pcm, sr):
                uid = enrolled_uid["value"] or None
                _vp.add_labelled_sample(pcm, sr, emo, user_id=uid)
                if emo == "neutral":
                    _vp.add_sample(pcm, sr, user_id=uid)
                    _sid.enroll_speaker(pcm, sr, name=display, user_id=uid)
            return _s

        for emo in _vp.EMOTIONS:
            steps.append({
                "prompt": _EMOTION_CUES.get(emo, f"Say it {emo}."),
                "sink": _emo_sink(emo),
            })

    def _done():
        uid = enrolled_uid["value"] or ""
        try:
            r = _vp.build_profile(user_id=uid or None)
            if uid:
                _sid.rebuild_from_enrollment(uid)
            em_ok = bool((r.get("emotion_model") or {}).get("ok"))
            stt._speak_prompt(
                f"Speaker {display} enrolled — I know your voice and your tone."
                if r.get("ok") and em_ok else
                f"Speaker {display} enrolled — I'll recognize your voice on this machine."
                if r.get("ok") else
                f"Speaker {display} enrolled."
            )
        except Exception:
            stt._speak_prompt(f"Speaker {display} enrolled.")

    stt.begin_capture_script(
        steps, _done,
        prompt_start=(
            f"Speaker enrollment for {display}. I'll cue your voice fingerprint"
            + (" and a short emotion baseline." if include_emotions else ".")
        ),
    )
    em_note = (
        f", then neutral / happy / angry / excited / sad so I learn your tone"
        if include_emotions else ""
    )
    return {
        "ok": True,
        "name": display,
        "samples": len(steps),
        "emotions": list(_vp.EMOTIONS) if include_emotions else [],
        "message": f"Enrolling speaker '{display}' — {n} voice samples{em_note}.",
    }

# Hard override: require wake word for ALL safe-direct commands regardless of media state.
# Default off — media-aware gating in classify() handles the speaker-bleed case dynamically.
# Set ELI_STT_REQUIRE_WAKE_FOR_SAFE_DIRECT=1 to force wake word even in silence.
REQUIRE_WAKE_FOR_SAFE_DIRECT = os.environ.get(
    "ELI_STT_REQUIRE_WAKE_FOR_SAFE_DIRECT", "0"
).lower() in ("1", "true", "yes", "on")

# Per-cycle ambient chatter ("Listening...", "Heard ...", "ignored — no wake word", echo/music
# drops) floods the CLI and buries real logs. Hidden unless ELI_STT_VERBOSE=1; real
# command-lifecycle events (wake word detected, Command, armed) always show.
_STT_VERBOSE = os.environ.get("ELI_STT_VERBOSE", "0").lower() in ("1", "true", "yes", "on")

# The speech gate is set by the room (noise_floor.NoiseFloor), measured every chunk, unless the
# owner sets a number of their own (Settings ▸ Audio: Automatic sensitivity off, or
# ELI_STT_SENSITIVITY=manual with ELI_STT_ENERGY_THRESHOLD).
def _manual_gate() -> Optional[float]:
    """The speech gate the owner set by hand, or None for the automatic one."""
    env = os.environ.get("ELI_STT_SENSITIVITY", "").strip().lower()
    try:
        from eli.core.runtime_settings import load_settings
        s = load_settings() or {}
    except Exception:
        log.debug("[AUDIO] sensitivity settings unreadable; automatic", exc_info=True)
        s = {}
    auto = (env != "manual") if env in ("manual", "auto") else bool(s.get("stt_sensitivity_auto", True))
    if auto:
        return None
    try:
        return float(os.environ.get("ELI_STT_ENERGY_THRESHOLD") or s.get("stt_energy_threshold") or 0) or None
    except (TypeError, ValueError):
        log.debug("[AUDIO] manual threshold unreadable; automatic", exc_info=True)
        return None


def _vprint(*a, **k):
    if _STT_VERBOSE:
        print(*a, **k)



def _cleanup(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^\w\s'%-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    prefixes = ["comma", "please", "could you", "can you", "would you", "will you", "hey", "okay", "ok"]
    for p in prefixes:
        if t.startswith(p + " "):
            t = t[len(p) + 1:].strip()
    return re.sub(r"^[,\s\-:;.]+", "", t).strip()


def _normalize_transcript(text: str) -> str:
    """Whitespace + case normalisation for chat display; keeps . ? ! , ; :"""
    t = (text or "").strip().lower()
    return re.sub(r"\s+", " ", t).strip()


def _collapse_repeated_phrase(text: str, *, strip_punctuation: bool = True) -> str:
    t = _cleanup(text) if strip_punctuation else _normalize_transcript(text)
    words = t.split()
    n = len(words)
    if n < 2:
        return t

    # Whole-phrase repetition (e.g. "pause pause pause" → "pause")
    for size in range(1, min(5, n // 2) + 1):
        chunk = words[:size]
        i = 0
        reps = 0
        while i + size <= n and words[i:i + size] == chunk:
            reps += 1
            i += size
        if reps >= 2 and i == n:
            return " ".join(chunk)

    # Leading-loop bleed: "X X X Y" where X repeats 3+ times then a tail follows.
    # Almost always music/speaker bleed picking up a chorus + a different snippet.
    # Drop the repeated portion; keep the tail (it's most likely the real command).
    for size in range(1, min(4, n // 3) + 1):
        chunk = words[:size]
        i = 0
        reps = 0
        while i + size <= n and words[i:i + size] == chunk:
            reps += 1
            i += size
        if reps >= 3 and i < n:
            tail = words[i:]
            # If tail is also just the chunk word(s), the whole thing was a loop.
            if all(w in chunk for w in tail):
                return " ".join(chunk)
            return " ".join(tail)

    deduped = [words[0]]
    for w in words[1:]:
        if w != deduped[-1]:
            deduped.append(w)
    return " ".join(deduped)




# Fast command alias resolution. Whisper/base.en often mangles very short commands (almost no
# linguistic context). This layer is deliberately tiny and only rewrites observed short-command
# mishears before VoiceGate/router classification.
def _eli_fast_command_alias(text: str) -> str:
    raw = str(text or "")
    t = " ".join(raw.lower().replace("-", " ").replace("_", " ").split())

    exact = {
        "volume up": "volume up",
        "volume down": "volume down",
        "vol up": "volume up",
        "vol down": "volume down",
        "find him down": "volume down",
        "turn him down": "volume down",
        "fin him down": "volume down",
        "mute": "mute",
        "unmute": "unmute",
        "play": "play",
        "pause": "pause",
        "resume": "resume",
        "stop": "stop",
        "next": "next",
        "previous": "previous",
    }

    mapped = exact.get(t)
    if mapped:
        if mapped != t:
            log.debug(f"[STT_ALIAS] {raw!r} -> {mapped!r}")
        return mapped

    return t

def _word_count(text: str) -> int:
    return len(_cleanup(text).split())


# Safe-direct media wake-word guard
def _eli_media_probably_audible() -> bool:
    """
    Best-effort local check. If playerctl says something is playing, bare media
    controls should require the wake window to avoid lyrics triggering commands.
    """
    if os.environ.get("ELI_STT_REQUIRE_WAKE_FOR_BARE_MEDIA", "1").lower() in {"0", "false", "no", "off"}:
        return False
    try:
        cp = subprocess.run(
            ["playerctl", "-a", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.35,
            check=False,
        )
        out = (cp.stdout or "").lower()
        return "playing" in out
    except Exception:
        return False

# Known-app fast-direct route
_SAFE_FAST_APP_TARGETS = {
    "spotify", "browser", "web browser", "firefox", "chrome", "chromium",
    "terminal", "settings", "files", "file manager", "calculator",
    "steam", "discord", "vlc", "text editor", "code", "vscode",
}

_SAFE_FAST_CLOSE_TARGETS = _SAFE_FAST_APP_TARGETS | {"tab", "window"}

def _is_volume_percent_command(text: str) -> bool:
    """A volume set to a level ("volume 40", "set the volume to forty percent"), read by the one
    volume grammar (command_parser.volume_said); this module kept its own pattern of it."""
    t = _collapse_repeated_phrase(text)
    if not t:
        return False
    from eli.execution.routing.command_parser import volume_said
    said = volume_said(t)
    return bool(said and (said.get("args") or {}).get("mode") == "absolute")


def _is_safe_direct(text: str) -> bool:
    t = _collapse_repeated_phrase(text)

    # Existing bare deterministic controls.
    if t in SAFE_DIRECT_COMMANDS:
        return True

    if _is_volume_percent_command(t):
        return True

    # Use media/app alias normalizer if later patches installed it.
    alias = globals().get("_eli_media_voice_alias")
    if callable(alias):
        try:
            t = alias(t)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    # Known local app opens may bypass wake.
    # Arbitrary "open X" stays guarded, because ambient media can say "open ...".
    m = re.fullmatch(r"(?:open|launch|start)\s+(.+)", t)
    if m and m.group(1).strip() in _SAFE_FAST_APP_TARGETS:
        return True

    m = re.fullmatch(r"(?:close|quit|kill)\s+(.+)", t)
    if m and m.group(1).strip() in _SAFE_FAST_CLOSE_TARGETS:
        return True

    # Bare known app names can be used as fast app-open shorthand.
    if t in _SAFE_FAST_APP_TARGETS:
        return True

    return False


def _is_guarded_command(text: str) -> bool:
    t = _collapse_repeated_phrase(text)
    if not t:
        return False
    if t in GUARDED_EXACT:
        return True
    return any(t.startswith(prefix) for prefix in GUARDED_PREFIXES)


def _mic_resolver_diagnostics() -> dict:
    """Resolver snapshot for diagnostics (never forces a probe, never raises)."""
    try:
        from eli.perception.mic_resolver import diagnostics
        return diagnostics()
    except Exception as e:
        return {"error": repr(e)}


def stt_diagnostics() -> dict:
    out = {
        "speech_recognition_imported": sr is not None,
        "speech_recognition_error": repr(_SR_IMPORT_ERROR) if _SR_IMPORT_ERROR else None,
        "mic_device_index_env": os.environ.get("ELI_MIC_DEVICE_INDEX"),
        "mic_resolver": _mic_resolver_diagnostics(),
        "wake_word_disabled": ELI_DISABLE_WAKE_WORD,
        "allow_direct_chat_without_wake": ALLOW_DIRECT_CHAT_WITHOUT_WAKE,
        "min_direct_chat_words": MIN_DIRECT_CHAT_WORDS,
        "wake_arm_timeout": WAKE_ARM_TIMEOUT,
        "wake_duck_enabled": _ELI_DUCK_ENABLED,
        "media_duck_enabled": _media_duck_enabled(),
    }
    try:
        from eli.perception.local_whisper_stt import whisper_status
        out["whisper"] = whisper_status()
    except Exception as exc:
        out["whisper"] = {"error": repr(exc)}
    try:
        from eli.perception.wakeword import get_wake_phrases, is_trained
        out["wake_model_trained"] = is_trained()
        out["wake_phrase"] = (get_wake_phrases() or [""])[0]
    except Exception as exc:
        out["wake_model_trained"] = False
        out["wake_phrase_error"] = repr(exc)
    return out


def process_transcript(text: str):
    try:
        from eli.execution.router_enhanced import route
        from eli.execution.executor_enhanced import execute
        intent = route(text)
        return execute(intent.get("action") or "CHAT", intent.get("args") or {"message": text})
    except Exception as e:
        return {
            "ok": False,
            "action": "VOICE_ROUTE",
            "error": str(e),
            "content": "Voice routing failed",
            "response": "Voice routing failed",
        }


# Bare command verbs that need a target — if heard alone, wait for more
_INCOMPLETE_COMMAND_VERBS = {
    "open", "launch", "start", "run", "execute",
    "close", "quit", "kill",
    "delete", "remove", "move", "copy", "rename",
    "install", "uninstall",
    "go to", "browse", "search",
}

def _is_incomplete_command(text: str) -> bool:
    """Return True if text is a bare command verb without a target."""
    t = _cleanup(text).strip()
    return t in _INCOMPLETE_COMMAND_VERBS


# Words a spoken sentence does not end on: someone who stops after "and", "the" or "because" is
# thinking, not done. In conversation the voice gate waits for the rest (VoiceGate.classify).
_TRAILING_WORDS = {"and", "but", "or", "because", "the", "a", "an", "my", "your", "our", "their",
                   "his", "um", "uh", "erm"}


def _trails_off(text: str) -> bool:
    """What was said stops mid-sentence: on a word above, a comma or a dash, or trailing dots."""
    raw = str(text or "").rstrip()
    if raw.endswith((",", "...", "\u2026", "-", "\u2014")):
        return True
    words = _cleanup(raw).split()
    return bool(words) and words[-1] in _TRAILING_WORDS


# "play … on <service>" — when the service is named, dispatch immediately (router handles it).
_MEDIA_PLAY_ON_SERVICE_RE = re.compile(
    r"\s+on\s+(?:"
    r"spotify|youtube|yt|mpv|soundcloud|"
    r"netflix|net\s*flix|"
    r"prime(?:\s+video)?|primevideo|amazon\s+prime|"
    r"disney(?:\+|\s*plus)?|disneyplus|"
    r"hulu|twitch|"
    r"max|hbo\s+max|paramount(?:\+|\s*plus)?|paramountplus|"
    r"peacock|apple\s+tv|appletv|plex|crunchyroll|discovery\+|discoveryplus|"
    r"tubi|pluto(?:\s+tv)?"
    r")\s*$",
    re.I,
)

_BARE_MEDIA_SERVICE_FRAGMENTS = {
    "spotify", "youtube", "yt", "mpv", "netflix",
    "on spotify", "on youtube", "on yt", "on mpv", "on netflix",
    "prime", "prime video", "on prime", "on prime video",
    "disney", "disney plus", "disney+", "on disney plus",
    "hulu", "on hulu", "twitch", "on twitch", "soundcloud", "on soundcloud",
}

_BARE_MEDIA_SERVICE_PLAY_FRAGMENTS = {
    "spotify play", "youtube play", "yt play", "mpv play", "netflix play",
    "on spotify play", "on youtube play", "on yt play", "on mpv play", "on netflix play",
    "prime play", "on prime play", "on prime video play",
}


# Media phrases often arrive split across STT chunks:
#   "play blood runs cold" + "by jedi mind tricks on spotify"
# Do not dispatch ambiguous play-search fragments immediately.
def _is_potentially_incomplete_media_play(text: str) -> bool:
    t = _eli_media_voice_alias(text or "")
    if not t:
        return False

    # Service-first fragments are incomplete without a query.
    if t in _BARE_MEDIA_SERVICE_FRAGMENTS:
        return True
    if t in _BARE_MEDIA_SERVICE_PLAY_FRAGMENTS:
        return True

    if not t.startswith("play "):
        return False

    # Explicit service means complete enough for router (incl. Netflix/Prime/etc.).
    if _MEDIA_PLAY_ON_SERVICE_RE.search(t):
        return False

    # Dangling connectors are definitely incomplete.
    if t.endswith((" by", " on", " from", " album by", " song by")):
        return True

    # If there is no explicit service, hold it so the user can finish: "by artist on spotify". This
    # prevents accidental YouTube launches. VoiceGate.release_due hands it on as it stands once the
    # user's turn is over.
    return True



# Media voice alias and pending-command guard, before VoiceGate. Fixes short media-service fragments
# Whisper regularly mangles: "algezera on you" -> "al jazeera on youtube", "open alt-0 on youtube"
# -> "open al jazeera on youtube", "party ... on spot" -> "party ... on spotify".
def _media_voice_alias_legacy(text: str) -> str:
    t = _cleanup(text)
    if not t:
        return t

    # common command verb mishear
    t = re.sub(r"^pay\s+", "play ", t)
    t = re.sub(r"^played\s+", "play ", t)
    t = re.sub(r"^opens\s+", "open ", t)

    # "put" -> "play" when it looks like a media search (Whisper often hears "play" as "put" / "put
    # our" / "put a"). Only when followed by a filler article that signals the original was "play";
    # a bare "put X" may be a real system command.
    t = re.sub(r"^put\s+(?:our|a|the)\s+", "play ", t)
    # Also rewrite "put X on spotify/youtube/yt/mpv" unconditionally.
    t = re.sub(
        r"^put\s+(.+?)\s+on\s+(spotify|youtube|yt|mpv)\b",
        lambda m: f"play {m.group(1)} on {m.group(2)}",
        t,
    )

    # Al Jazeera is a brutal one for Whisper/base.en.
    # Keep this intentionally narrow to command/media phrases.
    alj_patterns = [
        r"al\s*jazeera",
        r"al\s*jazerra",
        r"al\s*jazera",
        r"al\s*gezera",
        r"al\s*gezira",
        r"algezera",
        r"algezira",
        r"algazear",
        r"algazare",
        r"algazera",
        r"algazira",
        r"algazir",
        r"algiers\s+era",
        r"all\s+jizz\s*era",
        r"alt\s*[- ]?\s*0",
        r"alt\s+zero",
        r"hours\s+of\s+zero",
        r"i'll\s+just\s+air",
        r"and\s+i'll\s+just\s+air",
    ]
    for pat in alj_patterns:
        t = re.sub(rf"\b{pat}\b", "al jazeera", t)

    # Service aliases: strip junk after a clear service fragment at the end. Rewrite a trailing
    # "on you[tube]" / "on spot[ify]" only when the text is a media-play command, otherwise ordinary
    # talk ("checking up on you", "turned on your network") was corrupted into "...youtube".
    _looks_media_cmd = bool(re.match(
        r"^(play|pay|played|put|open|opens|listen|watch|stream|queue)\b", t
    )) or " play " in f" {t} "
    if _looks_media_cmd:
        t = re.sub(
            r"\bon\s+(?:you|your|youtub|youtube|you\s*tube|yt|your\s+ship)(?:\b.*)?$",
            "on youtube",
            t,
        )
        t = re.sub(
            r"\bon\s+(?:spot|spott|spot\s+of\s*f?|spot\s+if\s+i|spotify|spodify|spotif(?:y)?)(?:\b.*)?$",
            "on spotify",
            t,
        )

    # Standalone service fragment completion used after pending media command.
    # Only unambiguous service words — bare "you"/"your" are normal speech and
    # must never be auto-completed to YouTube.
    if t in {"youtub", "youtube", "you tube", "yt"}:
        return "on youtube"
    if t in {"spot", "spott", "spot of", "spot of f", "spot if i", "spotify", "spodify", "spotif"}:
        return "on spotify"
    if t in {"netflix", "net flix"}:
        return "on netflix"
    if t in {"prime", "prime video", "on prime", "on prime video", "amazon prime"}:
        return "on prime video"
    if t in {"disney", "disney plus", "disney+", "on disney plus"}:
        return "on disney plus"
    if t in {"hulu", "on hulu"}:
        return "on hulu"

    return re.sub(r"\s+", " ", t).strip()


def _eli_pending_media_can_complete(prefix: str, fragment: str) -> bool:
    p = _eli_media_voice_alias(prefix)
    f = _eli_media_voice_alias(fragment)

    # Media play fragments may only be completed by explicit service/artist/service text.
    if p.startswith("play "):
        if re.search(
            r"\bon\s+(?:spotify|youtube|yt|mpv|soundcloud|netflix|net\s*flix|"
            r"prime(?:\s+video)?|primevideo|amazon\s+prime|"
            r"disney(?:\+|\s*plus)?|disneyplus|hulu|twitch)\b",
            f,
        ):
            return True
        if f in _BARE_MEDIA_SERVICE_FRAGMENTS:
            return True
        if re.search(
            r"\bby\s+.+\s+on\s+(?:spotify|youtube|yt|mpv|soundcloud|netflix|"
            r"prime(?:\s+video)?|primevideo|disney(?:\+|\s*plus)?|disneyplus|hulu|twitch)\b",
            f,
        ):
            return True
        return False

    # Non-media incomplete commands like "open" can still take a target.
    return bool(f)


# Internal-prompt echo phrases. When ELI's TTS reads internal prompt content into the mic (or the
# LLM emits scaffolding), STT can capture phrases like "the situation brief and conversation
# history" and try to dispatch them. They're unlikely in real speech; drop them before routing.
_INTERNAL_PROMPT_ECHO_RE = re.compile(
    r"\b("
    r"situation\s+brief|"
    r"conversation\s+history|"
    r"grounding\s+package|"
    r"grounded\s+(?:facts|evidence)|"
    r"recent\s+dialogue|"
    r"final\s+instruction|"
    r"orchestrator\s+observations?|"
    r"intent\s+action|"
    r"user\s+prompt|"
    r"persona\s+handoff|"
    r"agent\s+bus\s+notes?|"
    r"reranked\s+hits"
    r")\b",
    re.IGNORECASE,
)


def is_internal_prompt_echo(text: str) -> bool:
    """True if `text` looks like a leak of ELI's own internal scaffolding."""
    return bool(_INTERNAL_PROMPT_ECHO_RE.search(str(text or "")))


def speaking_lock_held(path) -> bool:
    """Whether ELI's voice holds the speaking lock at `path`. It is held for as long as ELI speaks,
    however long the reply: taken as stale after 30 seconds, a minute-long reply was heard by the mic
    and transcribed half-way through. Stale only when the process that wrote it has ended."""
    from pathlib import Path
    from eli.system.process_guard import pid_alive
    lock = Path(path)
    try:
        owner = int(lock.read_text(encoding="utf-8").strip() or 0)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True       # being written as it is read
    if owner and not pid_alive(owner):
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            log.warning("[AUDIO] a speaking lock left by an ended process could not be removed: %s", lock, exc_info=True)
        return False
    return True


# What the STT voice profile learns from: the utterances the voice gate takes as the owner's.
_VOICE_PROFILE_SOURCE = "accepted utterances"

# ELI's media lowered at the onset of speech its own output does not explain (near_speech), not only
# once the wake word has been transcribed. ELI_STT_ONSET_DUCK=0 keeps the old behaviour.
_ONSET_DUCK = os.environ.get("ELI_STT_ONSET_DUCK", "1").strip().lower() not in ("0", "false", "no", "off")
# The most unread microphone audio kept, seconds: longer than any transcription the loop waits on.
_MIC_BACKLOG_S = 10.0
_DUCK_LOCK = threading.Lock()


class _ContinuousMic:
    """The microphone read without a break. The listen loop reads nothing while Whisper transcribes
    the last capture, and the capture device's small buffer overran meanwhile: what was said then
    ("computer, tell me the time" said in one go, the command while the wake word was being read)
    was lost. A thread reads every chunk as it is captured, hands it to `on_chunk(data, t_end)`
    (near speech, in real time), and keeps it for the loop's read(); flush() drops what the loop
    must not hear (ELI's own voice while it spoke), all of it or what came before a moment (the user
    spoke over ELI then: that is kept for the loop)."""

    def __init__(self, stream, chunk: int, width: int, on_chunk=None):
        self._stream, self._chunk, self._width = stream, int(chunk), int(width)
        self._on_chunk = on_chunk
        self._buf = bytearray()
        self._marks: "deque" = deque()       # (t_end, bytes) of each chunk the buffer holds, oldest first
        self._cv = threading.Condition()
        self._error: Optional[BaseException] = None
        self._stop = threading.Event()
        self._rate = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="eli-mic-reader")
        self._thread.start()

    def bind_rate(self, rate: int) -> "_ContinuousMic":
        self._rate = int(rate)
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._stream.read(self._chunk)
            except Exception as exc:          # the device went away: the loop's read() raises it
                with self._cv:
                    self._error = exc
                    self._cv.notify_all()
                return
            t_end = time.monotonic()
            if self._on_chunk is not None:
                try:
                    self._on_chunk(data, self._rate, t_end)
                except Exception:
                    log.debug("[AUDIO] a microphone chunk was not judged", exc_info=True)
            with self._cv:
                self._buf += data
                self._marks.append([t_end, len(data)])
                most = int(_MIC_BACKLOG_S * max(1, self._rate)) * self._width
                if len(self._buf) > most > 0:
                    self._drop(len(self._buf) - most)
                self._cv.notify_all()

    def _drop(self, n: int) -> None:
        """The oldest `n` bytes go (under the condition's lock)."""
        n = min(int(n), len(self._buf))
        del self._buf[:n]
        while n > 0 and self._marks:
            take = min(n, self._marks[0][1])
            self._marks[0][1] -= take
            n -= take
            if self._marks[0][1] <= 0:
                self._marks.popleft()

    def read(self, size: int) -> bytes:
        want = int(size) * self._width
        with self._cv:
            while len(self._buf) < want and self._error is None and not self._stop.is_set():
                self._cv.wait(timeout=1.0)
            if len(self._buf) < want and self._error is not None:
                raise self._error
            out = bytes(self._buf[:want])
            self._drop(len(out))
            return out

    def flush(self, keep_from: Optional[float] = None) -> None:
        """Drop what the buffer holds; with `keep_from`, only the chunks heard before that moment."""
        with self._cv:
            if keep_from is None:
                self._buf.clear()
                self._marks.clear()
                return
            n = 0
            for t_end, size in self._marks:
                if t_end >= keep_from:
                    break
                n += size
            self._drop(n)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            log.warning("[AUDIO] the microphone reader did not stop; its stream is left open")
            return
        self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class VoiceGate:
    CONTINUATION_SLACK = 1.5
    """A held command is the user's whole turn once they have been quiet for half again as long as
    the slowest pause after which they finished one before ("play X" ... "on spotify")."""
    CONTINUATIONS_KEPT = 8
    """How many of those pauses are remembered: enough to see this person's spread, few enough that
    one long pause long ago stops setting the wait."""

    def __init__(self, wake_timeout_sec: float = WAKE_ARM_TIMEOUT):
        self.wake_timeout_sec = wake_timeout_sec
        self._guarded_until = 0.0
        self._last_wake_ts = 0.0
        self._pending_prefix = ""  # accumulated incomplete command prefix
        self._pending_started = 0.0
        self._released = ""        # a held command a follow-up could not complete, to go on as it stands
        self._continuations: list = []   # seconds from a held command to the words that finished it
        self._inner: list = []           # seconds of the pauses this person made inside a turn and went on from
        self._conversing = False         # conversation mode: every turn is for ELI, no wake word each time
        self._hold_prior = float("inf")

    def set_conversing(self, on: bool, hold_prior_s: float = float("inf")) -> None:
        """Conversation mode on or off. On, the gate stays armed (no wake word for each turn), a
        sentence that trails off is held for the rest of it, and a held one goes on after
        `hold_prior_s` of quiet until this person's own pauses have been seen."""
        self._conversing = bool(on)
        self._hold_prior = float(hold_prior_s)
        if on:
            self._guarded_until = float("inf")
        else:
            self._guarded_until = 0.0
            self.clear()

    @property
    def conversing(self) -> bool:
        return self._conversing

    def note_inner_pause(self, seconds: float) -> None:
        if seconds > 0:
            self._inner = (self._inner + [float(seconds)])[-self.CONTINUATIONS_KEPT:]

    def end_of_phrase_pause(self, prior: float) -> float:
        """The silence that ends what is being said. In conversation, half again as long as the
        longest pause this person recently made and went on from, so a thinking pause is waited
        out; `prior` (the configured pause) until one has been seen, and outside conversation."""
        if not self._conversing or not self._inner:
            return float(prior)
        return max(float(prior), max(self._inner) * self.CONTINUATION_SLACK)

    def _hold(self, text: str) -> None:
        self._pending_prefix = text
        self._pending_started = time.monotonic()

    def _release_for_new_turn(self, said: str) -> None:
        """A new wake word starts a new turn: a command still held goes on as it stands (release_due),
        unless what follows re-says it ("play X" held, then "computer, play X on spotify")."""
        held = self._pending_prefix
        if not held:
            return
        verb = held.split()[0]
        if not (said and said.startswith(verb + " ")):
            self._released = held
        self._pending_prefix = ""
        self._pending_started = 0.0

    def end_of_turn_gap(self) -> float:
        """How long after a held command this user may still finish it, from their own pauses before
        finishing one; until one has been seen, the armed window alone decides."""
        if not self._continuations:
            return self._hold_prior if self._conversing else float("inf")
        return max(self._continuations) * self.CONTINUATION_SLACK

    def release_due(self) -> Optional[str]:
        """The held command, to be handed on as it stands now (the router then chooses the service):
        when the words after it could not complete it, when the user has been quiet past their
        end-of-turn gap, or when the armed window has closed. None while it may still be finished.
        A held "play some jazz" was never released before: it waited for good."""
        if self._released:
            held, self._released = self._released, ""
            return held
        if not self._pending_prefix:
            return None
        if self.armed() and time.monotonic() - self._pending_started < self.end_of_turn_gap():
            return None
        held = self._pending_prefix
        self.clear()
        return held

    def arm(self) -> None:
        self._guarded_until = time.monotonic() + self.wake_timeout_sec
        self._armed_at = time.monotonic()

    def clear(self) -> None:
        self._guarded_until = float("inf") if self._conversing else 0.0
        self._pending_prefix = ""
        self._pending_started = 0.0

    def armed(self) -> bool:
        return time.monotonic() < self._guarded_until

    def touch_arm(self, extra_sec: float = 4.0) -> None:
        """Extend the armed window while the user is still in a ducked wake session."""
        if not self.armed() or self._conversing:
            return
        cap = getattr(self, "_armed_at", 0.0) + (self.wake_timeout_sec * 1.5)
        self._guarded_until = min(time.monotonic() + max(1.0, float(extra_sec)), cap)

    def _strip_leading_wake(self, text: str) -> tuple[Optional[str], str]:
        t = _collapse_repeated_phrase(text)
        for wake in sorted(WAKE_WORDS, key=len, reverse=True):
            if t == wake:
                return wake, ""
            if t.startswith(wake + " "):
                return wake, _cleanup(t[len(wake):])
        return None, t

    def _strip_trailing_wake(self, text: str) -> tuple[str, Optional[str]]:
        """Detect wake word after a command — e.g. 'pause. computer.' while music is up."""
        t = _collapse_repeated_phrase(text)
        for wake in sorted(WAKE_WORDS, key=len, reverse=True):
            for sep in (" ", ". ", ", "):
                suffix = sep + wake
                if t.endswith(suffix) or t.rstrip(".,!?") == wake:
                    if t.rstrip(".,!?") == wake:
                        return "", wake
                    cmd = _cleanup(t[: -len(suffix)])
                    if cmd:
                        return cmd, wake
        return t, None

    def classify(self, raw_text: str) -> tuple[str, Optional[str], Optional[str]]:
        text = _eli_media_voice_alias(_collapse_repeated_phrase(raw_text))
        if not text:
            return "ignore", None, None

        # Drop internal-prompt echoes before any other gate decision.
        if is_internal_prompt_echo(text):
            return "ignore", None, None

        if ELI_DISABLE_WAKE_WORD:
            return "dispatch", text, None

        wake, remainder = self._strip_leading_wake(text)

        # Trailing wake with a safe-direct command ("pause. computer." over music).
        if not wake:
            _cmd, _trail_wake = self._strip_trailing_wake(text)
            if _trail_wake and _cmd and _is_safe_direct(_cmd):
                self.arm()
                return "dispatch", _cmd, _trail_wake

        # Bare wake word: arm guarded window; do not emit to callback.
        if wake and not remainder:
            now = time.time()
            if (now - self._last_wake_ts) < WAKE_DEBOUNCE_S:
                return "ignore", None, wake
            self._last_wake_ts = now
            self._release_for_new_turn("")
            self.arm()
            return "arm", None, wake

        # Wake word plus command in same utterance.
        if wake and remainder:
            self._release_for_new_turn(remainder)
            # Check if remainder is incomplete (e.g., bare "open" without target)
            if _is_incomplete_command(remainder) or _is_potentially_incomplete_media_play(remainder):
                print(f"🔄 [AUDIO] Incomplete command '{remainder}' — waiting for target/service...")
                self._hold(remainder)
                self.arm()  # re-arm to wait for the target/service
                return "arm_incomplete", None, wake
            self.clear()
            return "dispatch", remainder, wake

        # If armed from a prior bare wake word, prepend any pending prefix.
        if self.armed():
            if self._pending_prefix:
                if not _eli_pending_media_can_complete(self._pending_prefix, text):
                    # The words after it do not finish it: the held command goes on as it stands
                    # (release_due) and the fragment is dropped, as before.
                    print(f"🧯 [AUDIO] Dropped non-completing fragment for pending command: '{self._pending_prefix}' + '{text}'")
                    self._released = self._pending_prefix
                    self.clear()
                    return "ignore_unarmed", None, None
                self._continuations = (self._continuations
                                       + [time.monotonic() - self._pending_started])[-self.CONTINUATIONS_KEPT:]

                # If the user re-said the full command (text already self-contained and
                # complete), use text directly rather than prepending the pending prefix
                # — prevents duplication like "play veridian play veridian on spotify".
                _pref_verb = self._pending_prefix.split()[0] if self._pending_prefix else ""
                if _pref_verb and text.startswith(_pref_verb + " ") and not _is_potentially_incomplete_media_play(text):
                    combined = _eli_media_voice_alias(text)
                else:
                    combined = _eli_media_voice_alias(f"{self._pending_prefix} {text}")
                print(f"🔗 [AUDIO] Combined: '{self._pending_prefix}' + '{text}' → '{combined}'")
                self._pending_prefix = ""
                self.clear()
                if self._conversing and _trails_off(raw_text):
                    self._hold(combined)              # still mid-sentence: wait for the rest
                    return "arm_incomplete", None, None
                return "dispatch", combined, None
            # In conversation a guarded command still needs the wake word ("computer, delete it"):
            # a television or another person is in the room as well.
            if self._conversing and _is_guarded_command(text):
                return "guarded_without_wake", None, None
            # Check if this utterance itself is an incomplete command
            if (_is_incomplete_command(text) or _is_potentially_incomplete_media_play(text)
                    or (self._conversing and _trails_off(raw_text))):
                print(f"🔄 [AUDIO] Incomplete command '{text}' — waiting for target/service...")
                self._hold(text)
                # Stay armed, don't clear
                return "arm_incomplete", None, None
            self.clear()
            return "dispatch", text, None

        # Safe direct commands (play/pause/next/volume/etc.) dispatch without wake
        # when the room is quiet. When media is playing, speaker bleed from Netflix
        # / Spotify lyrics can trigger false pauses — require wake (or armed window).
        if _is_safe_direct(text):
            if REQUIRE_WAKE_FOR_SAFE_DIRECT:
                return "ignore_unarmed", None, None
            if _eli_media_probably_audible() and not self.armed():
                return "ignore_unarmed", None, None
            return "dispatch", text, None

        # Guarded commands without wake are ignored.
        if _is_guarded_command(text):
            return "guarded_without_wake", None, None

        # Free-form direct chat without wake is optional. Use a minimum word count
        # so ambient snippets like "mot" or single-noise blips do not trigger the LLM.
        if _allow_direct_chat():
            if _word_count(text) >= MIN_DIRECT_CHAT_WORDS:
                return "dispatch", text, None
            return "ignore_too_short", None, None

        return "ignore_unarmed", None, None


def _resolve_input_device_index() -> Optional[int]:
    """Resolve a capture device that actually delivers audio.

    Delegates to :mod:`eli.perception.mic_resolver`, which probes candidate
    devices (Bluetooth or wired, any platform) in isolated subprocesses and, on
    Linux, pins ELI's process to a working Pulse/PipeWire source via
    ``PULSE_SOURCE`` when the system default source is dead. Honours
    ``ELI_MIC_DEVICE_INDEX`` and ``ELI_MIC_AUTORESOLVE``.

    Returns the PortAudio device index (or None for the OS default) and applies
    any required ``PULSE_SOURCE`` pin as a side effect.
    """
    try:
        from eli.perception.mic_resolver import resolve_capture, live_device_index
    except Exception:
        # Resolver unavailable — fall back to OS default; the calibration
        # watchdog below still guarantees startup cannot hang.
        return None
    try:
        choice = resolve_capture()
    except Exception as e:  # never let mic resolution break STT construction
        log.debug(f"[AUDIO] mic resolver failed ({e}); using OS default device")
        return None
    pulse_override = os.environ.get("ELI_MIC_PULSE_SOURCE", "").strip()
    if pulse_override:
        os.environ["PULSE_SOURCE"] = pulse_override
        log.debug(f"[AUDIO] PULSE_SOURCE override → {pulse_override}")
    elif choice.pulse_source:
        # Pin this process (and the PortAudio stream it opens) to the chosen
        # source without touching the system-wide default.
        os.environ["PULSE_SOURCE"] = choice.pulse_source
        log.debug(f"[AUDIO] PULSE_SOURCE pinned → {choice.pulse_source}")
    # Re-resolve by name against the enumeration this process will open with: PortAudio indices
    # shift between PyAudio instances, so the probed index can be out of range here and silently
    # fall back to a different microphone. See mic_resolver.live_device_index.
    try:
        index = live_device_index(choice)
    except Exception:
        log.debug("[AUDIO] live device re-resolve failed; using probed index", exc_info=True)
        index = choice.device_index
    if index != choice.device_index:
        log.debug(f"[AUDIO] device index re-resolved {choice.device_index} → {index} "
                  f"(name={choice.device_name!r})")
    log.info(f"[AUDIO] capture resolved: {choice.reason}")
    return index


class ELIAudioSTT:
    def __init__(self):
        if sr is None:
            raise RuntimeError(
                "speech_recognition is not installed in this environment. "
                "Install with: pip install SpeechRecognition pyaudio"
            )

        self.recognizer = sr.Recognizer()

        # Wake-word enrollment state (capture the user's own voice through the
        # existing mic loop — no second microphone, no device conflict).
        self._enroll_remaining = 0
        self._enroll_target = 0
        # Generic mic-capture script (wake enrollment, voice training, labelled emotion: one
        # mechanism). Each step is {"prompt": spoken cue, "sink": callable(int16, sr)}; captured
        # clips advance through the script and a 'done' callback runs after the last.
        self._capture_steps: list = []
        self._capture_idx = 0
        self._capture_done = None
        global _ACTIVE_STT
        _ACTIVE_STT = self
        _merge_custom_wake_words()
        # Before any device is resolved: AEC publishes its own cleaned-up source, so it
        # has to exist first or the resolver would pin the raw, bleed-carrying one.
        ensure_echo_cancel()

        # When SpeechRecognition takes speech to have started and stopped. The energy gate is the
        # room's (noise_floor, measured every chunk the mic delivers); the configured start is used
        # only until the room has been heard for non_speaking_duration.
        self.recognizer.energy_threshold = float(os.environ.get("ELI_STT_ENERGY_THRESHOLD", "1200"))
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.pause_threshold = float(os.environ.get("ELI_STT_PAUSE_THRESHOLD", "1.20"))
        self.recognizer.non_speaking_duration = float(
            os.environ.get("ELI_STT_NON_SPEAKING_DURATION", "0.35")
        )
        # Minimum sustained duration (seconds) of above-threshold audio before it
        # counts as a phrase. 0.2s is enough to distinguish a real word from a
        # brief transient; 0.5s was silently dropping short commands like "next".
        self.recognizer.phrase_threshold = float(os.environ.get("ELI_STT_PHRASE_THRESHOLD", "0.2"))
        from eli.perception.noise_floor import NoiseFloor
        self._floor = NoiseFloor()
        self._gate_manual = _manual_gate()

        device_index = _resolve_input_device_index()
        # Every sr.Microphone() builds its own PyAudio instance, and PortAudio prints
        # ~50 lines of ALSA/JACK probe noise each time — a healthy launch looked like a
        # wall of errors. Silence fd 2 for the construction only; always restored.
        _suppress_alsa()
        try:
            if device_index is not None:
                try:
                    self.microphone = sr.Microphone(device_index=device_index)
                    log.info(f"[AUDIO] Using microphone device index {device_index}")
                except Exception as e:
                    log.debug(f"[AUDIO] Failed to use device {device_index}: {e}. Falling back to default mic.")
                    self.microphone = sr.Microphone()
            else:
                self.microphone = sr.Microphone()
        finally:
            _restore_stderr()

        self.is_listening = False
        self.callback: Optional[Callable[[str], None]] = None
        self.audio_queue: queue.Queue[str] = queue.Queue()
        self.last_transcript = ""
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._voice_gate = VoiceGate()
        self._eli_duck_snapshot = None
        self._eli_ignore_until = 0.0
        self._eli_ignore_reason = ""

        # Voice profile state: the level and length of the owner's speech (the diagnostics, and the
        # level the wake clips are checked against).
        self._voice_profile = self._load_voice_profile()
        self._voice_profile_uid = self._voice_profile_user_id()
        log.debug(
            f"[AUDIO] Microphone ready "
            f"(gate {'set by hand at ' + str(self._gate_manual) if self._gate_manual else 'automatic'}, "
            f"voice_profile_n={self._voice_profile.get('count', 0)})"
        )
        self._verify_mic_delivers_audio()

    def _verify_mic_delivers_audio(self) -> None:
        """One-chunk sanity check — catches pulse/speexrate devices that open but stay silent."""
        try:
            import audioop as _aop

            _suppress_alsa()
            try:
                with self.microphone as source:
                    if getattr(source, "stream", None) is None:
                        return
                    buf = source.stream.read(source.CHUNK)
            finally:
                _restore_stderr()
            rms = _aop.rms(buf, source.SAMPLE_WIDTH) if buf else 0
            log.info(
                f"[AUDIO] Mic verify: device_index={getattr(self.microphone, 'device_index', '?')} "
                f"rate={getattr(source, 'SAMPLE_RATE', '?')} peek_rms={rms} "
                f"threshold={self.recognizer.energy_threshold:.0f}",
            )
            if not buf or rms < 30:
                log.warning(
                    "[AUDIO] Microphone opened but delivered silence on the first read. "
                    "Run: python -m eli.tools.mic_diag — then set ELI_MIC_DEVICE_INDEX "
                    "to a live device, or on Linux: "
                    "export PULSE_SOURCE=\"$(pactl get-default-source)\"",
                )
        except Exception:
            log.debug("[AUDIO] Mic verify failed", exc_info=True)

    # ── Hearing the owner over ELI's own media ───────────────────────────
    def _start_near_speech(self) -> None:
        """Read what the default sink plays (near_speech.OutputTap) so speech the output does not
        explain can be told from the music at the mic. Where no recorder reads the sink (no sound
        server, Windows, macOS) ELI's own voice is the output it knows: the voice hands what it
        plays to reference_output. Nothing with ELI_STT_ONSET_DUCK=0."""
        self._near, self._tap = None, None
        if not _ONSET_DUCK:
            return
        try:
            from eli.perception.near_speech import NearSpeech, OutputTap
            near = NearSpeech()
            tap = OutputTap(near.reference)
            self._near, self._tap = near, (tap if tap.start() else None)
        except Exception:
            log.debug("[AUDIO] near-speech detection unavailable", exc_info=True)

    def reference_output(self, samples, rate: int, t_end: float) -> None:
        """What ELI's voice hands the output device, stamped with when it will be heard: the
        output's reference where no recorder reads the sink (with one, the sink's monitor holds it
        already, music included)."""
        near = getattr(self, "_near", None)
        if near is None or getattr(self, "_tap", None) is not None or np is None:
            return
        x = np.asarray(samples, dtype=np.float32).ravel()
        if rate and rate != 16000:
            from eli.perception.audio_samples import resample
            x = resample(x, rate, 16000)
        near.reference(x, t_end)

    # ── Conversation mode: no wake word for each turn, and the user may speak over ELI ────────────
    def set_conversing(self, on: bool, on_barge_in: Optional[Callable[[float], None]] = None,
                       on_barge_in_empty: Optional[Callable[[], None]] = None) -> None:
        """Conversation mode on or off. On, every turn is for ELI (VoiceGate.set_conversing) and
        speech that starts while ELI speaks calls `on_barge_in(t)` at once (the voice falls silent
        there) and is kept for the listener, from its onset; when it held no words,
        `on_barge_in_empty()` (the voice goes on)."""
        from eli.perception.near_speech import BargeIn
        self._on_barge_in = on_barge_in if on else None
        self._on_barge_in_empty = on_barge_in_empty if on else None
        self._barge_at = None
        if on and getattr(self, "_barge", None) is None:
            self._barge = BargeIn()
        hold = float(os.environ.get("ELI_STT_ARMED_PAUSE", "1.4")) * VoiceGate.CONTINUATION_SLACK
        self._voice_gate.set_conversing(on, hold_prior_s=hold)

    def _barge_keep_from(self) -> Optional[float]:
        """Where the audio to keep starts after a barge-in (the onset, less the frames it took to be
        sure of it and the listener's own lead-in), None when there was none."""
        at = getattr(self, "_barge_at", None)
        if at is None:
            return None
        from eli.perception.near_speech import HOP_S, NEED_FRAMES
        lead = NEED_FRAMES * HOP_S + float(getattr(self.recognizer, "non_speaking_duration", 0.0) or 0.0)
        return at - lead

    def _barge_in_came_to_nothing(self) -> None:
        """What silenced ELI held no words (a cough, a door): it is forgotten and ELI goes on."""
        if getattr(self, "_barge_at", None) is None:
            return
        self._barge_at = None
        cb = getattr(self, "_on_barge_in_empty", None)
        if cb is not None:
            try:
                cb()
            except Exception:
                log.debug("[VOICE_BARGE_IN] the voice did not go on", exc_info=True)

    def _stop_near_speech(self) -> None:
        tap = getattr(self, "_tap", None)
        if tap is not None:
            tap.stop()
        self._near, self._tap = None, None

    def _hear_continuously(self, source) -> None:
        """Every opening of the mic is read by a _ContinuousMic (after each enter of the source)."""
        stream = getattr(source, "stream", None)
        if stream is None or isinstance(stream, _ContinuousMic):
            return
        source.stream = _ContinuousMic(stream, source.CHUNK, source.SAMPLE_WIDTH,
                                       self._on_mic_chunk).bind_rate(source.SAMPLE_RATE)

    def _own_voice_now(self) -> bool:
        """ELI is speaking, or its voice's tail is still in the room (the listen loop's drain)."""
        last = getattr(self, "_tts_mon_last", None)
        drain = float(getattr(self, "_post_tts_drain_s", 0.0) or 0.0)
        return bool(last and last[0] > 0 and time.monotonic() - last[0] < drain)

    def _on_mic_chunk(self, data: bytes, rate: int, t_end: float) -> None:
        """Every chunk as it is captured: its level for the room's gate (noise_floor), then speech the
        output does not explain (near_speech)."""
        if np is None or not data:
            return
        pcm = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2")
        if not pcm.size:
            return
        own = self._own_voice_now()
        floor = getattr(self, "_floor", None)
        if floor is not None:
            floor.push(float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))), pcm.size / float(rate or 16000),
                       t_end, own_voice=own, samples=pcm.size)
        near = getattr(self, "_near", None)
        barge = getattr(self, "_on_barge_in", None)
        x = pcm.astype(np.float32) / 32768.0
        if rate and rate != 16000:
            from eli.perception.audio_samples import resample
            x = resample(x, rate, 16000)
        if not own:
            self._keep_room(x, t_end)
        if near is None and barge is None:
            return
        onsets = near.mic(x, t_end) if near is not None else []
        if onsets:
            self._near_onset_at = onsets[-1]
        if barge is not None and own:
            hit = self._barge.judge(x, t_end, near, onsets)
            if hit is not None and getattr(self, "_barge_at", None) is None:
                self._barge_at = hit
                log.debug(f"[VOICE_BARGE_IN] the user spoke over ELI at {hit:.3f}")
                threading.Thread(target=barge, args=(hit,), daemon=True, name="eli-barge-in").start()
            return
        if onsets:
            threading.Thread(target=self._duck_at_onset, daemon=True, name="eli-onset-duck").start()

    # ── The wake word learnt from use (wakeword.learn: clips of confirmed wakes, the rooms they were
    # said in, and training again once enough have gathered) ────────────────────────────────────
    def _keep_room(self, x, t_end: float) -> None:
        """The last _MIC_BACKLOG_S of what the mic heard, ELI's own voice left out: the room a wake
        word was said in, for the wake detector's noise beds."""
        ring = getattr(self, "_room_ring", None)
        if ring is None:
            ring = self._room_ring = deque()
        ring.append((t_end, x))
        while ring and ring[0][0] < t_end - _MIC_BACKLOG_S:
            ring.popleft()

    def _voice_of(self, pcm, sr: int) -> Optional[bool]:
        """Whether a capture is the owner's (or an enrolled user's) voice: True, False for a voice
        that is none of theirs, None when nobody is enrolled (the voice says nothing then)."""
        try:
            from eli.perception import speaker_id
            if not speaker_id.enabled() or not speaker_id.list_speakers():
                return None
            return bool(speaker_id.identify(pcm, sr).get("identified"))
        except Exception:
            log.debug("[WAKE] speaker not identified", exc_info=True)
            return None

    def _wake_candidate(self, audio, span) -> Optional[tuple]:
        """(clip, room) of a wake word just heard, to keep once the command after it is acted on;
        None when it may not be the owner's own wake: ELI was speaking, ELI's media played and the
        mic heard no speech it does not explain (music alone), or the voice is not an enrolled one."""
        if os.environ.get("ELI_WAKE_LEARN", "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        if audio is None or span is None or np is None:
            return None
        start, end = span
        last = getattr(self, "_tts_mon_last", None)
        if last and last[0] >= start - float(getattr(self, "_post_tts_drain_s", 0.0) or 0.0):
            return None
        if _eli_media_probably_audible():
            onset = getattr(self, "_near_onset_at", None)
            if onset is None or not (start - 1.0 <= onset <= end):
                return None
        try:
            raw = audio.get_raw_data()
            rate = int(getattr(audio, "sample_rate", 16000) or 16000)
            pcm = np.frombuffer(raw, dtype=np.int16)
        except Exception:
            log.debug("[WAKE] capture unreadable for learning", exc_info=True)
            return None
        if self._voice_of(pcm, rate) is False:
            return None
        from eli.perception import wakeword as _ww
        clip = _ww.wake_segment(pcm, rate)
        if clip is None:
            return None
        before = [x for t, x in list(getattr(self, "_room_ring", None) or []) if t < start]
        room = np.concatenate(before)[-2 * _ww.CLIP_N:] if before else None
        return clip, room

    def _confirm_wake(self) -> None:
        """The command after a wake word was acted on: its clip and room are kept, and the detector
        is trained again in the background once enough have gathered."""
        pending, self._pending_wake = getattr(self, "_pending_wake", None), None
        if not pending:
            return
        clip, room = pending
        try:
            from eli.perception import wakeword as _ww
            _ww.save_enrollment_clip((clip * 32767).astype(np.int16), sr=_ww.SR)
            if room is not None:
                _ww.save_noise_bed(room, _ww.SR)
            if _ww.retrain_due():
                _ww.retrain_in_background()
        except Exception:
            log.warning("[WAKE] a confirmed wake was not kept", exc_info=True)

    def _duck_at_onset(self) -> None:
        """Speech began that ELI's output does not explain: ELI's media is lowered now, so the rest of
        the wake word and the command after it are heard over quieter music. It goes back up when the
        utterance turns out not to be for ELI (_restore_duck), as after a wake word."""
        with _DUCK_LOCK:
            if self._eli_duck_snapshot is not None or self._voice_gate.armed():
                return
            if not _eli_media_probably_audible():
                return
            snap = _eli_duck_output()
            if snap is None:
                return
            self._eli_duck_snapshot = snap
        near = getattr(self, "_near", None)
        if near is not None:
            near.output_changed(lowered=True)
        log.debug("[AUDIO_DUCK] media lowered at the onset of speech the output does not explain")

    def _restore_duck(self, why: str) -> None:
        with _DUCK_LOCK:
            snap, self._eli_duck_snapshot = self._eli_duck_snapshot, None
        if snap is None:
            return
        try:
            _eli_restore_duck(snap)
            log.debug(f"[AUDIO_DUCK] {why} — volume restored")
        except Exception as e:
            log.debug(f"[AUDIO_DUCK][RESTORE_ERROR] {e}")
        near = getattr(self, "_near", None)
        if near is not None:
            near.output_changed(lowered=False)

    # ── Voice profile learning ───────────────────────────────────────────
    def _voice_profile_user_id(self) -> str:
        try:
            from eli.perception.speaker_id import get_active_speaker
            uid = str((get_active_speaker() or {}).get("user_id") or "").strip()
            if uid:
                return uid
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            from eli.kernel.state import get_active_user_id
            return str(get_active_user_id("") or "").strip()
        except Exception:
            return ""

    def _voice_profile_path(self):
        from pathlib import Path
        uid = self._voice_profile_user_id()
        if uid and uid not in ("", "default-user"):
            try:
                from eli.kernel.state import _user_runtime_dir
                return _user_runtime_dir(uid) / "voice_profile.json"
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        from eli.core.paths import get_paths
        return get_paths().artifacts_dir / "runtime" / "voice_profile.json"

    def _maybe_switch_voice_profile(self, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid or uid == getattr(self, "_voice_profile_uid", ""):
            return
        self._save_voice_profile()
        self._voice_profile_uid = uid
        self._voice_profile = self._load_voice_profile()

    def _load_voice_profile(self) -> dict:
        """The profile at _voice_profile_path. One written before it learnt from the owner's accepted
        utterances alone (no "learned_from") measured every capture, songs included: it starts over."""
        import json
        p = self._voice_profile_path()
        try:
            if p.exists():
                prof = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(prof, dict) and prof.get("learned_from") == _VOICE_PROFILE_SOURCE:
                    return prof
                log.info(f"[AUDIO] voice profile at {p} learnt from every capture, music included "
                         f"({(prof or {}).get('count', 0)} samples); learning it again from the owner's speech")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return {"learned_from": _VOICE_PROFILE_SOURCE, "count": 0, "energy_mean": 0.0, "energy_min": 0.0,
                "energy_max": 0.0, "duration_mean_s": 0.0}

    def _save_voice_profile(self) -> None:
        import json
        p = self._voice_profile_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._voice_profile, indent=2), encoding="utf-8")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _apply_gate(self) -> float:
        """The speech gate for the next capture: the owner's own number when they set one, else the
        room's (noise_floor), else the configured start until the room has been heard."""
        rec = self.recognizer
        manual = getattr(self, "_gate_manual", None)
        floor = getattr(self, "_floor", None)
        gate = manual or (floor.threshold(float(getattr(rec, "non_speaking_duration", 0.0) or 0.0),
                                          *self._phrase_rule()) if floor is not None else None)
        if gate:
            rec.energy_threshold = float(gate)
        return float(rec.energy_threshold)

    def _phrase_rule(self) -> tuple:
        """(phrase_s, gap_s): how long a phrase is, and the silence that ends one, as the listener
        will take them on the next capture."""
        rec = self.recognizer
        gate = getattr(self, "_voice_gate", None)
        gap = float(os.environ.get("ELI_STT_SHORT_PAUSE", "0.5"))
        if gate is not None:
            gap = gate.end_of_phrase_pause(gap)
        return float(getattr(rec, "phrase_threshold", 0.2) or 0.2), gap

    def set_sensitivity(self, automatic: bool, threshold: Optional[float] = None) -> None:
        """The Settings ▸ Audio choice, at once: the room's gate, or the owner's number."""
        self._gate_manual = None if automatic else (float(threshold) if threshold else None)
        self._apply_gate()

    def level_meter(self) -> dict:
        """{level, floor, threshold, manual, near_heard, speaking} for the live meter."""
        floor = getattr(self, "_floor", None)
        out = (dict(floor.snapshot(*self._phrase_rule())) if floor is not None
               else {"level": None, "floor": None, "threshold": None})
        out["manual"] = getattr(self, "_gate_manual", None)
        out["gate"] = float(getattr(self.recognizer, "energy_threshold", 0.0) or 0.0)
        near = getattr(self, "_near", None)
        out["near_heard"] = bool(near is not None and getattr(near, "_heard", False))
        return out

    def _learn_voice(self, audio) -> None:
        """An utterance the gate took as the owner's (the wake word, a command it sent on) goes into
        the voice profile, unless what the mic heard was more than the voice: ELI's media playing, or
        a room clearly louder than the quietest it has been heard (noise_floor). Its level then
        measures the music: learnt from every capture, the profile's 95 samples averaged 9,129 RMS,
        the level measured for the music alone (9,544)."""
        if audio is None:
            return
        floor = getattr(self, "_floor", None)
        if floor is not None and floor.louder_than_usual():
            log.debug("[AUDIO] voice profile not updated: the room is louder than it usually is")
            return
        if _eli_media_probably_audible():
            log.debug("[AUDIO] voice profile not updated: media is playing")
            return
        self._record_voice_sample(audio)

    def _record_voice_sample(self, audio) -> None:
        """Update voice profile statistics from a confirmed user utterance (_learn_voice decides)."""
        try:
            import audioop
            raw = audio.get_raw_data()
            energy = audioop.rms(raw, audio.sample_width)
            duration = len(raw) / float(audio.sample_width * audio.sample_rate)
            n = int(self._voice_profile.get("count", 0)) + 1
            mean = float(self._voice_profile.get("energy_mean", 0.0))
            mn = float(self._voice_profile.get("energy_min", energy))
            mx = float(self._voice_profile.get("energy_max", energy))
            dmean = float(self._voice_profile.get("duration_mean_s", 0.0))
            new_mean = mean + (energy - mean) / n
            new_dmean = dmean + (duration - dmean) / n
            self._voice_profile = {
                "learned_from": _VOICE_PROFILE_SOURCE,
                "count": n,
                "energy_mean": new_mean,
                "energy_min": min(mn, energy) if n > 1 else energy,
                "energy_max": max(mx, energy) if n > 1 else energy,
                "duration_mean_s": new_dmean,
                "last_energy": energy,
                "last_duration_s": duration,
            }
            # Persist every 5 samples to keep IO low.
            if n % 5 == 0:
                self._save_voice_profile()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def start_listening(self, callback=None):
        from eli.plugins.sandbox import enforce
        enforce("microphone", "listen on the microphone")
        with self._state_lock:
            self.callback = callback
            if self._thread and self._thread.is_alive():
                self.is_listening = True
                log.debug("[AUDIO] Already listening")
                return
            self.is_listening = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="ELI-STT")
            self._thread.start()
        log.debug("[AUDIO] Started listening for voice commands")

    def stop_listening(self):
        with self._state_lock:
            self.is_listening = False
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, MAIN_TIMEOUT + 0.5))
        with self._state_lock:
            if self._thread is thread and thread and not thread.is_alive():
                self._thread = None
        log.debug("[AUDIO] Stopped listening")

    def _listen_adaptive_pause(self, source, timeout, phrase_time_limit,
                               short_pause, long_pause, long_after):
        """speech_recognition's listen() with a DURATION-ADAPTIVE end-of-phrase pause.

        Faithful copy of sr.Recognizer._listen (sr 3.16) — same energy detection,
        non-speaking trimming, phrase cap, and WaitTimeoutError, without its dynamic threshold (the
        room's gate is set before each capture, noise_floor) — with ONE change: the silence required
        to END a phrase grows once the utterance gets long. Each pause the user makes and then goes
        on from is told to the voice gate (note_inner_pause), which sets `short_pause` from them.
          • a short command finalises after `short_pause`s of silence (snappy — no
            more 1.4 s wait between commands);
          • a long dictated prompt that has been speaking for `long_after`s needs
            `long_pause`s of silence before it ends, so a natural mid-sentence pause
            no longer cuts it off.
        Stock listen() can't do this because it reads pause_threshold once per phrase.
        """
        import audioop as _audioop, collections as _collections, math as _math
        import speech_recognition as _sr
        rec = self.recognizer
        seconds_per_buffer = float(source.CHUNK) / source.SAMPLE_RATE
        phrase_buffer_count = int(_math.ceil(rec.phrase_threshold / seconds_per_buffer))
        non_speaking_buffer_count = int(_math.ceil(rec.non_speaking_duration / seconds_per_buffer))
        short_count = int(_math.ceil(max(0.05, short_pause) / seconds_per_buffer))
        long_count = int(_math.ceil(max(short_pause, long_pause) / seconds_per_buffer))

        elapsed_time = 0
        buffer = b""
        while True:
            frames = _collections.deque()
            # store audio input until the phrase starts
            while True:
                elapsed_time += seconds_per_buffer
                if timeout and elapsed_time > timeout:
                    raise _sr.WaitTimeoutError("listening timed out while waiting for phrase to start")
                buffer = source.stream.read(source.CHUNK)
                if len(buffer) == 0:
                    break
                frames.append(buffer)
                if len(frames) > non_speaking_buffer_count:
                    frames.popleft()
                energy = _audioop.rms(buffer, source.SAMPLE_WIDTH)
                if energy > rec.energy_threshold:
                    break

            # read audio input until the phrase ends
            pause_count, phrase_count = 0, 0
            phrase_start_time = elapsed_time
            while True:
                elapsed_time += seconds_per_buffer
                if phrase_time_limit and elapsed_time - phrase_start_time > phrase_time_limit:
                    break
                buffer = source.stream.read(source.CHUNK)
                if len(buffer) == 0:
                    break
                frames.append(buffer)
                phrase_count += 1
                energy = _audioop.rms(buffer, source.SAMPLE_WIDTH)
                if energy > rec.energy_threshold:
                    if pause_count:
                        # the user paused and went on: how long this person pauses inside a turn
                        self._voice_gate.note_inner_pause(pause_count * seconds_per_buffer)
                    pause_count = 0
                else:
                    pause_count += 1
                # ── the only behavioural change vs sr: the pause needed to END the
                # phrase depends on how long the user has been speaking.
                speech_elapsed = elapsed_time - phrase_start_time
                cur_pause_count = long_count if speech_elapsed >= long_after else short_count
                if pause_count > cur_pause_count:
                    break

            phrase_count -= pause_count
            if phrase_count >= phrase_buffer_count or len(buffer) == 0:
                break

        for _ in range(max(0, pause_count - non_speaking_buffer_count)):
            if frames:
                frames.pop()
        return _sr.AudioData(b"".join(frames), source.SAMPLE_RATE, source.SAMPLE_WIDTH)

    @staticmethod
    def _flush_mic(source, keep_from: Optional[float] = None) -> None:
        stream = getattr(source, "stream", None)
        if isinstance(stream, _ContinuousMic):
            stream.flush(keep_from=keep_from)

    def _speak_prompt(self, text: str):
        try:
            from eli.perception import tts_router
            tts_router.speak(text)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def begin_capture_script(self, steps: list, done=None, *, prompt_start: str = "") -> dict:
        """Run a capture SCRIPT through the running mic loop: each captured clean clip
        goes to steps[i]['sink'](int16, sr) and the next cue is spoken; done() runs
        once after the last step. Shared by wake enrollment, voice training, and
        labelled-emotion capture — one mechanism, no second microphone."""
        steps = [s for s in (steps or []) if callable(s.get("sink"))]
        if not steps:
            return {"ok": False, "error": "empty capture script"}
        self._capture_steps = steps
        self._capture_idx = 0
        self._capture_done = done
        if prompt_start:
            self._speak_prompt(prompt_start)
        self._speak_prompt(steps[0].get("prompt", "Speak now."))
        return {"ok": True, "samples": len(steps)}

    def listen_once(self, timeout=5) -> str:
        try:
            with self.microphone as source:
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=PHRASE_TIME_LIMIT)
            from eli.perception.local_whisper_stt import transcribe_speech_recognition_audio as _eli_local_stt
            text = _eli_local_stt(audio).strip()
            text = _collapse_repeated_phrase(text, strip_punctuation=False)
            text = _eli_fast_command_alias(text)
            self.last_transcript = text
            return text
        except Exception:
            return ""

    def _recognize(self, audio) -> str:
        try:
            from eli.perception.local_whisper_stt import transcribe_speech_recognition_audio as _eli_local_stt
            raw = (_eli_local_stt(audio) or "").strip()
            return _collapse_repeated_phrase(raw, strip_punctuation=False)
        except Exception as _stt_err:
            log.debug(f"[STT][ERROR] transcription failed: {type(_stt_err).__name__}: {_stt_err}")
            return ""

    def _emit(self, cmd: str):
        cmd = _collapse_repeated_phrase(cmd)
        cmd = _eli_fast_command_alias(cmd)
        if not cmd:
            return

        try:
            from eli.perception.voice_intent import on_partial_transcript
            on_partial_transcript(cmd, is_final=True)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        print(f"🎤 [AUDIO] Command: '{cmd}'")
        self.last_transcript = cmd

        # Feed System 2 so emotion-aware TTS adapts to what the user said (voice path).
        try:
            from eli.cognition import tone_adaptor as _ta
            _ta.note_user_text(cmd)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if self.callback:
            print("   ↳ CALLBACK IS SET, invoking now")
            try:
                self.callback(cmd)
                self._confirm_wake()           # a wake word whose command was acted on is the owner's
            except Exception as e:
                log.debug(f"[AUDIO] Callback error: {e}")
        else:
            print("   ↳ NO CALLBACK SET")

        # Restore output after command dispatch.
        self._restore_duck("command dispatched")

        # Short post-command gate so ELI/media output does not immediately get
        # re-transcribed as the next user command.
        try:
            gate_s = float(__import__("os").environ.get("ELI_STT_POST_COMMAND_ECHO_GATE", "0.5"))
        except Exception:
            gate_s = 0.5
        self._eli_ignore_until = __import__("time").monotonic() + gate_s
        self._eli_ignore_reason = "post_command_echo_gate"
        log.debug(f"[AUDIO_ECHO_GATE] ignoring mic for {gate_s:.1f}s after command dispatch")

    def _act_on_transcript(self, transcript: str, audio=None) -> None:
        """What a heard utterance does: the echo and bleed gates, then the voice gate, and on a
        dispatch the command the gate chose is handed on: the wake word taken off, a held "play X"
        joined to the "on spotify" that finished it, an alias repaired. The raw latest utterance went
        on instead, so "play X" + "on spotify" reached the router as "on spotify" alone. `audio`, the
        capture, goes into the voice profile when the gate takes it as the owner's (_learn_voice)."""
        transcript_display = _eli_fast_command_alias(transcript)
        transcript_route = _collapse_repeated_phrase(transcript_display, strip_punctuation=True)
        # Speaker-bleed filter: when media is audible and the transcript
        # looks like a song-loop capture (high token-repetition density),
        # drop it. Real commands rarely have >60% repetition.
        try:
            _words = transcript_route.split()
            if len(_words) >= 4 and _eli_media_probably_audible():
                _unique = len(set(_words))
                _rep_ratio = 1.0 - (_unique / len(_words))
                if _rep_ratio >= 0.5:
                    _vprint(
                        f"🎵 [AUDIO_BLEED_FILTER] dropped likely-music transcript "
                        f"(rep_ratio={_rep_ratio:.2f}): {transcript_display!r}",
                        flush=True,
                    )
                    return
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _heard_preview = (
            transcript_display if len(transcript_display) <= 80
            else (transcript_display[:77] + "...")
        )
        _vprint(f"👂 [AUDIO] Heard ({len(transcript_display)} chars): '{_heard_preview}'", flush=True)

        # Drop mic input during short post-command cooldown.
        try:
            now_mono = __import__("time").monotonic()
            if now_mono < getattr(self, "_eli_ignore_until", 0.0):
                left = getattr(self, "_eli_ignore_until", 0.0) - now_mono
                _vprint(
                    f"🛡️ [AUDIO_ECHO_GATE] ignored during cooldown "
                    f"({left:.1f}s left): {transcript_display!r}",
                    flush=True,
                )
                return
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Drop obvious assistant/TTS echo.
        try:
            if _eli_echo_like_assistant_output(transcript_route):
                _vprint(f"🛡️ [AUDIO_ECHO_GATE] ignored assistant echo: {transcript_display!r}", flush=True)
                return
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        self.audio_queue.put(transcript_display)

        # A command held for its service ("play some jazz", waiting for "on spotify") goes on as it
        # stands once its time is up, and before anything said later is acted on.
        self._emit_released()
        action, payload, wake = self._voice_gate.classify(transcript_route)
        self._emit_released()

        if action in ("arm", "arm_incomplete", "dispatch"):
            span, self._capture_span = getattr(self, "_capture_span", None), None
            room = getattr(self, "_floor", None)
            if span and room is not None:
                room.mark_speech(*span)            # the owner's words, not the room
            if wake and action in ("arm", "dispatch"):
                self._pending_wake = self._wake_candidate(audio, span)

        # Lowered (at the wake word, or at the onset of this utterance) and nothing for ELI followed
        # (arm → arm_incomplete → the user went silent, or speech that was not to ELI): back up.
        if action not in ("arm", "arm_incomplete", "dispatch") and not self._voice_gate.armed():
            self._restore_duck("no command followed")

        if action == "arm":
            print(f"🔊 [AUDIO] Wake word detected: '{wake}'")
            print("🎧 [AUDIO] Guarded command window armed")
            self._learn_voice(audio)
            try:
                # Only snapshot+duck if not already ducked: a re-arm (the wake word said twice) or a
                # duck at the utterance's onset must NOT overwrite the pre-duck volume with the
                # ducked level. The media is paused either way.
                with _DUCK_LOCK:
                    if self._eli_duck_snapshot is None:
                        self._eli_duck_snapshot = _eli_duck_output()
                    else:
                        log.debug("[AUDIO_DUCK] Already ducked — keeping existing snapshot")
                media_player_duck_pause()
            except Exception as e:
                log.debug(f"[AUDIO_DUCK][ERROR] {e}")
            return

        if action == "arm_incomplete":
            print(f"🔄 [AUDIO] Waiting for command target...")
            # Keep listening — volume already ducked, window already armed
            return

        if action == "guarded_without_wake":
            print("⚠️ [AUDIO] guarded — wake word required", flush=True)
            return

        if action == "ignore_unarmed":
            _vprint("🫥 [AUDIO] ignored — no wake word", flush=True)
            # _vprint is stdout-only and off unless ELI_STT_VERBOSE=1, so a correctly heard command dropped
            # for lack of a wake word left no trace, indistinguishable from a dead microphone (which is how
            # a working mic got reported broken).
            _now_hint = time.monotonic()
            if _now_hint - getattr(self, "_last_wake_hint_ts", 0.0) >= 45.0:
                _pw = primary_wake_word()
                print(
                    f"💡 [AUDIO] Heard speech but no wake word — say "
                    f"{_pw!r} first (e.g. \"{_pw}, pause music\"). "
                    f"Set ELI_DISABLE_WAKE_WORD=1 to disable.",
                    flush=True,
                )
                self._last_wake_hint_ts = _now_hint
            log.debug(
                f"[AUDIO] dropped (no wake word; say {primary_wake_word()!r} first, "
                f"or enable direct chat): {transcript_display!r}"
            )
            return

        if action == "ignore_too_short":
            _vprint(
                f"🫥 [AUDIO] ignored — too short "
                f"({_word_count(transcript_route)} words, need {MIN_DIRECT_CHAT_WORDS})",
                flush=True,
            )
            return

        if action == "ignore":
            return

        if action == "dispatch" and payload:
            # A command the decoder was not sure of is not carried out or chatted about: the
            # window opens again so it can just be said again.
            from eli.perception.local_whisper_stt import heard_clearly
            if not heard_clearly(transcript_display):
                print(f"❓ [AUDIO] not sure of what was said: {transcript_display!r}; say it again",
                      flush=True)
                self._voice_gate.arm()
                _unclear = getattr(self, "on_unclear", None)
                if callable(_unclear):
                    try:
                        _unclear(transcript_display)
                    except RuntimeError:      # the window it reports to has closed
                        log.warning("[AUDIO] the unclear-command notice had nowhere to go", exc_info=True)
                return
            self._learn_voice(audio)
            self._emit(payload)

    def _emit_released(self) -> None:
        held = self._voice_gate.release_due()
        if held:
            print(f"⏱️ [AUDIO] Held command goes on as it stands: '{held}'", flush=True)
            self._emit(held)

    def _listen_loop(self):
        try:
            _suppress_alsa()
            with self.microphone as source:
                _restore_stderr()
                log.debug("[AUDIO] Microphone ready")
                self._start_near_speech()
                self._hear_continuously(source)

                from eli.core.paths import temp_path as _temp_path
                # Must match tts_router's lock default (same process writes it there,
                # reads it here) — this version's own name in the portable temp dir.
                _speaking_lock_path = os.environ.get("ELI_TTS_SPEAKING_LOCK") or str(
                    _temp_path("tts_speaking.lock")
                )

                def _eli_is_speaking() -> bool:
                    try:
                        from eli.perception.voice_intent import is_tts_speaking
                        if is_tts_speaking():
                            return True
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                    return speaking_lock_held(_speaking_lock_path)

                # Background TTS monitor. listen() blocks up to phrase_time_limit so the _eli_is_speaking()
                # guard never runs and TTS audio comes back as a transcript. A daemon thread polls the speaking
                # lock every 0.1s, so after listen() returns we can tell if TTS overlapped and drop the echo.
                import threading as _threading
                _tts_mon_last: list[float] = [0.0]   # shared: last seen active (monotonic)
                _tts_mon_stop = _threading.Event()
                _post_tts_drain_s = float(
                    os.environ.get("ELI_STT_POST_TTS_DRAIN_SEC", "1.5")
                )
                # the mic reader's thread reads these too: ELI's own voice is not the room
                self._tts_mon_last, self._post_tts_drain_s = _tts_mon_last, _post_tts_drain_s

                def _tts_monitor_fn():
                    while not _tts_mon_stop.is_set():
                        try:
                            if _eli_is_speaking():
                                _tts_mon_last[0] = time.monotonic()
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                        time.sleep(0.1)

                _tts_mon_thread = _threading.Thread(
                    target=_tts_monitor_fn, daemon=True, name="eli-tts-monitor"
                )
                _tts_mon_thread.start()

                while self.is_listening and not self._stop_event.is_set():
                    try:
                        # Hard-pause mic capture while ELI's TTS is actively
                        # speaking. Without this, the mic transcribes ELI's
                        # own voice and triggers a chain of repeat commands.
                        if _eli_is_speaking():
                            _tts_mon_last[0] = time.monotonic()
                            # ELI's own voice is not for the mic; what the user said from the moment
                            # they spoke over it is (a barge-in, conversation mode)
                            self._flush_mic(source, keep_from=self._barge_keep_from())
                            time.sleep(0.15)
                            continue

                        # Post-TTS drain cooldown: if TTS ended less than _post_tts_drain_s ago,
                        # skip listen() and let PyAudio's ring buffer cycle out stale TTS frames
                        # before accepting mic input. Override: ELI_STT_POST_TTS_DRAIN_SEC. Not
                        # after a barge-in: the user is speaking, and ELI stopped for them.
                        if _tts_mon_last[0] > 0 and self._barge_keep_from() is None:
                            _drain_elapsed = time.monotonic() - _tts_mon_last[0]
                            if _drain_elapsed < _post_tts_drain_s:
                                self._flush_mic(source)
                                time.sleep(0.15)
                                continue

                        self._listen_count = getattr(self, '_listen_count', 0) + 1
                        if self._listen_count <= 1 or self._listen_count % 20 == 0:
                            _vprint(f"👂 [AUDIO] Listening... (cycle {self._listen_count})")
                        # Wider pause window while armed (multi-word command): the default
                        # pause_threshold (1.20s) is set in __init__ and only adjusted when the gate
                        # is armed so natural pauses don't cut commands short.
                        if self._voice_gate.armed():
                            # Wider pause window while armed — user is mid-command
                            # and natural pauses (e.g. "play ... on spotify") must
                            # not cut the phrase short.
                            self.recognizer.pause_threshold = float(
                                os.environ.get("ELI_STT_ARMED_PAUSE", "1.4")
                            )
                            self.recognizer.non_speaking_duration = float(
                                os.environ.get("ELI_STT_NON_SPEAKING_DURATION", "0.35")
                            )
                            _active_phrase_limit = PHRASE_TIME_LIMIT
                        elif _allow_direct_chat():
                            # Direct listen mode: the user may speak long sentences. 1.5s of silence ends the phrase (short
                            # enough that "pause" or "next" dispatch promptly in mild noise, long enough not to cut natural
                            # pauses); a 20s cap guarantees a submit if noise prevents silence detection.
                            self.recognizer.pause_threshold = float(
                                os.environ.get("ELI_STT_DIRECT_PAUSE", "1.5")
                            )
                            self.recognizer.non_speaking_duration = float(
                                os.environ.get("ELI_STT_NON_SPEAKING_DURATION", "0.35")
                            )
                            _active_phrase_limit = float(
                                os.environ.get("ELI_STT_DIRECT_PHRASE_LIMIT", "20.0")
                            )
                        else:
                            # Tight pause when unarmed: we only listen for the wake word (1-3 words). 0.30s of silence is
                            # enough, and a shorter non_speaking_duration and phrase_time_limit shrink the whisper input for
                            # faster wake detection.
                            self.recognizer.pause_threshold = UNARMED_PAUSE_S
                            self.recognizer.non_speaking_duration = float(
                                os.environ.get("ELI_STT_UNARMED_NON_SPEAKING", "0.15")
                            )
                            _active_phrase_limit = float(
                                os.environ.get("ELI_STT_UNARMED_PHRASE_LIMIT", "3.0")
                            )

                        # The gate the room sets now (noise_floor), or the owner's own number.
                        self._apply_gate()
                        _listen_start = time.monotonic()
                        # After a barge-in the wait is the short one: speech that silenced ELI goes
                        # on at once, and if none follows ELI should not stay silent for long.
                        _active_main_timeout = (
                            ARMED_MAIN_TIMEOUT if self._voice_gate.armed() and self._barge_keep_from() is None
                            else MAIN_TIMEOUT
                        )
                        # Duration-adaptive end-of-phrase pause for command/dictation states. Short commands finish
                        # after ELI_STT_SHORT_PAUSE (0.5s) of silence; past ELI_STT_LONG_AFTER (12s) it waits
                        # ELI_STT_LONG_PAUSE (2s) so a mid-sentence pause doesn't cut you off. Falls back to stock
                        # listen() on error.
                        _adaptive = os.environ.get("ELI_STT_ADAPTIVE_PAUSE", "1").lower() not in {"0", "false", "no", "off"}
                        _adaptive_state = self._voice_gate.armed() or _allow_direct_chat()
                        _used_limit = _active_phrase_limit   # cap the capture ran under
                        if _adaptive and _adaptive_state:
                            _ad_limit = max(_active_phrase_limit, float(os.environ.get("ELI_STT_ADAPTIVE_PHRASE_LIMIT", "45.0")))
                            try:
                                audio = self._listen_adaptive_pause(
                                    source, timeout=_active_main_timeout, phrase_time_limit=_ad_limit,
                                    short_pause=self._voice_gate.end_of_phrase_pause(
                                        float(os.environ.get("ELI_STT_SHORT_PAUSE", "0.5"))),
                                    long_pause=float(os.environ.get("ELI_STT_LONG_PAUSE", "2.0")),
                                    long_after=float(os.environ.get("ELI_STT_LONG_AFTER", "12.0")),
                                )
                                _used_limit = _ad_limit
                            except sr.WaitTimeoutError:
                                raise
                            except Exception as _ad_err:
                                log.debug(f"[AUDIO] adaptive listen failed, using stock listen(): {_ad_err}")
                                audio = self.recognizer.listen(
                                    source, timeout=_active_main_timeout,
                                    phrase_time_limit=_active_phrase_limit,
                                )
                        else:
                            audio = self.recognizer.listen(
                                source, timeout=_active_main_timeout,
                                phrase_time_limit=_active_phrase_limit,
                            )
                    except sr.WaitTimeoutError:
                        self._barge_in_came_to_nothing()
                        self._emit_released()       # quiet: a held command whose time is up goes on
                        if self._voice_gate.armed() and self._eli_duck_snapshot is not None:
                            self._voice_gate.touch_arm()
                        # Restore duck if the gate expired while we were waiting
                        # for the user to speak (covers arm_incomplete → silence → timeout).
                        if not self._voice_gate.armed():
                            self._restore_duck("gate expired (no speech)")
                            self._pending_wake = None    # no command followed the wake word
                        if _STT_VERBOSE and getattr(source, "stream", None) is not None:
                            try:
                                import audioop as _aop
                                _peek = source.stream.read(source.CHUNK)
                                _prms = _aop.rms(_peek, source.SAMPLE_WIDTH) if _peek else 0
                                if self._listen_count % 10 == 0:
                                    print(
                                        f"🔇 [AUDIO] listen timeout — peek RMS={_prms} "
                                        f"threshold={self.recognizer.energy_threshold:.0f}",
                                        flush=True,
                                    )
                                if not _peek or _prms == 0:
                                    self._dead_stream_streak = getattr(self, "_dead_stream_streak", 0) + 1
                                else:
                                    self._dead_stream_streak = 0
                                if self._dead_stream_streak >= 5:
                                    print(
                                        "⚠️ [AUDIO] Mic stream is silent — re-opening capture. "
                                        "Try: export ELI_MIC_PULSE_SOURCE=\"$(pactl get-default-source)\"",
                                        flush=True,
                                    )
                                    try:
                                        source.__exit__(None, None, None)
                                        source.__enter__()
                                        self._hear_continuously(source)
                                        self._dead_stream_streak = 0
                                    except Exception as _reopen_err:
                                        log.debug(f"[AUDIO] mic re-open failed: {_reopen_err}")
                            except Exception:
                                log.debug("suppressed exception", exc_info=True)
                        continue
                    except Exception as e:
                        if not self.is_listening or self._stop_event.is_set():
                            break
                        # Shared-microphone recovery. listen_once() and calibration `with self.microphone` on the same
                        # object, and __exit__ sets stream = None under this loop. Every later listen() then raised
                        # "Audio source must be entered" at full speed and pinned a core. Re-enter the source instead.
                        if getattr(source, "stream", None) is None:
                            try:
                                source.__enter__()
                                self._hear_continuously(source)
                                log.debug("[AUDIO] mic stream was closed by another "
                                          "capture path — re-entered")
                                continue
                            except Exception as _reenter_err:
                                log.debug(f"[AUDIO] mic re-enter failed: {_reenter_err}")
                                time.sleep(0.5)
                                continue
                        # Rate-limit the generic error so a persistent fault can
                        # never bury the log (or the disk) again.
                        _now_err = time.monotonic()
                        self._listen_err_count = getattr(self, "_listen_err_count", 0) + 1
                        if _now_err - getattr(self, "_listen_err_last_log", 0.0) >= 5.0:
                            _n = self._listen_err_count
                            log.debug(f"[AUDIO] Listen error: {e}"
                                      + (f" (×{_n} in the last 5s)" if _n > 1 else ""))
                            self._listen_err_last_log = _now_err
                            self._listen_err_count = 0
                        time.sleep(0.1)
                        continue

                    transcript = self._recognize(audio)
                    # The capture's span, for the room's gate: marked as the owner's speech, not the
                    # room, once the voice gate takes it as theirs (_act_on_transcript). A capture
                    # that ran to its cap never ended in silence: it is the room, whatever words the
                    # music or the television put in it.
                    try:
                        _cap_s = len(audio.get_raw_data()) / float(audio.sample_width * audio.sample_rate)
                    except Exception:
                        _cap_s = 0.0
                    self._capture_span = (None if _used_limit and _cap_s >= float(_used_limit) * 0.95
                                          else (_listen_start, time.monotonic()))

                    # Acoustic wake-word detector (self-trained, robust over music). When unarmed and a local model
                    # is trained, score the captured audio directly: it catches "computer" even when whisper
                    # transcribed the music instead. Inject the wake word so VoiceGate arms naturally. No model is a
                    # no-op, errors are ignored. ELI_WAKE_ACOUSTIC=0 disables it.
                    if (os.environ.get("ELI_WAKE_ACOUSTIC", "1").lower() not in {"0", "false", "no", "off"}
                            and not self._voice_gate.armed()):
                        try:
                            from eli.perception import wakeword as _ww
                            _wd = _ww.get_detector()
                            if _wd is not None:
                                _raw = audio.get_raw_data()
                                _sw = int(getattr(audio, "sample_width", 2) or 2)
                                _asr = int(getattr(audio, "sample_rate", 16000) or 16000)
                                if _sw == 2 and _raw:
                                    _pcm = np.frombuffer(_raw, dtype=np.int16)
                                    if _asr != 16000 and len(_pcm) > 1:
                                        _pcm = (_ww._resample(_pcm.astype("float32") / 32768.0, _asr, 16000) * 32767).astype("int16")
                                    # the voice as a second signal: the owner's wake at a lower
                                    # score, a television's or a stranger's at a higher one
                                    if _wd.is_wake(_pcm, self._voice_of(_pcm, 16000)):
                                        _low = (transcript or "").lower()
                                        if not any(_low.startswith(_w) for _w in WAKE_WORDS):
                                            transcript = (primary_wake_word() + " " + (transcript or "")).strip()
                                            _vprint("🔊 [AUDIO] acoustic wake-word detected (over noise/music)", flush=True)
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)

                    if not transcript:
                        self._barge_in_came_to_nothing()
                        continue

                    # TTS window overlap check. The background monitor stamps _tts_mon_last[0] whenever TTS is
                    # active; if that stamp is at or after _listen_start (0.2s pre-roll tolerance), TTS was playing
                    # with the mic open and the transcript is ELI's own voice. Drop it and restart the drain
                    # cooldown so the next listen() waits for fresh frames.
                    _barged = self._barge_keep_from() is not None
                    self._barge_at = None                     # what was said over ELI has been heard
                    if _tts_mon_last[0] >= _listen_start - 0.2 and not _barged:
                        _tts_mon_last[0] = time.monotonic()   # restart drain timer
                        _vprint(
                            f"🔇 [AUDIO_ECHO_GATE] TTS active during listen window — dropped: "
                            f"{transcript!r}",
                            flush=True,
                        )
                        continue

                    # If TTS is active, allow barge-in commands through; drop echo otherwise.
                    if _eli_is_speaking() and not _barged:
                        try:
                            from eli.perception.voice_intent import on_partial_transcript
                            _pre = _collapse_repeated_phrase(
                                _eli_fast_command_alias(transcript), strip_punctuation=True,
                            )
                            _vi = on_partial_transcript(_pre, is_final=True)
                            if not _vi.should_barge_in:
                                _vprint(
                                    f"🔇 [AUDIO_ECHO_GATE] dropped transcript captured during TTS: {transcript!r}",
                                    flush=True,
                                )
                                continue
                            _vprint(
                                f"🛑 [VOICE_BARGE_IN] interrupting TTS: {transcript!r}",
                                flush=True,
                            )
                        except Exception:
                            _vprint(
                                f"🔇 [AUDIO_ECHO_GATE] dropped transcript captured during TTS: {transcript!r}",
                                flush=True,
                            )
                            continue
                    # Mic-capture script (wake enrollment, voice training, emotion). Past the TTS-echo guards this
                    # is clean user speech: each clip feeds the current step's sink, then the next cue is spoken;
                    # after the last step the 'done' callback runs in the background (retrain the wake model, build
                    # the voice/emotion profile). Skips command routing.
                    if getattr(self, "_capture_steps", None) and self._capture_idx < len(self._capture_steps):
                        try:
                            _raw = audio.get_raw_data()
                            _asr = int(getattr(audio, "sample_rate", 16000) or 16000)
                            if int(getattr(audio, "sample_width", 2) or 2) == 2 and _raw:
                                _pcm = np.frombuffer(_raw, dtype=np.int16)
                                _step = self._capture_steps[self._capture_idx]
                                try:
                                    _step["sink"](_pcm, _asr)
                                except Exception as _sk:
                                    log.debug(f"[CAPTURE] sink failed: {_sk}")
                                self._capture_idx += 1
                                if self._capture_idx < len(self._capture_steps):
                                    self._speak_prompt(self._capture_steps[self._capture_idx].get("prompt", "Again."))
                                else:
                                    _done = self._capture_done
                                    self._capture_steps = []
                                    self._capture_done = None
                                    self._speak_prompt("Thanks. Working on it now.")
                                    if _done:
                                        import threading as _th_cap
                                        _th_cap.Thread(target=_done, daemon=True,
                                                       name="eli-capture-done").start()
                        except Exception as _cap_err:
                            log.debug(f"[CAPTURE] capture failed: {_cap_err}")
                        continue

                    # Publish the user's vocal tone for cognition in real time: on a genuine command, read
                    # prosody/emotion from this audio and publish it on a side channel the engine reads to adapt its
                    # reply (no callback change). Cheap (numpy), best effort.
                    if os.environ.get("ELI_VOICE_TONE", "1").lower() not in {"0", "false", "no", "off"}:
                        try:
                            _raw = audio.get_raw_data()
                            _asr = int(getattr(audio, "sample_rate", 16000) or 16000)
                            if int(getattr(audio, "sample_width", 2) or 2) == 2 and _raw and len(_raw) > 4000:
                                import numpy as _np_tone
                                from eli.perception import voice_profile as _vp
                                _pcm = _np_tone.frombuffer(_raw, dtype=_np_tone.int16)
                                # Speaker ID first — scopes tone/emotion to the right user
                                try:
                                    from eli.perception.speaker_id import identify, gate_unknown
                                    _sid = identify(_pcm, _asr)
                                    if _sid.get("identified") and _sid.get("user_id"):
                                        self._maybe_switch_voice_profile(_sid["user_id"])
                                    if gate_unknown() and not _sid.get("identified"):
                                        _vprint(
                                            f"🔒 [SPEAKER_GATE] unknown voice — command dropped: "
                                            f"{transcript!r}",
                                            flush=True,
                                        )
                                        continue
                                except Exception:
                                    log.debug("suppressed exception", exc_info=True)
                                _tone = _vp.classify_tone(_pcm, sr=_asr)
                                if _tone.get("ok"):
                                    _vp.set_last_tone(_tone)
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)

                    # The voice profile learns from what is taken as the owner's (_act_on_transcript), not
                    # from every capture: recorded here, it learnt the songs the mic transcribed.
                    self._act_on_transcript(transcript, audio)
                    continue
        finally:
            try:
                _tts_mon_stop.set()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            self._stop_near_speech()
            with self._state_lock:
                self.is_listening = False
                self._thread = None


_AUDIO_STT: Optional[ELIAudioSTT] = None


def get_audio_stt():
    global _AUDIO_STT
    if _AUDIO_STT is None:
        _AUDIO_STT = ELIAudioSTT()
    return _AUDIO_STT


def _get_recognizer():
    """Return the active sr.Recognizer if STT is running, else None."""
    if _AUDIO_STT is not None:
        return _AUDIO_STT.recognizer
    return None


def start_audio_listening(callback=None):
    try:
        get_audio_stt().start_listening(callback)
        return True
    except Exception as e:
        log.debug(f"[STT] start failed: {e}")
        return False


def stop_audio_listening():
    global _AUDIO_STT
    if _AUDIO_STT is not None:
        _AUDIO_STT.stop_listening()
    return True


def listen_for_command(timeout=5):
    from eli.plugins.sandbox import enforce
    enforce("microphone", "listen on the microphone")
    try:
        return get_audio_stt().listen_once(timeout)
    except Exception:
        return ""

# Noise-alias hardening: keep useful normalisation but block dangerous garbage aliases ("valium up"
# / "value mode" / "follow him up" -> "volume up"). They are common TV/music false positives and
# must not become commands.

_LEGACY_MEDIA_VOICE_ALIAS = globals().get("_media_voice_alias_legacy")

def _audio_compact(text):
    return " ".join(str(text or "").lower().replace("-", " ").replace("_", " ").split())

def _audio_noise_alias(text):
    c = _audio_compact(text)
    exact = {
        "valium up", "valium op", "valium mop", "valiumo", "valiumoth",
        "value up", "value op", "value more", "value mode",
        "volume mode",
        "follow him up", "follow him down", "follow mo", "follow more",
        "vanya mok", "violin up",
        "i'll find you more", "i'll tell you more", "i'll use more",
        "fire them up", "firemup", "for you more",
        "the volume up",
    }
    if c in exact:
        return True
    return any(frag in c for frag in (
        "valium", "follow him", "vanya mok", "violin up"
    ))

def _eli_media_voice_alias(text: str) -> str:
    raw = str(text or "")
    compact = _audio_compact(raw)

    canonical = {
        "volume up": "volume up",
        "volume down": "volume down",
        "vol up": "volume up",
        "vol down": "volume down",
        "mute": "mute",
        "unmute": "unmute",
        "play": "play",
        "pause": "pause",
        "resume": "resume",
        "stop": "stop",
        "next": "next",
        "previous": "previous",
    }

    if compact in canonical:
        mapped = canonical[compact]
        if mapped != raw.strip().lower():
            log.debug(f"[STT_ALIAS] {raw!r} -> {mapped!r}")
        return mapped

    if _audio_noise_alias(raw):
        log.debug(f"[STT_ALIAS_BLOCKED_FINAL] {raw!r} not converted")
        return raw

    if callable(_LEGACY_MEDIA_VOICE_ALIAS):
        try:
            mapped = str(_LEGACY_MEDIA_VOICE_ALIAS(raw) or raw)
            mapped_compact = _audio_compact(mapped)

            if _audio_noise_alias(raw) and mapped_compact in {
                "volume up", "volume down", "mute", "unmute",
                "play", "pause", "resume", "stop", "next", "previous"
            }:
                log.debug(f"[STT_ALIAS_BLOCKED_FINAL] old alias tried {raw!r} -> {mapped!r}")
                return raw

            return mapped
        except Exception as e:
            log.debug(f"[STT_ALIAS_BLOCKED_FINAL] alias error: {e}")
            return raw

    return raw

