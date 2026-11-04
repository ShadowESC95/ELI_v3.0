"""The wake-word trainer's own materials (eli/perception/wakeword.py): its noise beds are what they
are named, white, pink and brown, so the head learns against the noise a room actually has."""
import numpy as np


def _slope(bed: np.ndarray, sr: int) -> float:
    """Power fall-off per decade, as log10 of the power at 100 Hz over that at 1 kHz: 0 for white
    noise, 1 for pink (1/f), 2 for brown (1/f²)."""
    spectrum = np.abs(np.fft.rfft(bed)) ** 2
    freqs = np.fft.rfftfreq(len(bed), 1.0 / sr)

    def band(lo, hi):
        return float(spectrum[(freqs >= lo) & (freqs < hi)].mean())

    return float(np.log10(band(90.0, 110.0) / band(900.0, 1100.0)))


def test_the_noise_beds_are_white_pink_and_brown(tmp_path, monkeypatch):
    from eli.perception import wakeword as ww
    monkeypatch.setattr(ww, "_model_dir", lambda: tmp_path)       # no clips of the user's: the synthetic three
    beds = ww._noise_beds()
    assert len(beds) == 3
    white, pink, brown = (_slope(b, ww.SR) for b in beds)
    assert abs(white) < 0.3, "white is flat"
    assert 0.7 < pink < 1.3, "pink falls by ten per decade (it used to be brown under that name)"
    assert 1.7 < brown < 2.3, "brown by a hundred"
