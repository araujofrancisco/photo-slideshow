"""Tests for web/app.py edge cases and web/jobs.py persistence + cleanup."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from web import jobs
from web.app import app

# Disable the per-IP rate limit so the test suite isn't throttled when it
# issues many render requests in quick succession.
app.state.limiter.enabled = False

# Realistically sized 320x240 test image.
PNG = (Path(__file__).parent / "fixtures" / "red.png").read_bytes()


def _client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
def test_health_endpoint():
    client = _client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------
def test_too_many_files_rejected():
    client = _client()
    files = [("files", (f"img{i}.png", PNG, "image/png")) for i in range(250)]
    resp = client.post("/api/render", files=files, data={"delay": "1"})
    assert resp.status_code == 400
    assert "Too many files" in resp.json()["detail"]


def test_empty_filename_rejected():
    client = _client()
    files = [("files", (".hidden", PNG, "image/png"))]
    resp = client.post("/api/render", files=files, data={"delay": "2"})
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_delay_out_of_range_rejected():
    client = _client()
    files = [("files", ("img.png", PNG, "image/png"))]
    resp = client.post("/api/render", files=files, data={"delay": "-1"})
    assert resp.status_code == 400
    assert "Delay" in resp.json()["detail"]

    resp = client.post("/api/render", files=files, data={"delay": "999"})
    assert resp.status_code == 400


def test_crossfade_greater_than_delay_rejected():
    client = _client()
    files = [("files", ("img.png", PNG, "image/png"))]
    resp = client.post("/api/render", files=files, data={"delay": "1", "crossfade": "2"})
    assert resp.status_code == 400
    assert "Crossfade" in resp.json()["detail"]


def test_unsupported_type_rejected():
    client = _client()
    files = [("files", ("notes.txt", b"hello", "text/plain"))]
    resp = client.post("/api/render", files=files, data={"delay": "2"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Download not ready
# ---------------------------------------------------------------------------
def test_download_not_ready_returns_409():
    client = _client()
    resp = client.post(
        "/api/render",
        files=[("files", ("img.png", PNG, "image/png"))],
        data={"delay": "2", "resolution": "320x240"},
    )
    job_id = resp.json()["job_id"]
    dl = client.get(f"/api/jobs/{job_id}/download")
    assert dl.status_code == 409


def test_cancel_nonexistent_returns_404():
    client = _client()
    resp = client.delete("/api/jobs/nonexistent/cancel")
    assert resp.status_code == 404


def test_delete_nonexistent_returns_404():
    client = _client()
    resp = client.delete("/api/jobs/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# JobStore persistence
# ---------------------------------------------------------------------------
def test_job_store_persists_to_json(tmp_path):
    store_file = tmp_path / ".jobs.json"
    store = jobs.JobStore(store_file)

    job = jobs.Job(id="test123", created_at=time.time(), status="done", output_file="/out.mp4")
    store.add(job)

    # Reload from disk.
    store2 = jobs.JobStore(store_file)
    loaded = store2.get("test123")
    assert loaded is not None
    assert loaded.status == "done"
    assert loaded.output_file == "/out.mp4"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def test_cleanup_removes_old_terminal_jobs(tmp_path):
    store_file = tmp_path / ".jobs.json"
    store = jobs.JobStore(store_file)

    # Create an old "done" job (created 8 days ago, TTL is 7 days).
    old_job = jobs.Job(
        id="old1",
        created_at=time.time() - (8 * 86400),
        status="done",
    )
    store.add(old_job)

    # Create a recent "done" job.
    new_job = jobs.Job(
        id="new1",
        created_at=time.time(),
        status="done",
    )
    store.add(new_job)

    removed = store.cleanup()
    assert removed == 1
    assert store.get("old1") is None
    assert store.get("new1") is not None


def test_cleanup_does_not_remove_processing_jobs(tmp_path):
    store_file = tmp_path / ".jobs.json"
    store = jobs.JobStore(store_file)

    job = jobs.Job(
        id="proc1",
        created_at=time.time() - (10 * 86400),
        status="processing",
    )
    store.add(job)

    removed = store.cleanup()
    assert removed == 0
    assert store.get("proc1") is not None
