"""In-memory job registry + background worker for the web UI.

Responsibilities (single responsibility):
  * Persist uploaded jobs to disk under ``$JOB_DIR/<id>/`` (input/ + output.mp4).
  * Build a :class:`slideshow.config.Config` from web form options (reusing the
    same validation as the CLI).
  * Run the render in a bounded thread pool so HTTP requests never block, and
    stream live progress back via a callback.

The store is backed by a JSON file (``$JOB_DIR/.jobs.json``) so job state
survives server restarts. For multi-process or multi-replica deployments, swap
this for Redis/Celery -- the interface stays the same.
"""

from __future__ import annotations

import json
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
from slideshow.errors import CancelError, SlideshowError
from slideshow.ffmpeg import render
from slideshow.scanner import SUPPORTED_EXTENSIONS, MediaItem, scan_items

JOB_ROOT = Path(os.environ.get("JOB_DIR", "jobs")).resolve()
JOB_ROOT.mkdir(parents=True, exist_ok=True)

MAX_WORKERS = int(os.environ.get("SLIDESHOW_MAX_WORKERS", "2"))
JOB_TTL_HOURS = int(os.environ.get("SLIDESHOW_JOB_TTL_HOURS", "168"))  # 7 days
MAX_JOBS = int(os.environ.get("SLIDESHOW_MAX_JOBS", "200"))
STORE_FILE = JOB_ROOT / ".jobs.json"

# Terminal states -- jobs in these states are eligible for cleanup.
_TERMINAL_STATES = frozenset({"done", "error", "cancelled"})


@dataclass
class Job:
    id: str
    created_at: float
    status: str = "queued"  # queued | processing | done | error | cancelled
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
    """JSON-file-backed job store with thread-safe access."""

    def __init__(self, store_file: Path) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._store_file = store_file
        self._load()

    # -- Persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load jobs from disk if the store file exists."""
        if not self._store_file.exists():
            return
        try:
            data = json.loads(self._store_file.read_text(encoding="utf-8"))
            for raw in data.get("jobs", []):
                job = Job(
                    id=raw["id"],
                    created_at=raw["created_at"],
                    status=raw.get("status", "queued"),
                    progress=raw.get("progress", 0.0),
                    options=raw.get("options", {}),
                    error=raw.get("error"),
                    output_file=raw.get("output_file"),
                )
                self._jobs[job.id] = job
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupted store -- start fresh.

    def _save(self) -> None:
        """Persist current state to disk (called under lock)."""
        data = {"jobs": [asdict(j) for j in self._jobs.values()]}
        self._store_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- CRUD ---------------------------------------------------------------

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._save()

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
            self._save()

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._save()

    def list_jobs(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    # -- Cleanup ------------------------------------------------------------

    def cleanup(self) -> int:
        """Remove old terminal jobs and their disk artifacts.

        Returns the number of jobs removed.
        """
        cutoff = time.time() - (JOB_TTL_HOURS * 3600)
        removed = 0
        with self._lock:
            to_delete = [
                jid
                for jid, job in self._jobs.items()
                if job.status in _TERMINAL_STATES and job.created_at < cutoff
            ]
            for jid in to_delete:
                shutil.rmtree(JOB_ROOT / jid, ignore_errors=True)
                self._jobs.pop(jid, None)
                removed += 1
            # Also enforce max jobs limit (oldest terminal first).
            if len(self._jobs) > MAX_JOBS:
                terminal = sorted(
                    [(jid, j) for jid, j in self._jobs.items() if j.status in _TERMINAL_STATES],
                    key=lambda x: x[1].created_at,
                )
                excess = len(self._jobs) - MAX_JOBS
                for jid, _ in terminal[:excess]:
                    shutil.rmtree(JOB_ROOT / jid, ignore_errors=True)
                    self._jobs.pop(jid, None)
                    removed += 1
            if removed:
                self._save()
        return removed


store = JobStore(STORE_FILE)

# Worker pool used to run ffmpeg off the request path.
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# Per-job cancel flags. ``run_job`` registers an Event; ``cancel`` sets it, and
# the ffmpeg render polls it so the encode is actually terminated (not just
# marked cancelled in the UI).
_cancel_events: dict[str, threading.Event] = {}


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
    _cancel_events[job_id] = threading.Event()
    executor.submit(run_job, job_id, opts)


def delete(job_id: str) -> bool:
    job = store.get(job_id)
    if job is None:
        return False
    _cancel_events.pop(job_id, None)
    shutil.rmtree(JOB_ROOT / job_id, ignore_errors=True)
    store.remove(job_id)
    return True


def cancel(job_id: str) -> bool:
    """Mark a queued or processing job as cancelled and stop the ffmpeg encode.

    Setting the job's cancel Event makes :func:`run_job` terminate the running
    ffmpeg process (instead of only flipping the UI status).
    """
    job = store.get(job_id)
    if job is None:
        return False
    if job.status not in ("queued", "processing"):
        return False
    event = _cancel_events.get(job_id)
    if event is not None:
        event.set()
    store.update(job_id, status="cancelled", error="Cancelled by user")
    return True


def _build_config(opts: dict[str, Any], in_dir: Path, out_file: Path) -> Config:
    resolution = opts.get("resolution") or "1920x1080"
    width, height = parse_resolution(resolution)
    kb = opts.get("ken_burns")
    ken_burns = kb is True or (
        isinstance(kb, str) and kb.strip().lower() in ("true", "on", "1", "yes")
    )
    audio_file = opts.get("audio_file")
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
        bitrate=str(opts.get("bitrate") or "auto"),
        crf=int(opts.get("crf") or 23),
        audio_file=Path(audio_file) if audio_file else None,
        audio_fade_in=float(opts.get("audio_fade_in") or 1.0),
        audio_fade_out=float(opts.get("audio_fade_out") or 1.0),
        audio_volume=float(opts.get("audio_volume") or 1.0),
        audio_loop=bool(opts.get("audio_loop") or False),
        audio_normalize=bool(opts.get("audio_normalize") or False),
        autorotate=not bool(opts.get("no_autorotate") or False),
    )
    cfg.validate()
    return cfg


