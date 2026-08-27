"""In-memory job registry + background worker for the web UI.

Responsibilities (single responsibility):
  * Persist uploaded jobs to disk under ``$JOB_DIR/<id>/`` (input/ + output.mp4).
  * Build a :class:`slideshow.config.Config` from web form options (reusing the
    same validation as the CLI).
  * Run the render in a bounded thread pool so HTTP requests never block, and
    stream live progress back via a callback.

The store is intentionally in-memory (plus on-disk artifacts). For multi-process
or multi-replica deployments, swap this for Redis/Celery -- the interface
(``new_job`` / ``start`` / ``get`` / ``list`` / ``delete``) would stay the same.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from slideshow.config import Config, parse_resolution
from slideshow.errors import SlideshowError
from slideshow.ffmpeg import render
from slideshow.scanner import SUPPORTED_EXTENSIONS, find_images

JOB_ROOT = Path(os.environ.get("JOB_DIR", "jobs")).resolve()
JOB_ROOT.mkdir(parents=True, exist_ok=True)

MAX_WORKERS = int(os.environ.get("SLIDESHOW_MAX_WORKERS", "2"))


@dataclass
class Job:
    id: str
    created_at: float
    status: str = "queued"  # queued | processing | done | error
    progress: float = 0.0
    options: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    output_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at
        data["download_url"] = f"/api/jobs/{self.id}/download" if self.status == "done" else None
        return data


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                setattr(job, key, value)

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]


store = JobStore()

# Worker pool used to run ffmpeg off the request path.
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
# for _ in range(MAX_WORKERS):
#     executor.submit(lambda: None).result()


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def input_dir_path(job_id: str) -> Path:
    return JOB_ROOT / job_id / "input"


def output_path(job_id: str) -> Path:
    return JOB_ROOT / job_id / "output.mp4"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def new_job(opts: dict[str, Any]) -> Job:
    job_id = uuid.uuid4().hex
    input_dir_path(job_id).mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, created_at=time.time(), options=opts)
    store.add(job)
    return job


def start(job_id: str, opts: dict[str, Any]) -> None:
    """Mark the job as processing and enqueue the render on the worker pool."""
    store.update(job_id, status="processing", options=opts)
    executor.submit(run_job, job_id, opts)


def delete(job_id: str) -> bool:
    job = store.get(job_id)
    if job is None:
        return False
    shutil.rmtree(JOB_ROOT / job_id, ignore_errors=True)
    store.remove(job_id)
    return True


def _build_config(opts: dict[str, Any], in_dir: Path, out_file: Path) -> Config:
    resolution = opts.get("resolution") or "1920x1080"
    width, height = parse_resolution(resolution)
    kb = opts.get("ken_burns")
    ken_burns = kb is True or (
        isinstance(kb, str) and kb.strip().lower() in ("true", "on", "1", "yes")
    )
    cfg = Config(
        input_dir=in_dir,
        output_file=out_file,
        delay_seconds=float(opts.get("delay") or 5),
        transition=str(opts.get("transition") or "cut"),
        crossfade_seconds=float(opts.get("crossfade") or 1),
        width=width,
        height=height,
        overwrite=True,  # per-job output path is unique, safe to overwrite
        ken_burns=ken_burns,
        encoder=str(opts.get("encoder") or "auto"),
    )
    cfg.validate()
    return cfg


def run_job(job_id: str, opts: dict[str, Any]) -> None:
    """Execute the render for ``job_id`` and update its status.

    Intended to run as a background task (anyio/FastAPI ``BackgroundTasks``),
    which keeps it off the request path while still using a thread that the
    event loop manages -- important because spawning ffmpeg from a raw
    ``ThreadPoolExecutor`` worker under an async server can deadlock.
    """
    try:
        in_dir = input_dir_path(job_id)
        images = find_images(in_dir)
        cfg = _build_config(opts, in_dir, output_path(job_id))
        render(
            cfg,
            images,
            progress_callback=lambda pct: store.update(job_id, progress=round(pct, 1)),
        )
        store.update(
            job_id,
            status="done",
            progress=100.0,
            output_file=str(output_path(job_id)),
        )
    except SlideshowError as exc:
        store.update(job_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures to UI
        store.update(job_id, status="error", error=f"Unexpected error: {exc}")


def allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS
