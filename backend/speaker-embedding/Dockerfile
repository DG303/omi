FROM python:3.11-slim

# libsndfile for soundfile, ffmpeg for broad audio decode support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install torch CPU wheels explicitly (smaller, no CUDA) then the rest
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 torchaudio==2.5.1 \
    && pip install --no-cache-dir -r requirements.txt

# Bake model weights into the image (offline startup)
ENV ECAPA_SAVEDIR=/models/ecapa
COPY download_model.py .
RUN python download_model.py

COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
