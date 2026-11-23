"""Sound made for the listening tests: voices, music, a fan, a television and a room, all
synthesised here. Nothing is recorded, played or heard for real.

A "voice" is syllables of a voice's harmonics under vowel formants with pauses between them (the
fundamental and the formant scale tell one speaker from another); "music" is drums, a chord and a
rap-like vocal, as ELI's sink plays it; the room is a reverberant tail, a delay and a microphone
clock 80 ppm off the speakers'.
"""
from __future__ import annotations

import numpy as np

SR = 16000
_VOWELS = [(730, 1090, 2440), (530, 1840, 2480), (270, 2290, 3010), (570, 840, 2410), (300, 870, 2240)]


def voice(rng, seconds, f0, syll_s, formant_scale=1.0, gap_s=(0.02, 0.08)):
    """Speech-like sound, unit RMS."""
    out = np.zeros(int(seconds * SR))
    pos = 0
    while pos < len(out):
        n = int(rng.uniform(*syll_s) * SR)
        t = np.arange(n) / SR
        pitch = f0 * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(1, 2) * t))
        phase = 2 * np.pi * np.cumsum(pitch) / SR
        formants = np.array(_VOWELS[rng.integers(len(_VOWELS))]) * formant_scale
        syl = np.zeros(n)
        for h in range(1, int(4000 / f0)):
            amp = sum(1.0 / (1 + ((h * f0 - f) / 90.0) ** 2) for f in formants) / h ** 0.5
            syl += amp * np.sin(h * phase)
        syl *= np.sin(np.pi * np.linspace(0, 1, n)) ** 0.5
        e = min(len(out), pos + n)
        out[pos:e] = syl[: e - pos]
        pos = e + int(rng.uniform(*gap_s) * SR)
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def music(seconds, seed):
    """Drums, a chord and a rap-like vocal."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.zeros(n)
    beat = int(0.65 * SR)
    for k, start in enumerate(range(0, n, beat)):
        m = min(int(0.25 * SR), n - start)
        decay = np.exp(-np.arange(m) / (0.05 * SR))
        hit = rng.standard_normal(m) if k % 2 else np.sin(2 * np.pi * 55 * np.arange(m) / SR) * 3
        x[start:start + m] += hit * decay
    t = np.arange(n) / SR
    x += 0.3 * sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.2, 329.6))
    x = x / np.sqrt(np.mean(x ** 2))
    return x + voice(rng, seconds, 170.0, (0.10, 0.18), formant_scale=1.12)


def fan(seconds, seed):
    """A fan or an air conditioner: steady noise falling with frequency (brown), unit RMS."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.standard_normal(int(seconds * SR)))
    x = x - np.convolve(x, np.ones(801) / 801, mode="same")
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


def television(seconds, seed):
    """Two people talking on a television, taking turns with short gaps, unit RMS."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * SR))
    pos, who = 0, 0
    while pos < len(out):
        turn = rng.uniform(1.5, 4.0)
        f0, scale = ((205.0, 1.15), (102.0, 0.94))[who]
        u = voice(rng, turn, f0, (0.12, 0.22), formant_scale=scale)
        e = min(len(out), pos + len(u))
        out[pos:e] = u[: e - pos]
        pos = e + int(rng.uniform(0.2, 0.6) * SR)
        who = 1 - who
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def through_the_room(x, seed, delay_s=0.045, ppm=80.0):
    """The speakers and the room to the mic: a reverberant tail, a delay and another clock."""
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(int(0.4 * SR)) * np.exp(-np.arange(int(0.4 * SR)) / (0.06 * SR)) * 0.2
    ir[0] = 1.0
    y = np.fft.irfft(np.fft.rfft(x, 2 * len(x)) * np.fft.rfft(ir, 2 * len(x)))[: len(x)]
    idx = np.arange(len(y)) * (1 + ppm * 1e-6)
    y = np.interp(idx[idx < len(y) - 1], np.arange(len(y)), y)
    y = np.concatenate([np.zeros(int(delay_s * SR)), y])[: len(x)]
    return np.pad(y, (0, len(x) - len(y))) / (np.sqrt(np.mean(y ** 2)) + 1e-12)


def feed(det, ref, mic, chunk=1024):
    """A NearSpeech fed the output and the mic chunk by chunk; the onsets it reported."""
    onsets = []
    for pos in range(0, len(mic) - chunk, chunk):
        t = (pos + chunk) / SR
        det.reference(ref[pos:pos + chunk], t)
        onsets += det.mic(mic[pos:pos + chunk], t)
    return onsets


def int16_rms(x, full_scale_rms):
    """`x` (unit RMS) at `full_scale_rms` in 16-bit units, as the mic would hand it over."""
    return np.clip(x * full_scale_rms, -32768, 32767)
