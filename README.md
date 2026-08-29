# Photo Slideshow → MP4

A small, production-minded CLI tool that turns a folder of images into an MP4
video using system **FFmpeg**. Each image is shown for a configurable delay
(default **5 seconds**), with either **hard cuts** or **crossfade**
transitions. Settings come from a `.env` file, CLI flags, or defaults — with a
clear precedence.

## Features

- Configurable per-image delay (default 5s).
- Two transitions: `cut` (instant) and `crossfade` (configurable duration).
- **Per-image control**: a manifest (`--manifest file.json`, or the web UI's
  per-file fields) lets you set each image's on-screen duration and an optional
  caption, and fixes the slide order.
- **Captions**: burn an optional text overlay onto any image via the manifest
  (`caption`) — rendered with a bundled font, no system font needed.
- **EXIF auto-orient**: phone photos are uprighted automatically from their
  EXIF orientation tag (`--no-autorotate` to disable).
- **Audio track**: mux an optional background music file (`--audio`) with
  fade-in/out, volume, optional looping, and optional loudness normalization.
- **Letterbox scaling**: mixed sizes/orientations are fit (preserving aspect
  ratio) and padded with black bars into a uniform target resolution.
- **Ken Burns**: optional subtle slow zoom/pan per image (`--ken-burns`).
- **Hardware encoding**: `--encoder auto` (default) auto-detects the best
  usable GPU encoder — `h264_nvenc` (NVIDIA) → `h264_qsv` (Intel) →
  `h264_videotoolbox` (macOS) — and falls back to CPU `libx264`. Each encoder
  gets its optimal balanced preset. A functional probe ensures the GPU is
  actually usable, not just compiled in.
- Configuration via `.env` **and/or** CLI parameters.
- Clean UX: friendly errors for missing FFmpeg, empty folders, bad options;
  `--dry-run` to preview the FFmpeg command; a live progress bar in the CLI;
  non-zero exit codes on failure.
- H.264 / `yuv420p` / `+faststart` output for maximum player compatibility.

## Prerequisites

- Python 3.10+
- FFmpeg on your `PATH` (`ffmpeg -version` to verify)

Install Python deps:

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # for tests
```

## Usage

### Via `.env` (set-and-forget)

```bash
cp .env.example .env   # then edit paths/delay
python -m slideshow
```

### Via CLI flags (override any `.env` value)

```bash
python -m slideshow \
  --input-dir ./photos \
  --output ./output/slideshow.mp4 \
  --delay 5 \
  --transition crossfade \
  --crossfade 1 \
  --resolution 1920x1080 \
  --overwrite
```

### Preview without rendering

```bash
python -m slideshow --input-dir ./photos --output out.mp4 --dry-run
```

## Configuration precedence

**CLI flag > `.env` value > built-in default.**

| Setting         | CLI flag         | `.env` key           | Default      |
|-----------------|------------------|----------------------|--------------|
| Input directory | `--input-dir`    | `INPUT_DIR`          | _(required)_ |
| Output file     | `--output`       | `OUTPUT_FILE`        | _(required)_ |
| Delay (s)       | `--delay`        | `DELAY_SECONDS`      | `5`          |
| Transition      | `--transition`   | `TRANSITION`         | `cut`        |
| Crossfade (s)   | `--crossfade`    | `CROSSFADE_SECONDS`  | `1`          |
| Resolution      | `--resolution`   | `RESOLUTION`         | `1920x1080`  |
| Overwrite       | `--overwrite`    | `OVERWRITE`          | `false`      |
| Ken Burns         | `--ken-burns`    | `KEN_BURNS`          | `false`      |
| Audio file       | `--audio`        | `AUDIO_FILE`         | _(none)_     |
| Audio fade in    | `--audio-fade-in`| `AUDIO_FADE_IN`      | `1.0`        |
| Audio fade out   | `--audio-fade-out`| `AUDIO_FADE_OUT`    | `1.0`        |
| Audio volume     | `--audio-volume` | `AUDIO_VOLUME`       | `1.0`        |
| Audio loop       | `--audio-loop`   | `AUDIO_LOOP`         | `false`      |
| Audio normalize  | `--audio-normalize`| `AUDIO_NORMALIZE`   | `false`      |
| EXIF autorotate  | `--no-autorotate`| `AUTOROTATE`         | `true`       |
| Per-image manifest| `--manifest`    | _(n/a)_              | _(none)_     |
| Encoder         | `--encoder`      | `ENCODER`            | `auto`       |

## Project layout

```
slideshow/       # core Python library (CLI + engine)
  cli.py        # argument parsing & orchestration
  config.py     # Config model + .env/CLI merge + validation
  scanner.py    # image discovery & natural sort
  ffmpeg.py     # command construction & execution
  errors.py     # typed, user-facing exceptions
web/             # FastAPI web server + static file serving
  app.py        # REST API endpoints + Astro/React frontend mount
  jobs.py       # in-memory job registry + background render worker
frontend/        # Astro + React frontend (builds to web/static/)
  src/
    pages/      # Astro pages (index.astro)
    components/ # React components (App.tsx, styles.css)
