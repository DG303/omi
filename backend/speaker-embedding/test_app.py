"""Contract tests for the speaker-embedding service.

Shape/contract only — runs without real speech. Speaker-separation QUALITY
(does Ben's voice match Ben and not Skylar) is verified empirically against
real clips during live deploy, not here. See docs/adr/0005.
"""

import io

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from scipy.spatial.distance import cosine

from app import app, get_classifier

client = TestClient(app)

SR = 16000


def _wav_bytes(freq: float, seconds: float = 2.0, sr: int = SR) -> bytes:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    sig = 0.3 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, sig, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _embed(wav: bytes):
    resp = client.post("/v2/embedding", files={"file": ("a.wav", wav, "audio/wav")})
    assert resp.status_code == 200, resp.text
    return np.array(resp.json()["embedding"], dtype=np.float32)


def test_health():
    # /health now reflects model readiness (503 until loaded). The non-context
    # TestClient doesn't run lifespan, so preload the model explicitly first —
    # mirrors what the lifespan startup does in production.
    get_classifier()
    assert client.get("/health").json()["status"] == "ok"


def test_returns_fixed_dim_vector():
    emb = _embed(_wav_bytes(220.0))
    assert emb.ndim == 1
    assert emb.shape[0] == 192  # ECAPA embedding dimension


def test_identical_input_is_deterministic():
    wav = _wav_bytes(220.0)
    a, b = _embed(wav), _embed(wav)
    assert cosine(a, b) < 1e-4  # same audio -> ~zero cosine distance


def test_different_input_differs():
    a = _embed(_wav_bytes(180.0))
    b = _embed(_wav_bytes(440.0))
    assert cosine(a, b) > 1e-3  # different audio -> measurably different vector


def test_resamples_non_16k_input():
    # 44.1kHz input must be accepted (service resamples internally)
    emb = _embed(_wav_bytes(220.0, sr=44100))
    assert emb.shape[0] == 192


def test_rejects_empty_file():
    resp = client.post("/v2/embedding", files={"file": ("a.wav", b"", "audio/wav")})
    assert resp.status_code == 400
