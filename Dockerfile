# CABO all-in-one image: scoreboard PWA + Edge TTS -> RVC voice backend.
#
# One container, one origin, one HTTPS domain. Serving the web app from the same
# host as /tts is what makes phones actually use RVC: a cross-origin HTTP
# endpoint is blocked by mixed-content rules, a same-origin one is not.
#
# Voice weights are pulled at BUILD time from a public Hugging Face model repo
# so the git repo stays small. Override with --build-arg HF_MODEL_REPO=...
#
# Build args:
#   HF_MODEL_REPO   HF repo holding the .pth/.index files
#   INCLUDE_INDEX   "1" to also bake in .index files (better similarity, but
#                   they are large and faiss loads them into RAM). Default "0".
#   MODEL_ALLOW     glob of weights to include, e.g. "nanami*" for a lean image.
FROM python:3.10-slim

ARG HF_MODEL_REPO=feikong66/cabo-rvc-models
ARG INCLUDE_INDEX=0
ARG MODEL_ALLOW=*

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    RVC_MODELS_DIR=/app/rvc_models

# ffmpeg for audio, git for pip VCS installs, build tools for fairseq/pyworld.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git build-essential libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Main dependency set (CPU torch pinned via the PyTorch CPU index).
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt

# 2) fairseq — needed to load HuBERT. 0.12.2 has no wheel for py>=3.9 and its
#    sdist does not build under modern setuptools/Cython, so try, in order:
#      a. the pinned sdist under a legacy build env,
#      b. a community fork that supports newer Python,
#      c. plain pip as a last resort.
#    Any one succeeding is enough; the build only fails if all three do.
RUN pip install "setuptools<70" "wheel" "Cython<3" && \
    ( pip install --no-build-isolation fairseq==0.12.2 \
      || pip install --no-build-isolation "git+https://github.com/One-sixth/fairseq.git" \
      || pip install fairseq ) && \
    python -c "import fairseq; print('fairseq OK', fairseq.__version__)"

# 3) rvc-python without deps — requirements.txt above already provides them and
#    its own pins would drag in a conflicting fairseq/numpy resolution.
RUN pip install --no-deps rvc-python && \
    python -c "import rvc_python; print('rvc-python OK')"

# 4) Base models (hubert_base.pt, rmvpe.pt). rvc-python fetches these on first
#    init; doing it now keeps cold start fast and surfaces failures at build.
RUN python -c "\
from rvc_python.download_model import download_rvc_models; \
import rvc_python, os; \
lib=os.path.dirname(rvc_python.__file__); \
download_rvc_models(lib); \
print('base models ready')"

# 5) Voice weights from the HF model repo. Index files are excluded by default:
#    they dominate image size and RAM, while .pth alone already gives the voice.
ENV HF_MODEL_REPO=${HF_MODEL_REPO} \
    INCLUDE_INDEX=${INCLUDE_INDEX} \
    MODEL_ALLOW=${MODEL_ALLOW}
RUN pip install --upgrade huggingface_hub && python -c "\
import os; \
from huggingface_hub import snapshot_download; \
inc = os.environ.get('INCLUDE_INDEX','0') == '1'; \
pat = os.environ.get('MODEL_ALLOW','*'); \
allow = [pat + '.pth'] + ([pat + '.index'] if inc else []); \
snapshot_download(os.environ['HF_MODEL_REPO'], local_dir='/app/models', repo_type='model', allow_patterns=allow); \
print('weights ->', sorted(os.listdir('/app/models')))"

# 6) Application code and the CABO web app (served at /).
RUN mkdir -p /app/rvc_models
COPY config.py tts.py rvc.py app.py config.json /app/
COPY web /app/web

EXPOSE 7860
CMD ["python", "app.py"]
