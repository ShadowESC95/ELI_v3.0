"""Conversation mode: replies spoken as they are written, the user speaking over ELI, pausing it,
changing its voice, and the turn-taking that needs no wake word.

Everything here is a fixture: the voice is a stand-in that takes as long to render as Kokoro does on
an i7-8700 (0.43 of real time, measured), the sound device is a stand-in that plays at real speed
(scaled down so the tests are quick), the model is a stream of tokens at a fixed rate, and the
microphone and the sink are synthesised sound (tests/_audio_fixtures). Nothing is played, recorded
or rendered by a real model.
"""
from __future__ import annotations

import threading
import time
import types

import numpy as np
import pytest

from eli.perception import audio_stt, conversation, speech_stream
from eli.perception.speech_stream import Clauses, SpeechStream, _Queue
from tests import _audio_fixtures as fx

# ── the voice, the device and the model, as fixtures ──────────────────────────
SCALE = 0.1                 # every fixture second lasts a tenth of one
RTF = 0.43                  # Kokoro on an i7-8700: seconds of rendering per second of speech
OVERHEAD_S = 0.15           # each piece's own start-up (phonemes, the ONNX call)
SPEECH_S_PER_CHAR = 0.065   # about 15 characters a second, a natural speaking rate
RATE = 8000


class FakeOutput:
    """A sound device that plays the queue at real speed, block by block (the voice's audio is
    SCALE times shorter, so a fixture second lasts a tenth of one), noting each block it had to fill
    with silence while the reply still had something to say."""

    def __init__(self, block_s=0.02):
        self.q = _Queue(RATE)
        self.block = int(RATE * block_s * SCALE)
        self.latency_s = block_s
        self.gaps = 0
        self._stop = threading.Event()

    def open(self):
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        while not self._stop.is_set():
            with self.q.lock:
                waiting = sum(len(s.samples) - s.pos for s in self.q.segments)
                playing = self.q.first_at is not None and not self.q.paused and not self.q.stopped
                finished = self.q.finished
            block = self.q.pull(self.block)
            if playing and not finished and waiting < self.block and not block.all():
                self.gaps += 1
            time.sleep(self.block / RATE)

    def close(self):
        self._stop.set()


def fake_render(text):
    audio_s = len(text) * SPEECH_S_PER_CHAR
    time.sleep((OVERHEAD_S + RTF * audio_s) * SCALE)
    return np.full(int(audio_s * SCALE * RATE), 0.1, dtype=np.float32), RATE


def model_stream(reply, *, first_token_s, tokens_per_s):
    """The reply as a local model writes it: its first token after `first_token_s`, then a token
    (about four characters) every 1/tokens_per_s."""
    tokens = [reply[i:i + 4] for i in range(0, len(reply), 4)]
    time.sleep(first_token_s * SCALE)
    for tok in tokens:
        yield tok
        time.sleep(SCALE / tokens_per_s)


SHORT = "Sure, it's ten past three."
LONG = ("Right, so the short answer is yes. The longer one is that it depends on how much memory the "
        "card has free, because the voice model and the language model share it. If the card is "
        "nearly full, the voice stays on the processor, which is slower but never runs out.")


def _first_sentence(text):
    import re
    m = re.search(r"[.!?](?=\s|$)", text)
    return text[:m.end()] if m else text