tests/          # pytest unit + integration tests (no FFmpeg needed for most)
Makefile        # local dev shortcuts (make dev, make lint)
```

## How it works

Each image becomes an FFmpeg input (`-loop 1 -t <delay> -i file`), scaled to
fit and padded to the target resolution, then composited:

- **cut** → `concat` filter.
- **crossfade** → a chain of `xfade` filters. For N images of duration D and
  crossfade F, the j-th transition starts at offset `j * (D - F)`, yielding a
  final duration of `N*D - (N-1)*F`.

## Tests & Linting

All tests (unit + integration) run **inside Docker**, so ffmpeg executes only in
the container — never on the host. This avoids host/WSL2 issues from spawning
multiple ffmpeg processes. A `test` stage is built on top of the backend image
and a `docker-compose.test.yml` wires up the service.

```bash
# Build and run all tests (unit + integration) in the container
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test

# Run a specific test file
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test \
    pytest tests/test_ffmpeg_builder.py -v
```

> The integration tests use a committed fixture (`tests/fixtures/red.png`). A
> 1×1 PNG triggers an ffmpeg image2-demuxer bug when looped with `-loop 1`, so
> do not replace it with a degenerate image.

### Linting

Code quality is enforced with [ruff](https://docs.astral.sh/ruff/) (linting + formatting).

```bash
# Check code (no changes)
make lint

# Auto-fix lint issues + format
make format

# Lint in Docker (same image as tests)
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm lint
```

A pre-commit hook is also available:

```bash
pip install pre-commit
pre-commit install
```

### Run on Host (requires host ffmpeg — not recommended)

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

## Web UI

A small FastAPI service wraps the same engine: upload images, set options, watch
a **live progress bar**, and download — plus a job history gallery. The frontend
is an Astro app (React islands) served as static files.

```bash
pip install -r requirements.txt
uvicorn web.app:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

API (all under `/api`): `POST /api/render` (multipart files + form options),
`GET /api/jobs/{id}` (status + progress), `GET /api/jobs` (gallery),
`GET /api/jobs/{id}/download`, `DELETE /api/jobs/{id}`.

Environment overrides: `JOB_DIR` (job storage), `SLIDESHOW_MAX_WORKERS`
(concurrent renders, default 2), `SLIDESHOW_MAX_FILES`, `SLIDESHOW_MAX_FILE_BYTES`.

## Docker

The image is multi-stage: Node builds the Astro frontend, then a CUDA-runtime
Python image bundles an NVENC-capable ffmpeg. **Auto-detect uses the GPU
(NVENC) when the container is granted one, and falls back to CPU (libx264)
otherwise — same image.**

```bash
# CPU-only (any machine):
docker compose up --build

# GPU (NVIDIA Container Toolkit required): the compose file already reserves
# a GPU; just run normally and auto-detect picks h264_nvenc:
docker compose up --build
# -> http://localhost:8000
```

To run CPU-only on a GPU host, remove the `deploy.resources.reservations.devices`
block from `docker-compose.yml`.

### GPU / NVENC notes

Hardware encoding uses NVIDIA NVENC (`h264_nvenc`). A few things are wired
deliberately and should **not** be "tidied" away, or GPU encoding silently
falls back to CPU (or breaks outright):

- **`NVIDIA_DRIVER_CAPABILITIES` must include `video`.** Without it the NVIDIA
  encode library (`libnvidia-encode.so`) is never mounted into the container and
  ffmpeg can't open an NVENC session. It is set in `docker-compose.yml`.
- **All GPUs are exposed; the encode is pinned to GPU 1.** The compose file does
  *not* use `device_ids: ["1"]`. Pinning a single GPU only exposes
  `/dev/nvidia1` (no `/dev/nvidia0`), which makes NVENC report
  *"No capable devices found"*. Instead, all GPUs are visible so ffmpeg can
  enumerate the encoder, and the actual encode is pinned to GPU 1 via ffmpeg's
  `-gpu 1` flag (`NVENC_GPU_INDEX` in `slideshow/ffmpeg.py`). Change that
  constant if you want a different card.
- **`nv-codec-headers` is pinned to the NVENC API your driver exposes.**
  The Dockerfile pins tag `n13.0.19.1` (NVENC API 13.0) to match the host
  driver. If you instead track `nv-codec-headers` `master`, ffmpeg compiles
  against a newer NVENC API than the driver provides and fails at runtime with
  *"Driver does not support the required nvenc API version"*. **Only bump this
  pin when you upgrade the NVIDIA driver** — check the driver's NVENC API with
  `nvidia-smi` / the release notes first.
- **Image decoders are compiled in.** ffmpeg is built with `--enable-zlib`
  (PNG) and `--enable-libwebp`; without them, PNG/JPEG inputs fail to decode.
- **CPU fallback is automatic.** With no GPU attached, `--encoder auto` falls
  back to `libx264`.

## Per-image manifest

For fine control over ordering, per-image duration, and captions, pass a JSON
manifest (CLI: `--manifest file.json`; web: the per-file **Sec** and
**Caption** fields). It is a list of entries matched by file name:

```json
[
  { "name": "001.jpg", "duration": 6, "caption": "Beach day" },
  { "name": "002.jpg", "duration": 4, "caption": "Sunset" }
]
```

- `name` — the image file name in the input directory.
- `duration` — seconds the image is shown (defaults to the global delay).
- `caption` — optional text burned onto the image.

## Known limitations / next steps

- HEIC/HEIF inputs require a non-free FFmpeg build (excluded for safety).
- Job store is in-memory + on-disk; for multi-replica deployments swap it for
  Redis/Celery (the `web/jobs.py` interface stays the same).
