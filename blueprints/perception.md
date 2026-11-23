# ELI Perception — Vision, Voice, Wake Word, Tone, OS Control

`eli/perception/` (33 files). ELI's senses and hands: local vision, speech-to-text,
text-to-speech, **conversation mode**, a **self-trained wake-word detector**, a **voice-profile/tone**
subsystem, and OS control. All local, no APIs, no third-party accounts.

## Files

| File | LOC | Role |
|---|---|---|
| `mic_resolver.py` | ~620 | **cross-platform mic auto-resolve** — ranked PortAudio candidates, isolated live probes, Linux `PULSE_SOURCE` pin |
| `audio_stt.py` | ~2.9k | STT + mic capture (read without a break) + ducking + adaptive pause + wake/voice capture |
| `near_speech.py` | ~350 | **speech the machine's own output does not explain** — the default sink's monitor as reference, the music's share at the mic learnt band by band; lowers ELI's media at the onset of the owner's speech; `BargeIn`, the user speaking over ELI's voice |
| `noise_floor.py` | ~200 | **the room's level and the speech gate set over it**, measured from every mic chunk; `LevelOnset`, speech onsets over a learnt background |
| `conversation.py` | ~400 | **conversation mode**: the state, what each thing heard is (a control or a turn), the voice controls, the idle end learnt from use |
| `speech_stream.py` | ~500 | **a reply spoken while it is written**: clause by clause, rendering ahead of one continuous output; pause, resume, stop, what was heard |
| `wakeword.py` | ~470 | **self-trained, music-robust wake-word detector** (openWakeWord features + custom head; white, pink and brown noise beds; the threshold from out-of-fold scores) |
| `voice_profile.py` | ~490 | **prosody + labelled-emotion** (tone/question detection) |
| `tts_router.py` | ~1230 | voice router: Kokoro / Qwen3-TTS / pyttsx3 + `char:`/`clone:`/`natural:` voice resolution, sentence-ahead rendering, playback; `neural_fallback_state()` makes a neural fallback observable |
| `tts_kokoro.py` | ~440 | **Kokoro-82M** on the CPU (onnxruntime): misaki phonemes without espeak-ng, the unknown-word guess, the 28 English voices |
| `tts_qwen3.py` | ~290 | **Qwen3-TTS** natural voices and cloning, on a GPU with room for it (`voice-gpu` extra) |
| `gpu_room.py` | ~90 | room on a GPU for a support model beside the language model (Whisper, Qwen3-TTS) |
| `voice_fx.py` | 252 | **voice styles** (calm / robotic / energetic / synthetic / refined) — base voice + ffmpeg effect chain; user preset store |
| `tts_xtts.py` | ~410 | the **cloned-voice registry** (`add_clone()` keeps a reference clip) and the legacy Coqui XTTS-v2 backend (non-commercial model, never bundled); when no clone engine can speak, the default voice speaks — **loudly**, not silently, which was a live fault |
| `vision.py` | 726 |
| `os_controller.py` | 1110 |
| `screen_locator.py` | 497 | locate UI elements on screen (AT-SPI → local VL-ground → OCR) |
| `screen_analysis.py` | ~190 | depth modes, prior-screen memory, research-context prompts |
| `ui_ground.py` | ~320 | local GGUF precision click + computer-use agent (no HTTP) |
| `desktop_capabilities.py` | ~180 | cross-OS input/screenshot/locate probe; feeds the awareness briefing and SELF_TEST |
| `media_deps.py` | ~200 | bundled-python + PATH discovery for yt-dlp/mpv/desktop CLIs |
| `gaze_engine.py` | 351 |
| `log_rotation.py` | 224 | log housekeeping |
| `analyze_pdfs.py`, `analyze_image.py`, `analyze_csv.py` | 381 | file-type analysers |
| `ambient_vision.py` | 239 | periodic screen glances (off by default) |
| `local_whisper_stt.py` | 589 |
| `voice_worker_streaming.py` | small | streaming voice worker |

## Vision (`vision.py` + `ambient_vision.py` + `analyze_image.py`)

The working local-vision stack (see memory `eli-image-analysis`):
- VL model (default Qwen2.5-VL, configurable) loaded via
  `Qwen25VLChatHandler`; **CPU clip forced** by monkeypatching
  `mtmd_cpp.mtmd_init_from_file(use_gpu=False)` — the GPU mtmd clip path
  **segfaults** on compute-7.5 cards.
- **Hot-swap**: unload text model → load VL → infer → restore in `finally`,
  holding `gguf_inference._LLM_CALL_LOCK`.
- **Co-resident fast path** (the `vision_coresident` setting): a small model (Moondream2 Q4)
  loaded first, so the text model's load plan sees the memory it takes;
  `prefer_fast=True` uses it without a swap.
- Images downscaled to `vision_max_image_px` (1280) to avoid context overflow;
  `repeat_penalty`/`top_k`/`top_p` set to kill a repetition loop.
- `ambient_vision.py`: optional periodic screen glances (OFF by default). A
  guarded daemon re-reads the toggle/interval each cycle and **skips a glance
  whenever the shared LLM lock is busy** (so it never steals the model
  mid-reply). Stores a short description as memory for rolling awareness.

## Screen locate & click (`screen_locator.py` + `ui_ground.py`)

Three-strategy chain for “find/click X on screen” — all local:

1. **AT-SPI** (Linux) — real widget tree: role, bounds, invoke action. Best for
   labelled buttons and accessible apps.
2. **Local VL-ground** (optional) — `ui_ground_backend=auto|local_gguf` runs a
   local GGUF (Phi-Ground / OS-Atlas / UGround naming in `models/`) or the
   primary VL with a JSON box prompt. For icon-only or non-text UI.
3. **OCR** — Tesseract word boxes + fuzzy match; click centre coordinate.

**Computer-use agent** (`computer_use_backend=auto|local_gguf`) — optional
UI-TARS-style GGUF in `models/` for multi-step screenshot→action loops. Off by
default.

Configure on GUI **Screen tab** or `settings.json`: `ui_ground_backend`,
`computer_use_backend`, `screen_analysis_depth` (`quick`|`standard`|`deep`).

## Deep screen analysis (`screen_analysis.py` + `SCREEN_READ_ANALYZE`)

Voice examples that route to a real screenshot + VL + OCR (+ text-model fusion):

- “What's on my screen?” — standard depth
- “Analyze my screen in depth / exactly what's on my screen” — deep audit
- “How does this relate to my research?” — pulls local memory + KG context
- “Do you remember seeing this in previous sessions?” — prior `screen_awareness` glances

No network. Missing OS tools (mpv, tesseract, ydotool) are offered via
`grounded_remediation` **after** a failed attempt — never as a pre-install gate.

## Cross-OS desktop input (`desktop_capabilities.py` + `platform_compat.py`)

| OS | Mouse/keyboard | Screenshot |
|---|---|---|
| Linux X11 | xdotool → PyAutoGUI | scrot |
| Linux Wayland | ydotool+ydotoold → xdotool (XWayland) → PyAutoGUI | grim+slurp |
| Windows | PyAutoGUI | Pillow ImageGrab |
| macOS | PyAutoGUI (+ Accessibility permission) | screencapture |
| Android/Termux | intents only | N/A |

See `docs/CROSS_PLATFORM.md` for the full matrix and install commands per OS.

## Speech-to-text (`audio_stt.py`, `local_whisper_stt.py`, `mic_resolver.py`)

**Microphone selection (`mic_resolver.py`, v2.3.44).** On every OS, ELI builds a ranked list of
PortAudio inputs — USB and headset/Bluetooth (AirPods, Turtle Beach Chat, Trust USB, …) before
built-in, webcam, and virtual loopback routes — and probes each in an isolated subprocess at the
device's native sample rate until one delivers live audio. Linux additionally tries
PipeWire/Pulse sources with a process-local `PULSE_SOURCE` pin after direct hardware. Honours
`ELI_MIC_DEVICE_INDEX` (explicit override) and `ELI_MIC_AUTORESOLVE=0` (OS default only).
Diagnostic: `python -m eli.tools.mic_diag`.

A large, feature-dense STT module:
- Mic capture + Whisper transcription (`local_whisper_stt`).
- **Output ducking** — lowers system sink volume while listening
  (`_eli_duck_output`/`_eli_restore_output` via `wpctl`, or `pactl` where that is all there is;
  with pactl the level used never to be put back) so ELI doesn't hear itself. A volume command
  given meanwhile (`effectors.system_helpers.volume_fallback`, every VOLUME path) first puts the
  level back (`release_output_duck`) and the listener's own put-back then leaves it: "volume up"
  said after the wake word was made to the ducked level ("now 28%") and undone by the put-back.
  A step up or down is made from the level found to a level set (up stops at 100% and never
  lowers a level above it), and the reply names both levels, so a level changed elsewhere between
  commands shows as that.
- **Echo/self-hearing suppression** (`_eli_echo_like_assistant_output`,
  `_eli_media_probably_audible`). The voice (`tts_router._run_tts`, every engine) holds the
  speaking lock for the whole reply, holding its process id; the mic treats it as stale only when
  that process has ended (`speaking_lock_held`). Taken as stale after 30 seconds, a minute-long
  reply was heard and transcribed half-way through.
- **Voice commands while a reply is coming**: a message waits its turn and is sent when the reply
  finishes (`ConversationMixin._drain_waiting`) instead of being dropped; a command that needs no
  model (pause, next, volume) runs at once (`_run_now`), also while the model loads and when none
  is loaded.
- **Decoder hints and doubt** (`local_whisper_stt`): the wake words and the apps on the machine the
  person has named in conversation go to faster-whisper as `hotwords` ("open spotify" in a noisy
  room was heard as "old thing spot employee"). A command whose every segment is below the
  decoder's own failed-decode bar is not carried out: the window opens again and the status bar
  says it was not clear (`heard_clearly`).
- **Playback** opens at the output device's own rate, the voice resampled to it, instead of
  failing first on a device that takes only its own rate.
