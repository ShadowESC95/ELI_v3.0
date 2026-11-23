"""Build the wake word's two feature models, the ONNX files eli/perception/wakeword.py runs, from their
sources, so ELI ships no file whose licence is in doubt.

openWakeWord's package carries both, and its licence note puts "all of the included pre-trained models"
under CC BY-NC-SA 4.0 (non-commercial). ELI v3 builds its own:

  * embedding_model.onnx: Google's speech_embedding/1 (Apache-2.0; https://tfhub.dev/google/speech_embedding/1,
    served from Kaggle Models), its weights read from the module's TensorFlow checkpoint and its layers
    written out as Google's graph defines them: a zero-mean 3x3 convolution, ReLU and batch norm, then
    nineteen 1x3 / 3x1 convolutions, each with batch norm, max(0.2x, x) and max(x, -0.4), and five max
    pools. Same inputs and outputs as openWakeWord's: input_1 [batch, 76, 32, 1] -> [batch, 1, 1, 96].
  * melspectrogram.onnx: a fixed transform, no trained weights: frames of 400 samples every 160, a
    periodic Hann window in a 512-point DFT (as two convolutions), the power spectrum, 32 Slaney mel
    filters from 60 to 3800 Hz (librosa's definition; the band Google's frontend uses), 10*log10 with a
    1e-10 floor, and a floor 80 dB under the clip's loudest value. input [batch, samples] -> output
    [batch, 1, frames, 32], as openWakeWord's.

    python packaging/wake_features.py --fetch DIR      # Google's module into DIR, checked against its sha256
    python packaging/wake_features.py --build DIR OUT  # both models from DIR into OUT (needs `onnx`)

Neither TensorFlow nor openWakeWord is needed: the checkpoint (a snappy-compressed table) and the graph are
read here. tests/test_wakeword_features.py holds the shipped files to openWakeWord's outputs and to the
definition above.
"""
from __future__ import annotations

import hashlib
import struct
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

# Google's module, as Kaggle Models serves TF Hub's google/speech_embedding/1 (no account needed).
SPEECH_EMBEDDING_URL = ("https://www.kaggle.com/api/v1/models/google/speech-embedding/tensorFlow1/"
                        "speech-embedding/1/download")
SPEECH_EMBEDDING_SHA256 = "0f626c42009c1eb916401b7b2800229a211e43100115bc591b9afbf1b60da27f"
_MODULE_FILES = ("saved_model.pb", "tfhub_module.pb", "variables/variables.data-00000-of-00001",
                 "variables/variables.index")
_SCOPE = "embeddings/state/MudpuppyLite/"

# The frontend, as Google's module and openWakeWord define it.
SAMPLE_RATE, N_FFT, WIN_LENGTH, HOP, N_MELS, F_MIN, F_MAX = 16000, 512, 400, 160, 32, 60.0, 3800.0
AMIN, TOP_DB = 1e-10, 80.0


# ── Google's TensorFlow checkpoint, read without TensorFlow ──────────────────────────────────────
def _varint(b: bytes, i: int) -> Tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _fields(b: bytes) -> List[Tuple[int, object]]:
    i, out = 0, []
    while i < len(b):
        tag, i = _varint(b, i)
        number, kind = tag >> 3, tag & 7
        if kind == 0:
            value, i = _varint(b, i)
        elif kind == 2:
            n, i = _varint(b, i)
            value, i = b[i:i + n], i + n
        elif kind == 5:
            value, i = struct.unpack("<I", b[i:i + 4])[0], i + 4
        elif kind == 1:
            value, i = struct.unpack("<Q", b[i:i + 8])[0], i + 8
        else:
            raise ValueError(f"wire type {kind}")
        out.append((number, value))
    return out


def _snappy(b: bytes) -> bytes:
    n, i = _varint(b, 0)
    out = bytearray()
    while i < len(b):
        tag = b[i]
        i += 1
        if tag & 3 == 0:
            length = tag >> 2
            if length >= 60:
                width = length - 59
                length, i = int.from_bytes(b[i:i + width], "little"), i + width
            out += b[i:i + length + 1]
            i += length + 1
            continue
        if tag & 3 == 1:
            length, offset, i = ((tag >> 2) & 7) + 4, ((tag >> 5) << 8) | b[i], i + 1
        elif tag & 3 == 2:
            length, offset, i = (tag >> 2) + 1, int.from_bytes(b[i:i + 2], "little"), i + 2
        else:
            length, offset, i = (tag >> 2) + 1, int.from_bytes(b[i:i + 4], "little"), i + 4
        for _ in range(length):
            out.append(out[-offset])
    if len(out) != n:
        raise ValueError("a checkpoint block did not decompress to its length")
    return bytes(out)


