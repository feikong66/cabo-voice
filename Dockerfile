# CABO voice service — Edge TTS (source) -> RVC (target voice) -> WAV
# For Railway / Render / Koyeb / Fly free CPU tiers (Docker build).
# Model weights are pulled at BUILD time from a Hugging Face model repo,
# so the GitHub repo stays small (no 1.7GB binaries). Override the source with:
#   build arg / env HF_MODEL_REPO=your-org/your-models
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTORCH_ENABLE_MPS_FALLBACK=1 \
    HF_MODEL_REPO=feikong66/cabo-rvc-models

# System deps: ffmpeg for audio, git to fetch RVC, build tools for fairseq etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git build-essential cmake libsndfile1 wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Clone the official RVC WebUI (provides infer_cli.py + modules). Shallow clone.
RUN git clone --depth 1 https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git /app/rvc

# 2) Install CPU torch + service deps. requirements.txt pins CPU torch from the
#    PyTorch CPU index, so no huge CUDA build is pulled.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# 3) Add huggingface_hub (used to pull models at build time).
RUN pip install --no-cache-dir --upgrade huggingface_hub

# 4) Pull RVC voice models from the HF model repo at build time (public repo,
#    anonymous download). Lands in /app/models, matching config.json paths.
RUN python -c "import os; from huggingface_hub import snapshot_download; \
snapshot_download(os.environ['HF_MODEL_REPO'], local_dir='/app/models', repo_type='model')"

COPY config.py tts.py rvc.py app.py /app/
COPY config.json /app/config.json

EXPOSE 7860

CMD ["python", "app.py"]
