"""Audio as samples: read a sound file into one channel at the rate a consumer needs.

One place for what three modules did on their own (the wake word and the voice profile each had
the same resampler), and what two more needed:

  * speech recognition: faster-whisper decodes a file with PyAV, whose argument list changed under
    it (PyAV 18 dropped `metadata_errors`, which faster-whisper 1.2.1 still passes), and every
    transcription in the packaged app failed; and PyAV's wheels carry x264 (GPL), so no release
    carries PyAV at all. Handed samples at 16 kHz faster-whisper never opens a file.
  * playback: a USB headset's ALSA device took only its own rate, and opening it at the voice's
    22,050 Hz failed ("Invalid sample rate").
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

# The rate Whisper and the wake-word model are trained at.
SPEECH_RATE = 16000


def resample(x: np.ndarray, sr_in: int, sr_out: int = SPEECH_RATE) -> np.ndarray:
    """Mono float32 samples at sr_in, at sr_out instead (linear interpolation)."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if int(sr_in) == int(sr_out) or x.size == 0:
        return x
    n_out = int(round(x.size * int(sr_out) / int(sr_in)))
    if n_out <= 1:
        return np.zeros(0, dtype=np.float32)
    return np.interp(np.linspace(0, x.size, n_out, endpoint=False),
                     np.arange(x.size, dtype=np.float32), x).astype(np.float32)


def _read_pcm_wav(path):
    """(samples, rate, channels) of an integer PCM WAV, read with the standard library: what ELI
    records and what the voice engines write. Raises for anything else."""
    import wave
    with wave.open(str(path), "rb") as w:
        width, rate, channels = w.getsampwidth(), w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v >= 1 << 23, v - (1 << 24), v)).astype(np.float32) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"unsupported sample width {width}")
    return x, int(rate), int(channels)


# The rate a file only the system's ffmpeg reads is decoded at when the caller names none: Opus, which
# browsers record in, is always 48 kHz.
_FFMPEG_RATE = 48000


def _read_with_ffmpeg(path, rate: int):
    """(samples, rate) of a file the system's ffmpeg program reads, decoded to one channel at `rate`.
    ELI carries no FFmpeg of its own (PyAV's holds x264, under the GPL); an ffmpeg installed on the
    system is a program apart, run here."""
    import shutil
    import subprocess
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError("no ffmpeg program is installed")
    ran = subprocess.run([exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
                          "-f", "f32le", "-ac", "1", "-ar", str(int(rate)), "-"], capture_output=True, check=False)
    if ran.returncode != 0:
        raise ValueError(ran.stderr.decode("utf-8", "replace").strip() or f"ffmpeg exited {ran.returncode}")
    return np.frombuffer(ran.stdout, dtype="<f4").astype(np.float32), int(rate)


def read_mono(path, sr_out: Optional[int] = None):
    """(samples, rate): the file's channels averaged into one, as float32, at sr_out when given.
    A PCM WAV is read with the standard library; other formats libsndfile reads (FLAC, Ogg Vorbis and
    Opus, MP3) with soundfile; anything else (WebM, MP4) with the system's ffmpeg program when it has
    one. Raises, saying so, when none of them reads it."""
    try:
        data, sr, channels = _read_pcm_wav(path)
        if channels > 1:
            data = data[: data.size - data.size % channels].reshape(-1, channels).mean(axis=1)
    except Exception:
        try:
            import soundfile as sf
            data, sr = sf.read(str(path), dtype="float32", always_2d=False)
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
        except Exception as unread:
            try:
                return _read_with_ffmpeg(path, sr_out or _FFMPEG_RATE)
            except (OSError, ValueError) as failure:
                raise ValueError(f"{Path(str(path)).name}: not a format ELI reads itself (WAV, FLAC, Ogg, MP3: "
                                 f"{unread}), and the system's ffmpeg did not read it ({failure})") from unread
    data = np.asarray(data, dtype=np.float32).reshape(-1)
    if sr_out and int(sr_out) != int(sr):
        return resample(data, int(sr), int(sr_out)), int(sr_out)
    return data, int(sr)


__all__ = ["SPEECH_RATE", "resample", "read_mono"]
