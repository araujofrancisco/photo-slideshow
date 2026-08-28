# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the Astro frontend into static assets
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python backend on an NVIDIA CUDA runtime base.
# ffmpeg is compiled from source with NVENC support (see the build below) so the
# app can use the GPU for H.264 encoding when the container is launched with GPU
# access (see docker-compose.yml, which also sets NVIDIA_DRIVER_CAPABILITIES to
# include "video"). Without a GPU the app's auto-detect falls back to libx264.
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS backend
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    JOB_DIR=/app/jobs

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ca-certificates wget xz-utils \
        build-essential nasm pkg-config git \
        libx264-dev libx265-dev \
        zlib1g-dev libpng-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# nv-codec-headers (ffnvcodec): provides the NVENC/NVDEC codec headers ffmpeg
# needs to compile hardware support. We build from source and PIN the tag to the
# NVENC API the HOST DRIVER actually exposes, otherwise ffmpeg compiles against a
# newer NVENC API than the driver provides and fails at runtime with
# "Driver does not support the required nvenc API version". This host's driver
# (580.x) exposes NVENC API 13.0, so we pin the matching headers (n13.0.19.1).
ENV NV_CODEC_HEADERS_TAG=n13.0.19.1
RUN for i in 1 2 3 4 5; do \
        git clone --depth 1 --branch "${NV_CODEC_HEADERS_TAG}" https://github.com/FFmpeg/nv-codec-headers.git /tmp/nv-codec-headers \
        && break || echo "git clone attempt $i failed, retrying..."; \
        sleep 5; \
    done \
    && cd /tmp/nv-codec-headers \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /tmp/nv-codec-headers

# Compile ffmpeg from source with NVENC enabled. NVENC only needs the ffnvcodec
# headers at build time (no CUDA toolkit / nvcc required). At runtime the
# NVIDIA "video" driver capability (see docker-compose.yml) mounts
# libnvidia-encode.so into the container so the encoder actually works.
ENV FFMPEG_VERSION=7.0.2
RUN wget -q "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" -O /tmp/ffmpeg.tar.xz \
    && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
    && cd /tmp/ffmpeg-${FFMPEG_VERSION} \
    && ./configure \
        --prefix=/usr/local \
        --enable-gpl \
        --enable-libx264 \
        --enable-libx265 \
        --enable-libwebp \
        --enable-zlib \
        --enable-nvenc \
        --disable-debug \
        --disable-doc \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-${FFMPEG_VERSION}

WORKDIR /app
COPY requirements.txt ./
# Retry pip so a transient DNS/network blip (e.g. "Temporary failure in name
# resolution") doesn't abort an otherwise-good multi-minute build.
RUN for i in 1 2 3 4 5; do \
        python3 -m pip install --no-cache-dir --retries 10 --timeout 60 \
            -r requirements.txt && break \
        || echo "pip attempt $i failed, retrying..."; \
        sleep 5; \
    done && python3 -m pip check

COPY slideshow/ ./slideshow/
COPY web/ ./web/
COPY --from=frontend /frontend/dist/ ./web/static/

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# Stage 3: Test runner (includes test deps + test files)
# ---------------------------------------------------------------------------
FROM backend AS test
COPY requirements-dev.txt ./
RUN for i in 1 2 3 4 5; do \
        python3 -m pip install --no-cache-dir --retries 10 --timeout 60 \
            -r requirements-dev.txt && break \
        || echo "pip attempt $i failed, retrying..."; \
        sleep 5; \
    done && python3 -m pip check
COPY tests/ ./tests/
WORKDIR /app
ENV PYTHONPATH=/app
CMD ["pytest", "tests/", "-v"]