def _build_items(job_id: str, opts: dict[str, Any], default_duration: float) -> list[MediaItem]:
    """Build the slide list for a job from its per-image options (if any)."""
    in_dir = input_dir_path(job_id)
    manifest = opts.get("items")
    if manifest:
        return scan_items(in_dir, manifest, default_duration=default_duration)
    return scan_items(in_dir, default_duration=default_duration)


def run_job(job_id: str, opts: dict[str, Any]) -> None:
    """Execute the render for ``job_id`` and update its status."""
    try:
        in_dir = input_dir_path(job_id)
        cfg = _build_config(opts, in_dir, output_path(job_id))
        items = _build_items(job_id, opts, cfg.delay_seconds)
        event = _cancel_events.get(job_id)

        def _cancelled() -> bool:
            return event is not None and event.is_set()

        render(
            cfg,
            items,
            progress_callback=lambda pct: store.update(job_id, progress=round(pct, 1)),
            cancel_check=_cancelled,
        )
        store.update(
            job_id,
            status="done",
            progress=100.0,
            output_file=str(output_path(job_id)),
        )
    except CancelError:
        # Status already flipped to "cancelled" by cancel(); leave it.
        store.update(job_id, progress=0.0)
    except SlideshowError as exc:
        store.update(job_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures to UI
        store.update(job_id, status="error", error=f"Unexpected error: {exc}")


def allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


# Audio formats FFmpeg can decode for background music.
AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".oga",
    ".flac",
    ".mp4",
    ".mov",
}


def allowed_audio_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in AUDIO_EXTENSIONS