- **Hearing the owner over ELI's own music** (`near_speech.py`). The mic is read without a break
  (`_ContinuousMic`: a thread takes every chunk as it is captured, so what is said while Whisper
  transcribes is no longer lost, and the buffer is flushed while ELI speaks). Each chunk is compared
  with what the default sink plays (read from its monitor by `parec` / `pw-record`, routing and
  volumes untouched); speech the output does not explain lowers ELI's media at its onset
  (`_duck_at_onset`, only while media plays), so the rest of the wake word and the command are heard
  over quieter music; it goes back up when nothing for ELI followed, as after a wake word. On
  synthetic fixtures (rap-like vocals over drums and chords through a speaker and a reverberant
  room, the mic's clock 80 ppm off) it found 50 / 67 / 83 of 100 utterances at 9 / 6 / 3 dB below
  the music, where a rise in loudness found none, with 2 false onsets in 4 minutes of music and
  none from its own lowering and restoring. Off with `ELI_STT_ONSET_DUCK=0`; nothing happens where
  the mic does not hear the output (headphones) or no sound server runs.
- **The speech gate follows the room** (`noise_floor.NoiseFloor`). Every chunk the mic delivers is
  measured; the room is those chunks without the spans the voice gate took as the owner's words and
  without ELI's own voice (the speaking lock and its drain). Before each capture the gate is set to
  the lowest level at which the room's last 12 s would not have started a phrase by
  SpeechRecognition's own rule (`phrase_threshold` of chunks over it, no silence longer than the
  end-of-phrase pause between), raised by Z (near_speech's) deviations of a chunk's own level
  (1/sqrt(2n) for n samples): just above a fan, above a television's or a song's loudest stretch,
  and back down when the room is quiet. The configured start (`ELI_STT_ENERGY_THRESHOLD`, 1200)
  holds only until the room has been heard for `non_speaking_duration`. It replaced the fixed
  start, SpeechRecognition's dynamic threshold switch, a recalibration every 60 silent cycles and a
  self-heal that lifted the gate after three captures ran to their cap (a capture that runs to its
  cap with no words is room, so the measured gate is above it on the next cycle). Settings ▸ Audio:
  *Automatic sensitivity* (default) or a number by hand (`stt_sensitivity_auto`,
  `stt_energy_threshold`; `ELI_STT_SENSITIVITY=manual`), and a live meter (`level_meter()`). Measured
  on fixtures (`tests/test_stt_noise_self_heal.py`: a quiet room at 120 RMS, a fan at 600, a
  television at 1,500 and music at the incident's 7,924): no phrase started on any room alone in
  48 s of each, and the owner's utterances said 6 dB and 12 dB above the room's average started one
  10 of 10 times in every room.
- **Echo cancellation** (`ensure_echo_cancel`, Settings ▸ Audio or `ELI_STT_ECHO_CANCEL=1`): PipeWire's
  own canceller (webrtc) in **monitor mode** in a client process of ELI's (`pipewire -c` with a
  private config, ended with ELI by `PR_SET_PDEATHSIG` and at exit): the default sink's monitor is
  what it takes out, so ELI's voice and its music are cancelled whatever plays them; its clean source
  (`eli_echo_cancelled`, lowest priority, never the desktop default) is read by ELI alone through
  `PULSE_SOURCE`, and a microphone the owner chose is what it cleans. The earlier path loaded
  PulseAudio's module-echo-cancel into the server: it cancels only what plays into its own sink, and
  nothing does, so it cancelled nothing. On PulseAudio without PipeWire it is not done (moving
  every stream to its sink would change the desktop's routing); `echo_cancel_state()` says why.
  Not run against a live desktop in testing (the machine's sound settings were left alone); the
  tests stand in for `pipewire` and `pactl`.
- **Voice profile** (`voice_profile.json` in the user's runtime folder; `voice_profile_n` in the
  "Microphone ready" line): the level and length of the owner's speech. It learns only from what
  the voice gate takes as the owner's — the wake word, a command it sends on (`_learn_voice`) — and
  not while ELI's media plays or the room is clearly louder than the quietest it has been heard
  (`NoiseFloor.louder_than_usual`); learnt from every capture it measured the songs the mic
  transcribed. A profile written before (no `learned_from`) starts over.
- Transcript cleanup: `_cleanup`, `_collapse_repeated_phrase`,
  `_eli_fast_command_alias` (maps spoken phrases to commands),
  `_is_safe_direct`/`_allow_direct_chat` (gates which utterances bypass to chat).
- ALSA stderr suppression to keep the console clean.
- **Duration-adaptive end-of-phrase pause** (`_listen_adaptive_pause`, 2026-06-08) —
  stock `sr.listen()` reads `pause_threshold` once per phrase, so one value can't be
  both snappy for commands and tolerant of long dictation. A faithful copy of sr's
  capture with a single dynamic condition: short commands finalise after
  `ELI_STT_SHORT_PAUSE` (0.5s) of silence; a prompt speaking past `ELI_STT_LONG_AFTER`
  (12s) needs `ELI_STT_LONG_PAUSE` (2s), so a mid-sentence pause no longer cuts it.
  Flag-gated (`ELI_STT_ADAPTIVE_PAUSE`) with fallback to stock `listen()`. In conversation mode the
  short pause is the user's own: every pause they make inside a turn and go on from is told to the
  voice gate (`note_inner_pause`), and a turn ends after half again the longest recent one
  (`VoiceGate.end_of_phrase_pause`), so a thinking pause is waited out.
- **Generic mic-capture script** — one mechanism (no second microphone) drives both
  wake-word enrollment and voice/emotion training: a list of `{prompt, sink}` steps,
  each captured (past the TTS-echo guards) clip routed to its sink, the next cue
  spoken, a `done` callback after the last. `begin_capture_script` /
  `begin_wake_enrollment` / `begin_voice_training`.
- **Acoustic wake hook** — when unarmed and a wake model is trained, the captured
  audio is scored by `wakeword` and, if it fires, the wake word is injected so the
  existing `VoiceGate` arms — catching the wake word even when whisper transcribed the
  music. The voice is a second signal (`_voice_of`, speaker_id): with speakers enrolled, an enrolled
  voice needs a lower score and any other a higher one (`WakeDetector.threshold_for`). **Per-turn tone hook** — on a real command, `voice_profile.classify_tone` is
  run and published on a side-channel for cognition (below).

## Wake word (`wakeword.py`, 2026-06-08) — self-trained, robust over music

Transcription-based wake matching can't hear "computer" over loud music (whisper
transcribes the music). Instead ELI **trains its own** detector, 100% locally:
- **Positives**: the wake phrase synthesised by ELI's OWN voice (**Kokoro**) across several
  voices (spread over accents and genders) and speeds.
- **Augmentation (the robustness)**: each positive is mixed with noise/music at random
  SNRs, so the classifier learns to spot the wake word *through* a music bed.
- **Features**: openWakeWord's open, bundled melspectrogram→embedding extractor (no
  account, no download barrier).
- **Custom head**: a small torch classifier ELI owns; `train_model()` →
  `models/wakeword/eli_wake_head.pt` (gitignored). `WakeDetector.score_audio/is_wake`
  slide a 1.5s window. Validated: clean wake = 1.00, wake+loud music (3 dB) = 1.00,
  hard-negative/music-only = 0.00.
- **User-settable phrase** — `get/set_wake_phrases` persists any phrase ("change the
  wake word to athena"); it feeds both this model and the transcription matcher.
- **Personalisation** — enrolled real-mic clips of the user are folded in as heavily-
  weighted positives.
- Actions: `WAKE_SET` / `WAKE_TRAIN` / `WAKE_ENROLL`. Fully fallback-safe
  (`ELI_WAKE_ACOUSTIC=0`; no model → the transcription matcher). Training never worked before
  2026-11-23: each fold of the threshold's cross-validation was trained inside `torch.no_grad()`.
- **Learning from use** (`ELI_WAKE_LEARN=0` turns it off). A wake word heard (bare, or before a
  command) is held (`ELIAudioSTT._wake_candidate`) and kept once the command after it is acted on
  (`_confirm_wake`): the wake word cut from the capture (`wake_segment`: its first stretch of
  speech up to the first pause, centred as the trainer's clips are) as an enrolment clip, and the
  room it was said in (what the mic heard just before, ELI's voice left out) as a noise bed, one per
  clip. Not kept while ELI spoke, when ELI's media played and the mic heard no speech the output does
  not explain (music alone), or for a voice that is not an enrolled one. Once the real clips since
  the last training are at least as many as it learnt from then (and never fewer than its folds),
  it trains again in the background (`retrain_due`, `retrain_in_background`) and the new detector is
  used once done. `training_status()` feeds Settings ▸ Audio. A short capture is scored centred, as
  its training clips are (it was padded at the end).
- **The voice as a second signal** (`threshold_for`): the trained threshold when no voice is known;
  for an enrolled voice halfway down to the held-out non-wake clips' 95th percentile; for another
  voice halfway up to the held-out wake clips' median. Measured on fixtures with a model-free
  stand-in for openWakeWord's features (log energy in 24 bands; the trainer, head, thresholds and
  learning are the real ones; `tests/test_wakeword_training.py`), the owner's wake word 12 times per
  room, so the stand-in's absolute rates say little about openWakeWord's: trained from ELI's voices
  alone, missed 5 / 12 / 10 / 12 in a quiet room / fan / television / music, a minute of television
  talk woke it 28 times (22 as an unknown voice); after ten confirmed wakes in those rooms, missed
  4 / 7 / 11 / 10 with the voice known (7 / 8 / 11 / 10 not known) and the television woke it 15
  times (14 as an unknown voice). A guest saying the wake word was accepted 12 / 12 times by the
  stand-in either way: its features do not tell speakers apart, which the real speaker_id does.

## Voice profile + tone (`voice_profile.py`, 2026-06-08) — foundation for emotion

Deliberately SEPARATE from the wake word — this is *how* the user speaks, the basis for
tone/emotion and question-vs-statement:
- **Prosody (real, numpy only)**: per-clip autocorrelation **F0/pitch** track, energy,
  voiced ratio, speaking rate, and a **terminal-pitch slope** (`analyze_prosody`).
- **Question vs statement** (working): rising terminal pitch ⇒ question
  (`question_or_statement`).
- **Labelled emotion**: `add_labelled_sample` + a Gaussian-per-class classifier (each emotion
  its own spread per feature, shrunk toward the whole set's; Mahalanobis distance; a posterior over
  the classes as the confidence; a clip further from every class than the chi-square 99 % quantile
  is no emotion rather than the least unlike one; see `blueprints/epistemic_dynamics.md`), formerly a nearest-centroid classifier
  (`train_emotion_classifier`) over neutral/happy/angry/excited/sad — robust for the
  small data a quick enrollment yields, upgradeable to a NN without touching callers.
- **`classify_tone`** returns emotion + confidence, arousal, and the question/statement
  cue. `build_profile` learns the user's baseline so tone is scored *relative* to how
  they normally sound.
- **`TRAIN_VOICE`** runs one unified guided session (wake reps + the same line said in
  each emotion); modules stay separate, the user does one flow.
- **Wired into cognition**: STT publishes the per-turn read on a fresh, timestamped
  side-channel (`set_last_tone`); `_build_enhanced_system` (`kernel/stages/prompt_assembly.py`) reads `get_last_tone`
  and prepends a speaker-voice cue so ELI adapts its delivery (warmer if upset, brisk
  if energetic). `ELI_VOICE_TONE=0` disables. Complementary to the text-based
  `cognition/tone_analyzer` (persona preferences), not a duplicate.

## Tone / emotion adaptor (`cognition/tone_adaptor.py` + `cognition/emotion_palette.py`)

Decides the emotion ELI **expresses** and drives three output channels at once.

- **Two identifiers** (the "2 systems"): *acoustic* (`voice_profile.get_last_tone` — the
  user's voice this turn) and *semantic* (`detect_text_emotion` — the content of what they
  said). `_fuse()` combines them (agreement boosts confidence; voice slightly favoured).
- **Empathetic response policy**, not mimicry: a distressed user → `tender`, heat → `calm`,
  playful → `playful`. An explicit override (`set_tone("be comedic"/"talk street")` via
  `SET_TONE`/`CLEAR_TONE`) wins outright and persists.
- **Extensible palette** (`emotion_palette.py`): 20 built-in tones (comedic, professional,
  deadpan, street_smart, gremlin, tender, …), each carrying a persona directive, a voice
  prosody profile, and avatar expression; users add their own in `config/voices/tones.json`.
- **Three output channels**: text (a directive folded into `_build_enhanced_system`), voice
  (`tts_router._prosody` overlays the tone's pace and sentence pauses on Kokoro **and
  `_apply_tone_pitch` shifts the actual audio pitch** so sad sounds lower, ecstatic higher —
  applied on the Kokoro and clone paths; Qwen3-TTS takes the tone as its instruction), and face
  (`world/avatar/persona_mapper` + the animated `gui/widgets/eli_face.py` take the expression).
  ELI's world gets the tone too (`sync_world`, a `social_context` event): each turn it changes, from
  `build_situation_brief`, and at once on `set_tone`/`clear_tone`. Nothing sent it before, so ELI's
  world, and ELI's light in ELI VR, only ever had the few expressions ELI's own state gives. The
  override is read from the settings once per process: re-read while it was None, a world thread
  reading the tone between `clear_tone()` dropping it and saving the cleared setting took the old
  tone back up.
- **Core personality is never overwritten** — the directive explicitly shades *how* ELI
  delivers, not *who* it is (emergent-voice principle). `ELI_TONE_ADAPT=0` disables.

## Animated face liveness (`gui/widgets/eli_face.py` + `cognition/expression_state.py`)

The procedural face reacts in real time to what ELI is *doing*, via three cheap flags
in `expression_state` (priority: thinking > speaking > tone):
- **Lip-sync** — the mouth animates while TTS plays (`tts_router._run_tts` sets the
  `speaking` flag; amplitude-driven from the voice's samples, by the PortAudio callback or a
  timer beside the system player).
- **Thinking look** — a focused/reflective face while the model generates (the GUI sets
  `thinking` around `is_generating`).
- Otherwise it shows the current expressed tone. Pure-Qt, embedded in the world panel.

## Emotional memory + proactive check-in (`cognition/emotion_timeline.py`, v2.1.31)

`tone_adaptor` shades the *current* reply and then forgets: the acoustic read lives in a
12-second slot, the semantic read is recomputed from the last utterance. That makes ELI
**reactive**. The emotion timeline is the durable record that makes it **proactive**.

- **Persistence** — every fused read (neutral included, so the baseline isn't built only
  from bad turns) is appended to `emotion_events` in `user.sqlite3`, with the utterance
  that produced it, the arousal, and **the action ELI ran on the preceding turn**.
- **`assess()`** returns EVIDENCE, never a phrase: the sustained run and its dominant
  emotion, the `neutral→negative` transition, the ELI action at the turn the mood turned,
  and whether it is *unusual for this user* — measured against their own baseline, so a
  naturally reserved person is never read as upset.
- **Gates** (all must pass before ELI raises anything): a run of N reads rather than a
  single spike, per-read confidence above a floor, a credible baseline, and a cooldown so
  ELI cannot nag. `assess()` reports its `reason` either way, so a *non*-check-in is
  explainable too.
- **Two surfacing paths.** In-conversation, `evidence_block()` is folded into the system
  prompt and **the model decides whether and how to raise it** — the wording is ELI's, never
  a template (see [ELI No Hard-Coded Responses]). Unprompted, the proactive daemon runs the
  same assessment on its tick, LLM-synthesises an opener from the evidence, and pushes an
  `emotion_checkin` onto the suggestion queue (+ a pending proposal so a plain "yes" routes).
  With no resident model the daemon emits **nothing** rather than a canned line.
- **`trend_line()`** runs every turn regardless, giving ELI continuity on how the
  conversation has been feeling.
- **User control** — Settings ▸ Cognition ▸ *Emotional awareness*: master toggle, exchanges
  before a mood counts, minimum confidence, check-in cooldown, recent-mood window, baseline
  history. All read at call time (no restart), all defaulting to the shipped constants. A
  *Tone of voice* dropdown in the same tab pins the register ELI answers in (Auto + the 20
  palette tones) via `tone_adaptor.set_tone`. Kill switch: `ELI_EMOTION_MEMORY=0`.

## Text-to-speech (`tts_router.py`, `tts_kokoro.py`, `tts_qwen3.py`)

The active voice decides the engine, and no fallback is silent:
- **Kokoro-82M** (`tts_kokoro.py`, Apache-2.0 weights) speaks every plain voice id, on the CPU
  through onnxruntime, on every machine. 28 voices, American (`af_*`, `am_*`) and British
  (`bf_*`, `bm_*`) English; the default is `af_heart`, the one its authors grade A. A voice saved
  for the earlier engine (a Piper id such as `en_GB-alan-medium`) maps to its nearest Kokoro voice
  (`tts_kokoro.voice_for_legacy`), and spoken names of those voices ("the alan voice") still resolve.
- **Qwen3-TTS** (`tts_qwen3.py`, Apache-2.0) speaks the `natural:<speaker>` voices (CustomVoice: nine
  built-in speakers, steered by the tone as a plain-language instruction) and the `clone:<name>`
  voices (Base: from the user's recording), on a GPU with room for it beside the language model
  (`gpu_room.room_mb`, the same rule Whisper uses: the card's free memory less the reserve and the
  language model's plan; the model's measured footprint once known, its weights until then; unknown
  means no). The 1.7B model where it fits, else the 0.6B, else none. It needs the `voice-gpu` extra
  (qwen-tts, PyTorch); its weights (2.5-4.5 GB) are fetched at a pinned revision on first use and each
  weight file is checked against its SHA-256. The voice engine setting (`tts_engine`) is `auto` (Qwen3
  where the GPU has room, Kokoro elsewhere), `kokoro` or `natural`, chosen at the start-up picker or the
  first-run wizard. Where it cannot speak, Chatterbox (MIT) and XTTS-v2 (legacy, non-commercial model,
  never bundled) are tried for clones, then the Kokoro default voice speaks and
  `neural_fallback_state()` says the chosen voice is not what is heard.
- **pyttsx3** speaks a `sys:` voice (the operating system's own), only when one is chosen; these are
  hidden from the list unless `ELI_TTS_ALLOW_ROBOTIC=1` or nothing else can speak.

*Text to phonemes, without espeak-ng.* misaki (Kokoro's own G2P, Apache-2.0) reads the text with
spaCy's small English tagger and its gold/silver dictionaries. Its usual fallback for a word they lack is
espeak-ng (GPL), which ELI does not use; `tts_kokoro._OovFallback` guesses instead: an all-capitals word
letter by letter, a word made of two dictionary words as those words, any other with gruut's CRF model
trained on the CMU dictionary (MIT), its IPA mapped onto Kokoro's phoneme letters (non-rhotic forms for
the British voices). On misaki's gold words the CRF never saw, that made 19.5% phoneme errors (the CRF
alone 21.0%; OpenPhonemizer 18.0%, but it needs PyTorch and a pickled checkpoint; letter by letter
126.6%). ELI builds misaki's G2P itself (`_misaki_g2p`) because misaki's constructor installs a missing
spaCy model with pip at run time. Kokoro's other languages are left out: misaki reads them through
espeak-ng.

*Files.* `eli/runtime/voice_assets.py` pins every file Kokoro uses to a revision and a SHA-256
(`kokoro_manifest()`): the fp32 ONNX export and its tokenizer (onnx-community/Kokoro-82M-v1.0-ONNX),
the 28 voices, spaCy's en_core_web_sm 3.8.0 (unpacked from its wheel) and gruut's `g2p/model.crf`. A
source install fetches them into `models/tts/kokoro` the first time ELI speaks (or in the installer's
voice step); the release builds carry them (`python -m eli.runtime.voice_assets --kokoro-to tts_kokoro`
in the release workflow, bundled by `ELI.spec`, read in place from the bundle). A download that does not
match its checksum is refused, and a refused one makes no folder. Once fetched, ELI speaks offline.
The fp32 export was kept: on a CPU it ran as fast as the fp16 and uint8 exports and four times faster
than the int8 one, and each reduced export changed the sound.

*Delivery the owner sets.* "Speak slower" and the like (conversation mode) are kept as
`tts_length_scale` (one step is half the spread of the palette's paces) and `tts_gain_db` (one step
halves or doubles the power), heard under any tone: the tone's values used to replace the saved pace,
neutral's own included, so a saved pace was never heard; now it is the tone's pace times the saved
pace's share of neutral. Qwen3-TTS gets the same as words in its instruction (`_tone_instruct`:
"Speak in a calm tone, more slowly than usual, softly."). Kokoro voices blend: `af_heart+bf_emma`
averages the voices' style vectors (`tts_kokoro.blend_parts`). `render_samples()` gives any voice as
samples for a caller that plays it itself.

*Speaking.* `_run_tts` holds the speaking lock (the microphone's echo guard) and the avatar's speaking
state for the whole reply. The Kokoro path renders the reply sentence by sentence on a thread of its own
and plays each sentence as soon as it is ready, the next one rendering meanwhile; the tone's pause
(`sentence_silence`) follows each sentence and the player's tail only the last. The tone palette's pace
(`length_scale`) is Kokoro's speed inverted; its pitch is shifted afterwards with ffmpeg
(`_apply_tone_pitch`); its `noise_scale`/`noise_w` have no Kokoro counterpart (they still shape a clone's
exaggeration). Measured on an i7-8700 under load, against the earlier Piper voice on the same machine:
Kokoro renders at about 0.42-0.45 of real time (Piper 0.04); the first audio of a one-sentence reply
comes after 1.2 s (Piper 0.13 s), of a three-sentence one after 3.8 s, its first sentence (Piper 0.75 s
for the whole reply); after that playback never waits for rendering.

*Where speech plays.* Through the desktop's sound server wherever one runs
(`platform_compat.sound_server()`: PipeWire or PulseAudio found by the socket a client would
reach; the system mixer on Windows and macOS). `tts_router._play_wav_blocking` opens a PortAudio
stream only on the server's own ALSA device (`pipewire`, `pulse`; on Windows anything but
WDM-KS), never a raw card the server holds; with none listed (a bundle whose PortAudio sees only
the cards) the system player plays (`pw-play`, `paplay`, then `aplay`), and lip-sync takes the
voice's loudness from the samples on a timer. Each sentence logs where it played or `NOT played`
with the reason; `last_playback()`, the `SPEAK` reply, the voice test and the speech diagnostics
say the same.

**Voice library** (`runtime/voice_assets.py`, Settings > VOICE / TTS > *Voice library…*,
`gui/widgets/voice_downloader.py`): every Kokoro voice with its accent, gender, grade and whether its
file is here; `download_voice()` fetches one (and the engine's files when missing); `resolve_voice_query()`
turns "a British male voice", "use the emma voice" or a voice id into a voice for `SET_VOICE` /
`DOWNLOAD_VOICE`, without hard-coded ids.

**Style presets** (`voice_fx.py`, `char:calm`, `char:robotic`, …) are a Kokoro base voice plus an ffmpeg effect chain (pitch / speed / filters). They are **generic sound styles**, not bundled impersonations of copyrighted characters; each preset carries an **ordered, gender-matched `fallback` chain** of Kokoro voices, and a preset saved with a Piper base reads as that base's Kokoro voice. **Voice cloning** (`CREATE_VOICE`, `clone:<name>`): `tts_xtts.add_clone()` keeps the reference recording (and its transcript when given, which lets Qwen3-TTS clone more closely); Qwen3-TTS speaks it on a GPU with room, else Chatterbox, else XTTS. Both `char:` and `clone:` resolve through `synthesize_wav` (browser voice) and `_run_tts` (host playback), and appear in the GUI voice picker.

## Conversation mode (`conversation.py`, `speech_stream.py`)

On with the window's 💬 Converse button or *"let's have a conversation"* (said or typed), off with
the button, *"stop the conversation"* / *"goodbye"*, or by itself after a quiet longer than half again
the longest of the user's recent answer gaps (learnt, kept as `conversation_answer_gaps`; before any,
the listener's armed window). While on:
- **Turns.** The voice gate stays armed (`VoiceGate.set_conversing`): no wake word for each turn,
  a guarded command still needs one, a sentence that trails off ("and", "the", "because") is held
  for the rest of it and released after the user's own continuation pause, and the end of a turn
  waits out the user's own inner pauses (above). Each turn runs in the fast reasoning mode
  (`canonical_mode("fast")`); the engine and the pipeline are unchanged.
- **Speech as it is written** (`SpeechStream`). The turn worker feeds the reply's tokens as they
  stream; `Clauses` cuts them into pieces (markup and everything from the first code block on left
  out, as `_spoken_part` does). The first clause is rendered at once; nothing plays until what is
  rendered lasts as long as the next piece takes to be written and rendered at the paces measured
  so far, then everything plays through one PortAudio stream (`DeviceOutput`, the same route as
  `_play_wav_blocking`; the system player piece by piece where none is listed) with a fade at each
  join. While audio plays, the next piece is the longest run of whole sentences that renders before
  the audio waiting runs out (a render model fitted to the last renders: seconds per piece plus per
  character), else of clauses, else as many words as do; each keeps its closing mark, so a clause
  cut at a comma is read with a comma's tone and pause. Measured on fixtures
  (`tests/test_conversation_mode.py`: a model writing 12 tokens a second after 0.8 s; Kokoro's
  measured 0.43 of real time plus 0.15 s a piece; 15 characters a second of speech): a 26-character
  reply's first audio at 2.1 s (2.3 s written whole, then spoken), a 261-character one at 2.4 s
  (7.4 s); no silence while rendering in either; the long one in 5 pieces with 2 joins inside a
  sentence.
- **Speaking over ELI** (`near_speech.BargeIn`, fed every mic chunk heard while ELI's voice plays).
  Where the mic hears the output (speakers) and NearSpeech has learnt its share there (after
  `LEARN_S`, 12 s of output; the learning is kept for the session), speech the output does not
  explain decides, ELI's voice and any music alike; before that, and where the mic does not hear the
  output (headphones), the level over what the mic hears while ELI speaks (`LevelOnset`, the same
  robust rule). Where no recorder reads the sink (no sound server, Windows, macOS) ELI's own voice
  is handed to NearSpeech as the reference (`reference_output`). At the onset the voice falls silent
  at once and the audio from the onset on is kept for the listener (`_ContinuousMic.flush(keep_from)`);
  the listener waits the short wait for it; what was said decides: "wait", "hold on", "pause" keep
  ELI silent where it stopped, "go on", "continue" go on from there, "stop", "that's enough" stop it,
  a voice control is carried out and ELI goes on, anything else drops the rest of the reply,
  cancels its generation (the window's own cancel) and is the next turn; a sound with no words in it
  lets ELI go on. A reply cut off before its stream ended is not stored by the engine; the window
  stores what was heard of it, marked as cut off there (`cut_off_record`). A reply whose stream had
  already ended is stored whole (only the main chat path waits at `__MAIN_REPLY_DONE__` for the voice).
  Measured on fixtures: speakers, the user 3 dB below ELI's level at the mic, 8 of 8 utterances found,
  128 ms median from their first syllable (320 ms worst), ELI silent within one output block after;
  headphones, 4 of 4, 100 ms; false interrupts per minute on ELI's own voice, ELI's voice over music
  and with headphones: 0, 0 and 0.
- **The voice's controls** (`read_control`, read from the words alone, before the engine: the router
  reads "speak slower" as text to say and "switch to Emma" as a window to focus): pace, level,
  tone ("calmer", "more excited", "more serious" through the palette's aliases), "speak normally",
  "use the British voice", "switch to <voice>", "blend <voice> and <voice>"; kept (the tts_* settings,
  the tone, the voice) and shown beside the button (`delivery_summary`). Pace, level, normal and
  blends are read with the mode off too; "pause", "stop" and "continue" only while ELI has something
  to pause (else they are for the music).

## OS control (`os_controller.py`)

**Platform-aware** (Linux/Windows/macOS) screenshot, volume, keyboard, clipboard.
`take_screenshot(region)` picks the right tool per platform
(`gnome-screenshot`, PIL `ImageGrab`, platform CLIs); fails gracefully where a
capability isn't available (e.g. area screenshots on some Windows installs).
`screen_locator.py` finds UI targets for click/automation.

## File analysers

`analyze_pdfs`, `analyze_image`, `analyze_csv` — typed handlers behind the `ANALYZE_*` actions, feeding
extracted content (text/OCR/structure) into the grounded pipeline.

Several folders or files named in one message all go to one job (`paths_named`,
`_route_several_paths`) in `eli/runtime/folder_analysis.py`; a path that is missing is reported; with
nothing named and no work in hand ELI asks which, never the current folder. Asked to analyse, a folder
is read in full, in pieces; asked to summarise, from the opening of each document, and the report says
which. A summary that comes back empty is a failed job: the documents are indexed and nothing has been
analysed.

## Honest assessment

- **Strong:** this is the layer that makes ELI genuinely multimodal and local —
  eyes (VL + OCR + ambient), ears (Whisper + ducking), mouth (Kokoro; Qwen3-TTS on a GPU),
  hands (cross-platform OS control). The vision co-residence + hot-swap + CPU-clip
  workaround is real engineering against hard 8GB-GPU constraints.
- **Weak / watch:**
  1. **Vision latency** — the hot-swap unloads/reloads the text model per glance;
     even the co-resident path runs CPU clip (~3.5s). Acceptable, not fast.
  2. `audio_stt.py` is a **2.5k-line** module mixing capture, ducking, echo
     suppression, command aliasing, and cleanup — wants splitting.
  3. **Residual "7B" comment** in `ambient_vision.py` ("the 7B vision model
     hot-swaps…") — cosmetic, but inconsistent with the model-agnostic line;
     should read "the vision model". (Flagged for a future scrub.)
  4. `gaze_engine.py` is experimental and off by default — fine, but it's carried
     weight.
  5. Platform tools are detected at call time; a machine missing
     `gnome-screenshot`/`wpctl` degrades silently to fallbacks (good) but
     there's no single "what perception capabilities do I actually have here?"
     probe surfaced to the user.

---

## Update — 2026-06-09 (VRAM-aware STT; drag-drop keeps the full path)
- **STT is VRAM-aware** (`local_whisper_stt._gpu_room_for_whisper`). faster-whisper goes on the
  GPU only when its card still holds this whisper model after the language model has taken every
  layer its load plan gives it (whisper's measured footprint on that card, or its weights until
  measured), else CPU — so on an 8 GB card whisper does not preload and starve the main model.
  Explicit `ELI_WHISPER_DEVICE` still wins.
- **Drag-and-drop keeps the FULL path.** A file dropped into the chat input now expands to
  `[File: <full path>]` followed by the inlined content (was content + basename only), so
  "fix/examine/edit this file" can resolve the file on disk while "summarise this" still has the
  content. PDFs keep their path too; combined with the FIX_FILE last-file memory, a bare follow-up
  "fix it" recovers the file named a turn earlier.