def _block(table: bytes, offset: int, size: int) -> List[Tuple[bytes, bytes]]:
    data = table[offset:offset + size]
    if table[offset + size] == 1:                 # the block trailer: 1 = snappy
        data = _snappy(data)
    restarts = struct.unpack("<I", data[-4:])[0]
    end, i, key, out = len(data) - 4 - 4 * restarts, 0, b"", []
    while i < end:
        shared, i = _varint(data, i)
        own, i = _varint(data, i)
        size_v, i = _varint(data, i)
        key = key[:shared] + data[i:i + own]
        i += own
        out.append((key, data[i:i + size_v]))
        i += size_v
    return out


def read_checkpoint(prefix: Path) -> Dict[str, np.ndarray]:
    """The float32 variables of a TensorFlow V2 checkpoint (PREFIX.index, PREFIX.data-00000-of-00001)."""
    table = Path(f"{prefix}.index").read_bytes()
    footer = table[-48:]
    _, j = _varint(footer, 0)
    _, j = _varint(footer, j)
    index_offset, j = _varint(footer, j)
    index_size, j = _varint(footer, j)
    data = Path(f"{prefix}.data-00000-of-00001").read_bytes()
    out = {}
    for _, handle in _block(table, index_offset, index_size):
        offset, k = _varint(handle, 0)
        size, k = _varint(handle, k)
        for key, value in _block(table, offset, size):
            if not key:
                continue                          # the bundle's header
            entry, shape = {}, []
            for number, v in _fields(value):
                if number == 2:
                    shape = [next((x for n, x in _fields(d) if n == 1), 0) for n2, d in _fields(v) if n2 == 2]
                else:
                    entry[number] = v
            if entry.get(1, 1) != 1:              # DT_FLOAT only
                continue
            raw = data[entry.get(4, 0):entry.get(4, 0) + entry.get(5, 0)]
            out[key.decode()] = np.frombuffer(raw, dtype="<f4").reshape(shape).copy()
    return out


