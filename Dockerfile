# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the Astro frontend into static assets
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python backend on an NVIDIA CUDA runtime base.
# The CUDA libs are present so an NVENC-capable ffmpeg can use the GPU when the
# container is launched with GPU access (see docker-compose.yml). Without a GPU
# the app's auto-detect falls back to libx264 automatically.
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS backend
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    JOB_DIR=/app/jobs

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ca-certificates wget xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Static ffmpeg build that includes NVENC (requires CUDA libs at runtime).
RUN wget -q https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -O /tmp/ff.tar.xz \
    && tar -xf /tmp/ff.tar.xz -C /tmp \
    && cp /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ffmpeg \
    && cp /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ffprobe \
    && rm -rf /tmp/ff.tar.xz /tmp/ffmpeg-*-amd64-static

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

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
RUN python3 -m pip install --no-cache-dir -r requirements-dev.txt
COPY tests/ ./tests/
WORKDIR /app
ENV PYTHONPATH=/app
CMD ["pytest", "tests/", "-v"]
