"""FastAPI application: REST API + static serving of the Astro frontend.

The web layer is a thin composition root: it validates uploads, delegates the
actual work to :mod:`web.jobs` (which reuses the core slideshow engine), and
serves the built Astro assets. No slideshow/ffmpeg logic lives here.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web import jobs

MAX_FILES = int(os.environ.get("SLIDESHOW_MAX_FILES", "200"))
MAX_FILE_BYTES = int(os.environ.get("SLIDESHOW_MAX_FILE_BYTES", str(20 * 1024 * 1024)))

app = FastAPI(title="Photo Slideshow Web", version="1.0.0")


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


@app.post("/api/render")
async def create_render(
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
            data = await upload.read()
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large: {upload.filename}",
                )
            if not data:
                continue
            safe_name = Path(upload.filename).name
            (in_dir / safe_name).write_bytes(data)
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


if STATIC_DIR is not None:
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