# ── the models ────────────────────────────────────────────────────────────────────────────────────
def mel_filters() -> np.ndarray:
    """[N_FFT // 2 + 1, N_MELS]: librosa.filters.mel(sr, n_fft, n_mels, fmin, fmax), Slaney scale and
    area normalisation, written out."""
    def to_mel(f):
        f = np.asanyarray(f, dtype=np.float64)
        log_from, step = 1000.0, np.log(6.4) / 27.0
        return np.where(f >= log_from, log_from * 3 / 200 + np.log(np.maximum(f, 1e-10) / log_from) / step, f * 3 / 200)

    def to_hz(m):
        m = np.asanyarray(m, dtype=np.float64)
        log_from, step = 1000.0, np.log(6.4) / 27.0
        return np.where(m >= log_from * 3 / 200, log_from * np.exp(step * (m - log_from * 3 / 200)), m * 200 / 3)

    fft_hz = np.linspace(0, SAMPLE_RATE / 2, N_FFT // 2 + 1)
    edges = to_hz(np.linspace(to_mel(F_MIN), to_mel(F_MAX), N_MELS + 2))
    width, ramps = np.diff(edges), edges[:, None] - fft_hz[None, :]
    weights = np.zeros((N_MELS, len(fft_hz)))
    for m in range(N_MELS):
        weights[m] = np.maximum(0, np.minimum(-ramps[m] / width[m], ramps[m + 2] / width[m + 1]))
    weights *= (2.0 / (edges[2:] - edges[:-2]))[:, None]
    return weights.T.astype(np.float32)


def dft_kernels() -> Tuple[np.ndarray, np.ndarray]:
    """The real and imaginary DFT rows [N_FFT // 2 + 1, 1, N_FFT] times a periodic Hann window of
    WIN_LENGTH, centred in N_FFT."""
    window = np.zeros(N_FFT)
    start = (N_FFT - WIN_LENGTH) // 2
    window[start:start + WIN_LENGTH] = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WIN_LENGTH) / WIN_LENGTH)
    phase = 2 * np.pi * np.arange(N_FFT // 2 + 1)[:, None] * np.arange(N_FFT)[None, :] / N_FFT
    return ((window * np.cos(phase))[:, None, :].astype(np.float32),
            (-window * np.sin(phase))[:, None, :].astype(np.float32))


def _const(name: str, value) -> "object":
    from onnx import numpy_helper
    return numpy_helper.from_array(np.asarray(value, dtype=np.float32), name)


def build_melspectrogram():
    from onnx import TensorProto, helper
    real, imag = dft_kernels()
    inits = [_const("dft_real", real), _const("dft_imag", imag), _const("mel", mel_filters()),
             _const("two", 2.0), _const("amin", AMIN), _const("inf", np.inf), _const("ten", 10.0),
             _const("ln10", np.log(10.0)), _const("top_db", TOP_DB),
             helper.make_tensor("axis1", TensorProto.INT64, [1], [1])]
    nodes = [
        helper.make_node("Unsqueeze", ["input", "axis1"], ["frames_in"]),
        helper.make_node("Conv", ["frames_in", "dft_real"], ["re"], kernel_shape=[N_FFT], strides=[HOP]),
        helper.make_node("Conv", ["frames_in", "dft_imag"], ["im"], kernel_shape=[N_FFT], strides=[HOP]),
        helper.make_node("Pow", ["re", "two"], ["re2"]),
        helper.make_node("Pow", ["im", "two"], ["im2"]),
        helper.make_node("Add", ["re2", "im2"], ["power"]),
        helper.make_node("Unsqueeze", ["power", "axis1"], ["power4"]),
        helper.make_node("Transpose", ["power4"], ["power_t"], perm=[0, 1, 3, 2]),
        helper.make_node("MatMul", ["power_t", "mel"], ["mel_power"]),
        helper.make_node("Clip", ["mel_power", "amin", "inf"], ["floored"]),
        helper.make_node("Log", ["floored"], ["ln"]),
        helper.make_node("Mul", ["ln", "ten"], ["ln10x"]),
        helper.make_node("Div", ["ln10x", "ln10"], ["db"]),
        helper.make_node("ReduceMax", ["db"], ["loudest"], keepdims=0),
        helper.make_node("Sub", ["loudest", "top_db"], ["floor_db"]),
        helper.make_node("Clip", ["db", "floor_db", "inf"], ["output"]),
    ]
    graph = helper.make_graph(
        nodes, "eli_melspectrogram",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, ["batch", "samples"])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["batch", 1, "frames", N_MELS])], inits)
    return _model(graph, "Log-mel frontend for ELI's wake word (no trained weights): see packaging/wake_features.py")


# Google's graph, block by block: (conv, kernel (time, mel), padding, batch norm, pool after it).
_POOLS = {"Conv_1": ((2, 2), (2, 2)), "Conv_5": ((1, 2), (1, 2)), "Conv_9": ((2, 2), (2, 2)),
          "Conv_13": ((1, 2), (1, 2)), "Conv_17": ((2, 2), (2, 2))}