def _whole_then_spoken(reply, first_token_s, tokens_per_s):
    """The earlier path, in fixture seconds: the whole reply written, then its first sentence rendered."""
    n_tokens = -(-len(reply) // 4)
    first = _first_sentence(reply)
    return first_token_s + n_tokens / tokens_per_s + OVERHEAD_S + RTF * len(first) * SPEECH_S_PER_CHAR


def _streamed(reply, first_token_s, tokens_per_s):
    out = FakeOutput()
    stream = SpeechStream(render=fake_render, output=out, hold_lock=False)
    t0 = time.monotonic()
    for tok in model_stream(reply, first_token_s=first_token_s, tokens_per_s=tokens_per_s):
        stream.feed(tok)
    stream.finish()
    assert stream.wait(timeout=60)
    out.close()
    first = (out.q.first_at - t0) / SCALE
    return first, out.gaps, stream.stats(), stream.heard()


@pytest.mark.parametrize("reply", [SHORT, LONG], ids=["short", "long"])
def test_the_first_words_are_heard_while_the_reply_is_still_being_written(reply):
    """Measured on the fixtures (a model writing 12 tokens a second after 0.8 s; Kokoro's measured
    speed): the first audio comes once the first clause is written and rendered, not after the
    whole reply; after it, playback never waits for rendering."""
    first, gaps, stats, heard = _streamed(reply, first_token_s=0.8, tokens_per_s=12)
    before = _whole_then_spoken(reply, 0.8, 12)
    print(f"[FIRST_AUDIO] {len(reply)} chars: {first:.2f} s streamed clause by clause, {before:.2f} s "
          f"written whole then spoken; {stats['pieces']} pieces, {stats['joins_inside_sentences']} joins "
          f"inside a sentence, {gaps} gaps")
    # a one-clause reply is written about as soon as its first clause: the gain is in longer ones
    assert first < before if len(reply) > 2 * len(_first_sentence(reply)) else first <= before + 0.1
    assert gaps == 0, "playback waited for rendering"
    assert heard == speech_stream.speakable(reply).strip()


def test_pieces_grow_once_audio_is_waiting_and_sentences_are_kept_whole():
    c = Clauses(max_chars=360)
    c.feed("Well, I checked the logs. The service restarted twice, both times at night. ")
    assert c.take(None) == ("Well,", False)              # nothing playing: the first clause, at once
    assert c.take(lambda n: n <= 200) == (
        "I checked the logs. The service restarted twice, both times at night.", True)
    c.feed("Dr. Lee said so. ```python\nprint('hi')\n``` and more")
    c.finish()
    assert c.take(lambda n: n <= 200) == ("Dr. Lee said so.", True)   # an abbreviation is not an end
    assert c.take(lambda n: n <= 200) is None and c.finished          # nothing from the code on is read


def test_a_clause_too_long_to_render_in_time_is_cut_at_a_word_and_never_into_crumbs():
    c = Clauses(max_chars=360)
    c.feed("the longer one is that it depends on how much memory the card has free, because ")
    piece, ends = c.take(lambda n: n <= 30)
    assert not ends and len(piece) <= 30 and piece.endswith("it")
    c2 = Clauses(max_chars=360)
    c2.feed("so. the short answer is yes. and that is all of it. ")
    assert c2.take(lambda n: False) == ("so. the short answer is yes. and that is all of it.", True)


def test_markup_is_not_read_and_a_long_run_without_marks_is_cut_at_a_word():
    c = Clauses(max_chars=40)
    c.feed("**Note** the " + "word " * 20)
    piece, _ = c.take(None)
    assert "*" not in piece and len(piece) <= 40 and not piece.endswith(" wor")


def test_pause_holds_the_place_resume_goes_on_and_stop_drops_the_rest():
    out = FakeOutput()
    stream = SpeechStream(render=fake_render, output=out, hold_lock=False)
    stream.feed(LONG)
    stream.finish()
    deadline = time.monotonic() + 10
    while out.q.first_at is None and time.monotonic() < deadline:
        time.sleep(0.01)
    stream.pause()
    held = stream.heard()
    time.sleep(0.3)
    assert stream.heard() == held, "paused, but the voice went on"
    stream.resume()
    time.sleep(0.3)
    assert len(stream.heard()) > len(held)
    cut = stream.stop()
    assert cut and LONG.startswith(cut.split(" ")[0]) and cut != speech_stream.speakable(LONG).strip()
    assert stream.wait(timeout=5)
    out.close()


# ── speaking over ELI ─────────────────────────────────────────────────────────
_ELI_F0, _USER_F0 = 196.0, 118.0


def _eli_voice(seconds, seed):
    return fx.voice(np.random.default_rng(seed), seconds, _ELI_F0, (0.12, 0.2), formant_scale=1.1)


def _judge(output, mic, *, chunk=1024, monitor=True, start=0.0):
    """BargeIn over a NearSpeech fed the sink (or, without a monitor, nothing but ELI's own output),
    chunk by chunk; the onsets it reported."""
    from eli.perception.near_speech import BargeIn, NearSpeech
    near, barge, hits = NearSpeech(), BargeIn(), []
    for pos in range(0, len(mic) - chunk, chunk):
        t = (pos + chunk) / fx.SR
        near.reference(output[pos:pos + chunk], t)
        onsets = near.mic(mic[pos:pos + chunk], t)
        hit = barge.judge(mic[pos:pos + chunk], t, near, onsets)
        if hit is not None and hit >= start:
            hits.append(hit)
    return hits


def _barge_in_case(output, room, user_db, seed, starts):
    """ELI's output through the room, the user speaking `user_db` relative to it at `starts`; the
    utterances found and how long after each began, and false onsets outside them."""
    rng = np.random.default_rng(seed + 50)
    speech = np.zeros(len(room))
    for s in starts:
        u = fx.voice(rng, 1.6, _USER_F0, (0.15, 0.25))
        speech[int(s * fx.SR):int(s * fx.SR) + len(u)] = u
    present = speech != 0
    gain = np.sqrt(np.mean(room ** 2)) * 10 ** (user_db / 20) / np.sqrt(np.mean(speech[present] ** 2))
    hits = _judge(output, room + gain * speech + 1e-3 * rng.standard_normal(len(room)), start=starts[0] - 1)
    found, delays = 0, []
    heard_for = 1.6 + 0.4                     # the utterance and the room's tail after it
    for s in starts:
        mine = [h for h in hits if s <= h <= s + heard_for]
        if mine:
            found += 1
            delays.append(mine[0] - s)
    false = [h for h in hits if not any(s <= h <= s + heard_for for s in starts)]
    return found, delays, false


def test_the_user_is_heard_over_elis_own_voice_on_speakers():
    """Measured: ELI speaking through speakers and a room, the user 3 dB below ELI's level at the
    mic; found once the output's share at the mic has been learnt, and how long after the first
    syllable. ELI stops within one output block of that."""
    found = said = 0
    delays = []
    for seed in (1, 2):
        out = _eli_voice(42.0, seed)
        room = fx.through_the_room(out, seed)
        starts = [20.0, 25.0, 30.0, 35.0]
        f, d, false = _barge_in_case(out, room, -3, seed, starts)
        found, said, delays = found + f, said + len(starts), delays + d
        assert not false, f"onsets where the user said nothing: {false}"
    print(f"[BARGE_IN] speakers, user 3 dB below ELI: {found}/{said} found, reaction "
          f"{np.median(delays) * 1000:.0f} ms median, {max(delays) * 1000:.0f} ms worst")
    assert found >= said * 0.7
    assert np.median(delays) < 0.4


def test_elis_own_voice_and_music_do_not_interrupt_it():
    """Measured false interrupts per minute: ELI's voice alone, and ELI's voice over music, both
    on speakers, once learnt; and through headphones (the mic hears only the room)."""
    rates, seconds = {}, 50.0
    for name, make in (("ELI's voice", lambda s: _eli_voice(seconds, s)),
                       ("ELI's voice over music", lambda s: _eli_voice(seconds, s) + fx.music(seconds, s + 7))):
        out = make(3)
        mic = fx.through_the_room(out, 3) + 1e-3 * np.random.default_rng(3).standard_normal(len(out))
        hits = [h for h in _judge(out, mic) if h > 20.0]
        rates[name] = len(hits) / ((seconds - 20.0) / 60.0)
    rng = np.random.default_rng(4)
    room = fx.fan(seconds, 4) * 0.01
    hits = [h for h in _judge(_eli_voice(seconds, 4), room + 1e-3 * rng.standard_normal(len(room))) if h > 20.0]
    rates["headphones"] = len(hits) / ((seconds - 20.0) / 60.0)
    print("[FALSE_INTERRUPT] per minute: " + ", ".join(f"{k} {v:.1f}" for k, v in rates.items()))
    assert all(v <= 0.6 for v in rates.values()), rates


def test_with_headphones_the_users_voice_over_the_room_is_enough():
    """The mic does not hear ELI (headphones): the user over a quiet room is found by level alone."""
    rng = np.random.default_rng(6)
    out = _eli_voice(26.0, 6)
    room = fx.fan(26.0, 6) * 0.02
    speech = np.zeros(len(room))
    starts = [8.0, 13.0, 18.0, 23.0]
    for s in starts:
        u = fx.voice(rng, 1.6, _USER_F0, (0.15, 0.25)) * 0.2
        speech[int(s * fx.SR):int(s * fx.SR) + len(u)] = u
    hits = _judge(out, room + speech)
    found = [min((h - s for h in hits if s <= h <= s + 1.6), default=None) for s in starts]
    delays = [d for d in found if d is not None]
    print(f"[BARGE_IN] headphones: {len(delays)}/{len(starts)} found, reaction {np.median(delays) * 1000:.0f} ms median")
    assert len(delays) >= len(starts) - 1 and np.median(delays) < 0.4
    assert not [h for h in hits if not any(s <= h <= s + 1.6 for s in starts)]


# ── the listener: kept audio, the gate in conversation ───────────────────────
def test_what_the_user_said_over_elis_voice_is_kept_from_its_onset():
    class _Device:
        def __init__(self):
            self.n = 0

        def read(self, frames):
            time.sleep(0.003)
            self.n += 1
            return bytes([self.n % 256]) * (frames * 2)

        def close(self):
            pass

    mic = audio_stt._ContinuousMic(_Device(), 4, 2).bind_rate(16000)
    time.sleep(0.2)
    with mic._cv:
        marks = [m[0] for m in mic._marks]
    keep_from = marks[len(marks) // 2]
    mic.flush(keep_from=keep_from)
    with mic._cv:
        assert mic._marks and mic._marks[0][0] >= keep_from
        assert len(mic._buf) == sum(m[1] for m in mic._marks)
    mic.close()


def test_in_conversation_every_turn_is_for_eli_and_a_sentence_left_hanging_is_waited_for():
    gate = audio_stt.VoiceGate()
    gate.set_conversing(True, hold_prior_s=1.0)
    assert gate.classify("what's the weather like") == ("dispatch", "what's the weather like", None)
    assert gate.armed(), "conversation keeps the gate open after a turn"
    assert gate.classify("i was thinking about the")[0] == "arm_incomplete"
    action, said, _ = gate.classify("trip next week")
    assert action == "dispatch" and said == "i was thinking about the trip next week"
    assert gate.classify("delete all my files")[0] == "guarded_without_wake"
    assert gate.end_of_phrase_pause(0.5) == 0.5
    gate.note_inner_pause(0.9)
    assert gate.end_of_phrase_pause(0.5) == pytest.approx(0.9 * gate.CONTINUATION_SLACK)
    gate.set_conversing(False)
    assert not gate.armed() and gate.end_of_phrase_pause(0.5) == 0.5


def test_speech_over_elis_voice_calls_the_barge_in_once_and_nothing_resumes_it(monkeypatch):
    stt = audio_stt.ELIAudioSTT.__new__(audio_stt.ELIAudioSTT)
    stt._voice_gate = audio_stt.VoiceGate()
    stt.recognizer = types.SimpleNamespace(energy_threshold=1200.0, non_speaking_duration=0.35)
    stt._near, stt._tap, stt._floor = None, None, None
    hits, resumed = [], []
    stt.set_conversing(True, on_barge_in=hits.append, on_barge_in_empty=lambda: resumed.append(1))
    stt._barge = types.SimpleNamespace(judge=lambda x, t, near, onsets: t)
    stt._tts_mon_last, stt._post_tts_drain_s = [time.monotonic()], 1.5      # ELI is speaking
    for t in (1.0, 1.1):
        stt._on_mic_chunk(b"\x10\x00" * 1024, 16000, t)
    for th in threading.enumerate():
        if th.name == "eli-barge-in":
            th.join(timeout=2)
    assert hits == [1.0] and stt._barge_keep_from() < 1.0
    stt._barge_in_came_to_nothing()
    assert resumed == [1] and stt._barge_keep_from() is None


# ── what is said, read locally ────────────────────────────────────────────────
@pytest.mark.parametrize("said,kind,value", [
    ("let's have a conversation", "start", None), ("start conversation mode", "start", None),
    ("stop the conversation", "stop", None), ("okay, that's all for now", "stop", None),
    ("wait", "pause", None), ("hold on a second", "pause", None), ("go on", "resume", None),
    ("please continue", "resume", None), ("stop talking", "hush", None),
    ("speak slower", "pace", "slower"), ("talk a bit faster please", "pace", "faster"),
    ("slow down", "pace", "slower"), ("speak softer", "level", "softer"), ("talk louder", "level", "louder"),
    ("be calmer", "tone", "calm"), ("sound more excited", "tone", "ecstatic"),
    ("be more serious", "tone", "intense"), ("speak normally", "normal", None),
    ("use the British voice", "voice", None), ("switch to Emma", "voice", "bf_emma"),
    ("blend heart and emma", "voice", "af_heart+bf_emma"),
])
def test_what_the_conversation_itself_is_asked(said, kind, value):
    c = conversation.read_control(said, active=True)
    assert c is not None and c.kind == kind, said
    if value is not None:
        assert c.value == value
    if said == "use the British voice":
        from eli.perception import tts_kokoro
        assert tts_kokoro.VOICES[c.value]["accent"] == "b"


@pytest.mark.parametrize("said", ["what time is it", "wait until tomorrow to remind me", "talk about cats",
                                  "use a calculator", "continue the story about the dragon", "be honest",
                                  "let's talk", "talk to me", "switch to firefox"])
def test_turns_for_eli_are_not_taken_for_controls(said):
    assert conversation.read_control(said, active=True) is None


def test_only_the_start_and_the_voice_are_read_while_the_conversation_is_off():
    assert conversation.read_control("wait", active=False) is None
    assert conversation.read_control("start a conversation", active=False).kind == "start"
    assert conversation.read_control("speak slower", active=False).kind == "pace"


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("ELI_TTS_VOICE", "")          # set_active_voice writes it: put back after the test
    monkeypatch.setenv("ELI_TTS_GAIN_DB", "")
    from eli.cognition import tone_adaptor
    monkeypatch.setattr(tone_adaptor, "_OVERRIDE", None)
    monkeypatch.setattr(tone_adaptor, "_OVERRIDE_KNOWN", True)
    monkeypatch.setattr(tone_adaptor, "sync_world", lambda force=False: None)
    from eli.core.runtime_settings import load_settings
    return load_settings


def test_voice_changes_are_made_kept_and_shown(settings, monkeypatch):
    from eli.perception import tts_router
    monkeypatch.setattr(tts_router, "_natural_default", lambda: "")
    slower = conversation.apply_control(conversation.Control("pace", "slower"))
    assert slower["ok"] and float(settings()["tts_length_scale"]) > 1.03
    assert tts_router._speed(tts_router._prosody()) < 1.0 / 1.03
    conversation.apply_control(conversation.Control("level", "softer"))
    assert tts_router.gain_db() == pytest.approx(-3.01, abs=0.01)
    conversation.apply_control(conversation.Control("tone", "calm"))
    conversation.apply_control(conversation.Control("voice", "bf_emma"))
    summary = conversation.delivery_summary()
    assert "Emma" in summary and "softer 3 dB" in summary and "calm" in summary and "pace" in summary
    assert "more slowly" in tts_router._tone_instruct() and "softly" in tts_router._tone_instruct()
    conversation.apply_control(conversation.Control("normal"))
    assert settings().get("tts_length_scale") is None and tts_router.gain_db() == 0.0
    assert "calm" not in conversation.delivery_summary()


class _Stream:
    def __init__(self, heard="Right, so the short answer"):
        self.paused = self.stopped = False
        self._heard = heard

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True
        return self._heard


def test_wait_go_on_and_speaking_over_eli(settings):
    states = []
    conv = conversation.Conversation(on_state=states.append)
    conv.start()
    stream = conv.begin_reply(_Stream)
    conv.speaking()
    conv.barge_in(1.0)
    assert stream.paused, "silent at the onset of the user's speech"
    assert conv.heard("hold on").kind == "control" and stream.paused and conv.state == "paused"
    assert conv.heard("go on").kind == "control" and not stream.paused and conv.state == "speaking"
    conv.barge_in(2.0)
    conv.barge_in_was_nothing()
    assert not stream.paused and conv.state == "speaking", "a cough does not stop ELI"
    conv.barge_in(3.0)
    out = conv.heard("what do you mean by short")
    assert out.kind == "turn" and stream.stopped and out.cut_off == "Right, so the short answer"
    assert conv.state == "thinking" and states[:3] == ["listening", "speaking", "paused"]
    assert conversation.cut_off_record(out.cut_off).startswith("Right, so the short answer")
    assert conv.heard("pause").kind == "turn", "with nothing of ELI's playing, pause is for the music"
    assert conv.heard("stop the conversation").kind == "control" and conv.state == "off"


def test_the_conversation_ends_after_longer_quiet_than_the_user_takes_to_answer(settings):
    now = [100.0]
    conv = conversation.Conversation(clock=lambda: now[0])
    assert conv.idle_timeout() == audio_stt.WAKE_ARM_TIMEOUT      # nothing learnt yet
    conv.start()
    for gap in (2.0, 4.0, 3.0):
        now[0] += gap
        conv.heard("tell me more")
        conv.ended_turn()
    assert conv.idle_timeout() == pytest.approx(4.0 * audio_stt.VoiceGate.CONTINUATION_SLACK)
    assert conversation.Conversation().idle_timeout() == conv.idle_timeout(), "learnt, and kept"
    now[0] += 5.0
    assert not conv.idle_due()
    now[0] += 2.0
    assert conv.idle_due()


# ── the window, offscreen ─────────────────────────────────────────────────────
def test_the_window_turns_conversation_mode_on_and_off_and_shows_its_state(tmp_path):
    """The Converse button turns it on (the microphone with it, stood in here), the state shows
    beside it, a typed "speak slower" changes the voice shown, and "stop the conversation" typed
    turns it off and the microphone back off; "start a conversation" typed turns it on again."""
    from tests.test_gui_names_resolve import _run
    got = _run("converse", tmp_path)
    seen = got["seen"]
    assert seen["on"] == {"state": "listening", "label": "👂 Listening", "label_hidden": False, "mic": True}
    assert "pace" in seen["voice_label"]
    assert seen["off"] == {"state": "off", "button": False, "label_hidden": True, "mic": False}
    assert seen["typed_start"] == "listening"
    assert seen["listening"][:4] == ["start", "conversing True", "conversing False", "stop"]
