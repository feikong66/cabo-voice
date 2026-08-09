# CABO all-in-one image: scoreboard PWA + neural voice, one origin.
#
# One container, one origin, one HTTPS domain. Serving the web app from the same
# host as /tts is what makes phones actually use the good voice: a cross-origin
# HTTP endpoint is blocked by mixed-content rules, a same-origin one is not.
#
# Two build profiles, selected with VOICE_MODE:
#
#   tts (default)  Edge TTS only. No torch, no model weights. ~200MB image,
#                  ~80MB RSS, builds in ~1 min, runs on a 512MB free tier.
#   rvc            Adds torch + RVC for custom character voices. ~3GB image,
#                  ~2GB RSS, builds in 15-25 min. Build it with:
#                    --build-arg VOICE_MODE=rvc --build-arg PYTHON_VERSION=3.10
#                  (3.10 because fairseq will not build on newer Python.)
#
# Other build args:
#   HF_MODEL_REPO   HF repo holding the .pth/.index files (rvc only)
#   INCLUDE_INDEX   "1" to also bake in .index files — better similarity, but
#                   they are large and faiss loads them into RAM. Default "0".
#   MODEL_ALLOW     glob of weights to include, e.g. "nanami*" for a lean image.
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ARG VOICE_MODE=tts
ARG HF_MODEL_REPO=feikong66/cabo-rvc-models
ARG INCLUDE_INDEX=0
ARG MODEL_ALLOW=*

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    VOICE_MODE=${VOICE_MODE} \
    RVC_MODELS_DIR=/app/rvc_models

# tts mode needs nothing but TLS roots. The heavy toolchain (ffmpeg for audio,
# git for pip VCS installs, compilers for fairseq/pyworld) is rvc-only.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    if [ "$VOICE_MODE" = "rvc" ]; then \
        apt-get install -y --no-install-recommends ffmpeg git build-essential libsndfile1; \
    fi && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Base dependencies — edge-tts + the web layer. Tiny in tts mode.
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# 2) Everything below is skipped entirely in tts mode.
COPY requirements-rvc.txt /app/requirements-rvc.txt
RUN if [ "$VOICE_MODE" = "rvc" ]; then \
        pip install -r /app/requirements-rvc.txt; \
    else \
        echo "VOICE_MODE=$VOICE_MODE — skipping torch/RVC stack"; \
    fi

# 3) fairseq — needed to load HuBERT. 0.12.2 has no wheel for py>=3.9 and its
#    sdist does not build under modern setuptools/Cython, so try, in order:
#      a. the pinned sdist under a legacy build env,
#      b. a community fork that supports newer Python,
#      c. plain pip as a last resort.
#    Any one succeeding is enough; the build only fails if all three do.
RUN if [ "$VOICE_MODE" = "rvc" ]; then \
        pip install "setuptools<70" "wheel" "Cython<3" && \
        ( pip install --no-build-isolation fairseq==0.12.2 \
          || pip install --no-build-isolation "git+https://github.com/One-sixth/fairseq.git" \
          || pip install fairseq ) && \
        python -c "import fairseq; print('fairseq OK', fairseq.__version__)" && \
        pip install --no-deps rvc-python && \
        python -c "import rvc_python; print('rvc-python OK')"; \
    fi

# 4) Base models (hubert_base.pt, rmvpe.pt) plus the voice weights. Fetching at
#    build time keeps cold start fast and surfaces failures before deploy.
ENV HF_MODEL_REPO=${HF_MODEL_REPO} \
    INCLUDE_INDEX=${INCLUDE_INDEX} \
    MODEL_ALLOW=${MODEL_ALLOW}
RUN if [ "$VOICE_MODE" = "rvc" ]; then \
        python -c "\
from rvc_python.download_model import download_rvc_models; \
import rvc_python, os; \
download_rvc_models(os.path.dirname(rvc_python.__file__)); \
print('base models ready')" && \
        pip install --upgrade huggingface_hub && \
        python -c "\
import os; \
from huggingface_hub import snapshot_download; \
inc = os.environ.get('INCLUDE_INDEX','0') == '1'; \
pat = os.environ.get('MODEL_ALLOW','*'); \
allow = [pat + '.pth'] + ([pat + '.index'] if inc else []); \
snapshot_download(os.environ['HF_MODEL_REPO'], local_dir='/app/models', repo_type='model', allow_patterns=allow); \
print('weights ->', sorted(os.listdir('/app/models')))"; \
    fi

# 5) Application code and the CABO web app (served at /).
RUN mkdir -p /app/rvc_models /app/models
COPY config.py tts.py rvc.py app.py config.json /app/
COPY web /app/web

# Fail the build rather than ship an image that boots into a broken voice.
RUN python -c "import app; print('[build] import OK, mode=', app.VOICE_MODE)"

# Informational only. The app binds whatever $PORT the platform injects
# (Cloud Run sends 8080, Render 10000) and falls back to 7860 locally.
EXPOSE 7860
CMD ["python", "app.py"]