def build_embedding(variables: Dict[str, np.ndarray]):
    from onnx import TensorProto, helper
    v = {k[len(_SCOPE):]: a for k, a in variables.items() if k.startswith(_SCOPE)}
    inits, nodes = [], []

    def add(name, array):
        inits.append(_const(name, array))
        return name

    def bn(x, scope, out):
        nodes.append(helper.make_node(
            "BatchNormalization",
            [x, add(f"{scope}/gamma", v[f"{scope}/gamma"]), add(f"{scope}/beta", v[f"{scope}/beta"]),
             add(f"{scope}/moving_mean", v[f"{scope}/moving_mean"]),
             add(f"{scope}/moving_variance", v[f"{scope}/moving_variance"])],
            [out], epsilon=1e-3))
        return out

    def activation(x, i):
        nodes.append(helper.make_node("LeakyRelu", [x], [f"leaky_{i}"], alpha=0.2))     # max(0.2x, x)
        nodes.append(helper.make_node("Max", [f"leaky_{i}", "minus_0_4"], [f"act_{i}"]))
        return f"act_{i}"

    add("minus_0_4", -0.4)
    inits.append(helper.make_tensor("pad_mel", TensorProto.INT64, [8], [0, 0, 0, 1, 0, 0, 0, 1]))
    nodes.append(helper.make_node("Transpose", ["input_1"], ["nchw"], perm=[0, 3, 1, 2]))
    nodes.append(helper.make_node("Pad", ["nchw", "pad_mel"], ["padded"]))
    first = v["ZeroMeanConv2d/weights"]
    first = first - first.mean(axis=(0, 1), keepdims=True)                 # Google's ZeroMeanConv2d
    nodes.append(helper.make_node("Conv", ["padded", add("ZeroMeanConv2d/weights", first.transpose(3, 2, 0, 1))],
                                  ["conv_0"], kernel_shape=[3, 3]))
    nodes.append(helper.make_node("Relu", ["conv_0"], ["relu_0"]))
    x = activation(bn("relu_0", "BatchNorm", "bn_0"), 0)
    for i in range(19):
        conv = "Conv" if i == 0 else f"Conv_{i}"
        weights = v[f"{conv}/weights"]
        kh, kw = weights.shape[:2]
        pads = [0, kw // 2, 0, kw // 2] if kw > 1 else [0, 0, 0, 0]       # 1x3 SAME, 3x1 VALID
        nodes.append(helper.make_node("Conv", [x, add(f"{conv}/weights", weights.transpose(3, 2, 0, 1))],
                                      [f"conv_{i + 1}"], kernel_shape=[kh, kw], pads=pads))
        if i == 18:
            x = f"conv_{i + 1}"                                          # the embedding: no norm, no activation
            break
        x = activation(bn(f"conv_{i + 1}", f"BatchNorm_{i + 1}", f"bn_{i + 1}"), i + 1)
        if conv in _POOLS:
            kernel, stride = _POOLS[conv]
            nodes.append(helper.make_node("MaxPool", [x], [f"pool_{i + 1}"], kernel_shape=list(kernel),
                                          strides=list(stride)))
            x = f"pool_{i + 1}"
    nodes.append(helper.make_node("Transpose", [x], ["embedding"], perm=[0, 2, 3, 1]))
    graph = helper.make_graph(
        nodes, "speech_embedding",
        [helper.make_tensor_value_info("input_1", TensorProto.FLOAT, ["batch", 76, N_MELS, 1])],
        [helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch", 1, 1, 96])], inits)
    return _model(graph, "Google's speech_embedding/1 (Apache-2.0), converted by ELI: see packaging/wake_features.py")


def _model(graph, doc: str):
    from onnx import checker, helper
    model = helper.make_model(graph, producer_name="eli-wake-features", opset_imports=[helper.make_opsetid("", 13)],
                              doc_string=doc)
    model.ir_version = 7
    checker.check_model(model)
    return model


# ── fetch, build ──────────────────────────────────────────────────────────────────────────────────
def fetch(dest: Path) -> Path:
    """Google's module, checked against its sha256 and unpacked (its four files and nothing else)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "speech-embedding-1.tar.gz"
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != SPEECH_EMBEDDING_SHA256:
        request = urllib.request.Request(SPEECH_EMBEDDING_URL, headers={"User-Agent": "eli-wake-features"})
        with urllib.request.urlopen(request, timeout=300) as response:
            body = response.read()
        if hashlib.sha256(body).hexdigest() != SPEECH_EMBEDDING_SHA256:
            raise ValueError("the speech_embedding archive does not match its sha256")
        archive.write_bytes(body)
    with tarfile.open(archive) as tar:
        for name in _MODULE_FILES:
            member = tar.getmember(name)
            target = dest / "module" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    return dest / "module"


def build(module: Path, out: Path) -> List[Path]:
    import onnx
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    variables = read_checkpoint(Path(module) / "variables" / "variables")
    written = []
    for name, model in (("melspectrogram.onnx", build_melspectrogram()), ("embedding_model.onnx", build_embedding(variables))):
        onnx.save(model, str(out / name))
        written.append(out / name)
    return written


def main(argv: List[str]) -> int:
    if argv[:1] == ["--fetch"] and len(argv) == 2:
        print(fetch(Path(argv[1])))
        return 0
    if argv[:1] == ["--build"] and len(argv) == 3:
        for path in build(Path(argv[1]), Path(argv[2])):
            print(f"{path}  sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
