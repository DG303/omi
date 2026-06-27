"""Bake the ECAPA model into the image at build time.

Instantiating the classifier with a local savedir downloads + caches the
weights so the running container needs no network at startup. See docs/adr/0005.
"""

import os

from speechbrain.inference.speaker import EncoderClassifier

MODEL_SOURCE = os.getenv("ECAPA_MODEL_SOURCE", "speechbrain/spkrec-ecapa-voxceleb")
MODEL_SAVEDIR = os.getenv("ECAPA_SAVEDIR", "/models/ecapa")

if __name__ == "__main__":
    EncoderClassifier.from_hparams(source=MODEL_SOURCE, savedir=MODEL_SAVEDIR, run_opts={"device": "cpu"})
    print(f"ECAPA cached to {MODEL_SAVEDIR}")
