"""CPU-only speaker-embedding microservice for the self-hosted Omi stack.

Answers the `/v2/embedding` contract that utils/stt/speaker_embedding.py expects:
  POST /v2/embedding   multipart `file` = WAV  ->  {"embedding": [floats]}

Model: SpeechBrain ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb), CPU.
All embeddings (user profile, enrolled people, live segments) come from THIS
service, so they share one vector space; matching is cosine distance between
our own vectors. See docs/adr/0005.
"""

import io
import logging
import os
from contextlib import asynccontextmanager

import numpy as np
import soundfile as sf
import torch
import torchaudio
from fastapi import FastAPI, File, HTTPException, UploadFile
from speechbrain.inference.speaker import EncoderClassifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("speaker-embedding")

MODEL_SOURCE = os.getenv("ECAPA_MODEL_SOURCE", "speechbrain/spkrec-ecapa-voxceleb")
MODEL_SAVEDIR = os.getenv("ECAPA_SAVEDIR", "/models/ecapa")
TARGET_SR = 16000

_classifier = None


def get_classifier() -> EncoderClassifier:
    global _classifier
    if _classifier is None:
        logger.info("loading ECAPA model from %s (savedir=%s)", MODEL_SOURCE, MODEL_SAVEDIR)
        _classifier = EncoderClassifier.from_hparams(
            source=MODEL_SOURCE, savedir=MODEL_SAVEDIR, run_opts={"device": "cpu"}
        )
        logger.info("ECAPA model loaded")
    return _classifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Preload weights at startup so (a) the first request isn't penalised by a
    # multi-second model load, (b) there's no lazy-init race between concurrent
    # first requests, and (c) /health reflects true readiness. A missing model
    # fails startup loudly instead of failing the first live embedding call.
    get_classifier()
    yield


app = FastAPI(title="speaker-embedding", lifespan=lifespan)


def _load_wav_mono16k(data: bytes) -> torch.Tensor:
    """Decode WAV bytes -> mono 16kHz float32 tensor of shape (1, frames)."""
    wav, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)  # (frames, channels)
    tensor = torch.from_numpy(wav.T)  # (channels, frames)
    if tensor.shape[0] > 1:
        tensor = tensor.mean(dim=0, keepdim=True)  # downmix to mono
    if sr != TARGET_SR:
        tensor = torchaudio.functional.resample(tensor, sr, TARGET_SR)
    return tensor  # (1, frames) == (batch, time)


@app.get("/health")
def health():
    # Honest readiness: not ok until the model is loaded, so a container
    # healthcheck (and depends_on: service_healthy) gates real traffic correctly.
    if _classifier is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok"}


# NOTE: sync `def` (not `async def`) is deliberate. Inference (encode_batch) is
# heavy CPU work; FastAPI runs `def` endpoints in a threadpool, so it never
# blocks the event loop. An `async def` here would stall /health and every other
# request for the duration of each inference. See backend AGENTS.md async rules.
@app.post("/v2/embedding")
def embedding(file: UploadFile = File(...)):
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        wav = _load_wav_mono16k(data)
    except Exception as e:
        logger.warning("decode failed (%d bytes): %s", len(data), e)
        raise HTTPException(status_code=400, detail=f"decode failed: {e}")
    if wav.shape[1] == 0:
        raise HTTPException(status_code=400, detail="no audio samples")
    with torch.no_grad():
        emb = get_classifier().encode_batch(wav)  # (1, 1, 192)
    vec = emb.squeeze().cpu().numpy().astype(np.float32).flatten().tolist()
    return {"embedding": vec}
