# Photo Slideshow → MP4

A small, production-minded CLI tool that turns a folder of images into an MP4
video using system **FFmpeg**. Each image is shown for a configurable delay
(default **5 seconds**), with either **hard cuts** or **crossfade**
transitions. Settings come from a `.env` file, CLI flags, or defaults — with a
clear precedence.

## Features

- Configurable per-image delay (default 5s).
- Two transitions: `cut` (instant) and `crossfade` (configurable duration).
- Letterbox scaling: mixed sizes/orientations are fit (preserving aspect
  ratio) and padded with black bars into a uniform target resolution.
- **Ken Burns**: optional subtle slow zoom/pan per image (`--ken-burns`).
- **Hardware encoding**: `--encoder auto` (default) auto-detects the best
  usable GPU encoder — `h264_nvenc` (NVIDIA) → `h264_qsv` (Intel) →
  `h264_videotoolbox` (macOS) — and falls back to CPU `libx264`. Each encoder
  gets its optimal balanced preset. A functional probe ensures the GPU is
  actually usable, not just compiled in.
- Configuration via `.env` **and/or** CLI parameters.
- Clean UX: friendly errors for missing FFmpeg, empty folders, bad options;
  `--dry-run` to preview the FFmpeg command; non-zero exit codes on failure.
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
| Ken Burns       | `--ken-burns`    | `KEN_BURNS`          | `false`      |
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

## Known limitations / next steps

- One global delay (no per-image durations yet).
- No audio track support yet.
- HEIC/HEIF inputs require a non-free FFmpeg build (excluded for safety).
- Job store is in-memory + on-disk; for multi-replica deployments swap it for
  Redis/Celery (the `web/jobs.py` interface stays the same).
