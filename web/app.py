"""FastAPI application: REST API + static serving of the Astro frontend.

The web layer is a thin composition root: it validates uploads, delegates the
actual work to :mod:`web.jobs` (which reuses the core slideshow engine), and
serves the built Astro assets. No slideshow/ffmpeg logic lives here.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.util import get_remote_address

from web import jobs
from web.sse import job_progress_stream

MAX_FILES = int(os.environ.get("SLIDESHOW_MAX_FILES", "200"))
MAX_FILE_BYTES = int(os.environ.get("SLIDESHOW_MAX_FILE_BYTES", str(20 * 1024 * 1024)))
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

app = FastAPI(title="Photo Slideshow Web", version="1.0.0")

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
def _resolve_static_dir() -> Path | None:
    """Locate the built frontend assets.

    Resolution order (first match wins):
      1. ``$SLIDESHOW_STATIC_DIR`` (explicit override, useful in containers/tests)
      2. ``web/static`` (Docker image layout, populated at build time)
      3. ``frontend/dist`` (local dev after ``npm run build``, no copy step)

    Returns ``None`` when no usable build is found so the API still boots and
    the missing-UI failure mode is obvious (404 on ``/``) rather than a crash.
    """
    candidates = [
        Path(os.environ.get("SLIDESHOW_STATIC_DIR", "")),
        Path(__file__).parent / "static",
        Path(__file__).parent.parent / "frontend" / "dist",
    ]
    for candidate in candidates:
        if candidate and candidate.is_dir() and (candidate / "index.html").exists():
            return candidate
    return None


STATIC_DIR = _resolve_static_dir()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.post("/api/render")
@limiter.limit("10/minute")
async def create_render(
    request: Request,
    files: list[UploadFile] = File(..., description="Image files to include"),
    delay: float = Form(5.0),
    transition: str = Form("cut"),
    crossfade: float = Form(1.0),
    resolution: str = Form("1920x1080"),
    ken_burns: bool = Form(False),
    encoder: str = Form("auto"),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES}).")
    if delay < 0.1 or delay > 300:
        raise HTTPException(status_code=400, detail="Delay must be between 0.1 and 300 seconds.")
    if crossfade < 0 or crossfade >= delay:
        raise HTTPException(status_code=400, detail="Crossfade must be >= 0 and < delay.")

    opts = {
        "delay": delay,
        "transition": transition,
        "crossfade": crossfade,
        "resolution": resolution,
        "ken_burns": ken_burns,
        "encoder": encoder,
    }

    job = jobs.new_job(opts)
    in_dir = jobs.input_dir_path(job.id)
    saved = 0
    try:
        for upload in files:
            if not jobs.allowed_extension(upload.filename or ""):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type: {upload.filename}",
                )
            safe_name = Path(upload.filename).name
            if not safe_name or safe_name.startswith("."):
                continue
            dest = in_dir / safe_name
            # Stream upload to disk in chunks to avoid loading entire file into memory.
            with open(dest, "wb") as out_f:
                while chunk := await upload.read(64 * 1024):
                    if out_f.tell() + len(chunk) > MAX_FILE_BYTES:
                        dest.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=400,
                            detail=f"File too large: {upload.filename}",
                        )
                    out_f.write(chunk)
            if dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
                continue
            saved += 1
    except HTTPException:
        jobs.delete(job.id)
        raise

    if saved == 0:
        jobs.delete(job.id)
        raise HTTPException(status_code=400, detail="No valid images provided.")

    jobs.start(job.id, opts)
    return {"job_id": job.id, "status": "processing"}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_dict()


@app.get("/api/jobs")
async def list_jobs(limit: int = 50):
    return [j.to_dict() for j in jobs.store.list_jobs(limit=limit)]


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str):
    job = jobs.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "done" or not job.output_file:
        raise HTTPException(status_code=409, detail="Output not ready.")
    return FileResponse(job.output_file, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.delete("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found or not cancellable.")
    return {"cancelled": True}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    if not jobs.delete(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"deleted": True}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    return StreamingResponse(
        job_progress_stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if STATIC_DIR is not None:
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
