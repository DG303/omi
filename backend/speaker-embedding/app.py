"""CPU-only speaker-embedding microservice for the self-hosted Omi stack.

Answers the `/v2/embedding` contract that utils/stt/speaker_embedding.py expects:
  POST /v2/embedding   multipart `file` = WAV  ->  {"embedding": [floats]}

Model: SpeechBrain ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb), CPU.
All embeddings (user profile, enrolled people, live segments) come from THIS
service, so they share one vector space; matching is cosine distance between
our own vectors. See docs/adr/0005.
"""

import io
import os

import numpy as np
import soundfile as sf
import torch
import torchaudio
from fastapi import FastAPI, File, HTTPException, UploadFile
from speechbrain.inference.speaker import EncoderClassifier

MODEL_SOURCE = os.getenv("ECAPA_MODEL_SOURCE", "speechbrain/spkrec-ecapa-voxceleb")
MODEL_SAVEDIR = os.getenv("ECAPA_SAVEDIR", "/models/ecapa")
TARGET_SR = 16000

app = FastAPI(title="speaker-embedding")
_classifier = None


def get_classifier() -> EncoderClassifier:
    global _classifier
    if _classifier is None:
        _classifier = EncoderClassifier.from_hparams(
            source=MODEL_SOURCE, savedir=MODEL_SAVEDIR, run_opts={"device": "cpu"}
        )
    return _classifier


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
    return {"status": "ok"}


@app.post("/v2/embedding")
async def embedding(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        wav = _load_wav_mono16k(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"decode failed: {e}")
    if wav.shape[1] == 0:
        raise HTTPException(status_code=400, detail="no audio samples")
    with torch.no_grad():
        emb = get_classifier().encode_batch(wav)  # (1, 1, 192)
    vec = emb.squeeze().cpu().numpy().astype(np.float32).flatten().tolist()
    return {"embedding": vec}
