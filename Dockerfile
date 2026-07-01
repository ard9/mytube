# =============================================================================
#  Echo / MyTube — GPU Dockerfile
# -----------------------------------------------------------------------------
#  Base = official NVIDIA CUDA 12.6 runtime WITH cuDNN 9. We need this combo
#  because the two heavy features use the GPU through different stacks:
#    * STT  (faster-whisper)  -> ctranslate2  -> needs CUDA 12 + cuDNN 9
#    * TTS  (StyleTTS2)        -> torch        -> needs CUDA 12 (cu126 wheels)
#  Ubuntu 24.04 ships Python 3.12, matching what you run locally.
#
#  Build context is this folder (the one containing backend/ and frontend/):
#      docker build -t echo-mytube .
#  …but normally you'll just use docker-compose (see docker-compose.yml).
# =============================================================================
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

# Quieter apt, no .pyc files, unbuffered logs so `docker logs` is live.
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KMP_DUPLICATE_LIB_OK=TRUE

# System packages:
#   python3 / venv / pip  -> the runtime
#   ffmpeg                -> thumbnails, TTS clip cutting, yt-dlp merges
#   git, build-essential  -> a few ML deps build small native bits on install
RUN rm -f /etc/apt/sources.list.d/cuda*.list /etc/apt/sources.list.d/nvidia-ml.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev python3-pip \
        ffmpeg git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Use an isolated venv (Ubuntu 24.04's system Python is "externally managed").
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip

# --- Heavy ML wheels first so these layers cache across code changes -------- #
# torch + torchaudio from the CUDA 12.6 wheel index (matches the base image).
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126

WORKDIR /app

# Core web deps (cached unless requirements.txt changes), then the two optional
# feature packages and yt-dlp. These are NOT in requirements.txt by default, so
# we install them explicitly here to make the container "batteries included".
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt && \
    pip install "faster-whisper>=1.0" "styletts2>=0.1.6" yt-dlp gtts

# --- Application code ------------------------------------------------------- #
COPY backend  /app/backend
COPY frontend /app/frontend

# --- Runtime configuration (all overridable at `docker run`/compose) -------- #
#   MYTUBE_DATA_DIR     -> all writable state (config, sessions, dictionary,
#                          tts_media, ...) lands here; mount a volume on it.
#   MYTUBE_LIBRARY_PATH -> your video library; bind-mount your real folder here.
#   HF_HOME             -> StyleTTS2 / Whisper model cache; mount a volume so
#                          models download once and survive rebuilds.
ENV MYTUBE_DATA_DIR=/data \
    MYTUBE_LIBRARY_PATH=/library \
    MYTUBE_HOST=0.0.0.0 \
    MYTUBE_PORT=8420 \
    HF_HOME=/cache/huggingface \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN mkdir -p /data /library /cache/huggingface

EXPOSE 8420

# main.py reads MYTUBE_HOST/PORT and serves the app.
CMD ["python", "/app/backend/main.py"]
