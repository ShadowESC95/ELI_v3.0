# Where the wake word's two feature models come from

Both files are built by `packaging/wake_features.py` (`--fetch DIR`, then `--build DIR/module OUT`), with
no TensorFlow and nothing of openWakeWord's model files. openWakeWord's own copies fall under its note
that "all of the included pre-trained models" are CC BY-NC-SA 4.0 (non-commercial), so ELI ships these.

| File | sha256 | What it is | Licence |
|---|---|---|---|
| `embedding_model.onnx` | `12227549401fb2ab9a1e0292b69a8750c638374d97d89f91ea78b7dada26b9dd` | Google's **speech_embedding/1**: its weights read from the module's checkpoint, its layers as the module's graph defines them (a zero-mean 3x3 convolution, ReLU and batch norm, nineteen 1x3 / 3x1 convolutions with batch norm, max(0.2x, x) and max(x, -0.4), five max pools). Input `input_1` [batch, 76, 32, 1], output [batch, 1, 1, 96] | **Apache-2.0** (Google), see `LICENSE.speech-embedding.txt` |
| `melspectrogram.onnx` | `258ccf2a45fc2495d47a19d1824c31544be918507bbf9505384d0bbf1a822639` | ELI's log-mel frontend, a fixed transform with no trained weights: 400-sample frames every 160 samples, a periodic Hann window in a 512-point DFT, the power spectrum, 32 Slaney mel filters from 60 to 3800 Hz (the band of Google's frontend), 10*log10 with a 1e-10 floor and a floor 80 dB under the loudest value. Input `input` [batch, samples], output [batch, 1, frames, 32] | ELI's own (`LICENSE.eli-melspectrogram.txt`) |

**Google's release:** TF Hub `google/speech_embedding/1`, served without an account by Kaggle Models at
`https://www.kaggle.com/api/v1/models/google/speech-embedding/tensorFlow1/speech-embedding/1/download`
(1,336,273 bytes, sha256 `0f626c42009c1eb916401b7b2800229a211e43100115bc591b9afbf1b60da27f`), the model
page stating Apache-2.0.

**Checked against openWakeWord's files** (openwakeword 0.4.0, on fixed synthetic audio: a voice-like
glide with harmonics and noise, the same 64 times quieter, and silence; `tests/test_wakeword_training.py`
repeats it wherever openwakeword's files are installed):

- melspectrogram: the same shape, at most 1.5e-5 dB apart on values of 21–101 dB (float rounding).
- embedding: identical outputs under ONNX Runtime's default optimisation (which folds each batch norm
  into its convolution, as openWakeWord's converter had); at most 3.4e-5 apart on values up to 46.6 with
  all optimisation off. openWakeWord's file is Google's weights, re-laid out.
- the wake head ELI trains on these features scores the same clips the same through either pair.
