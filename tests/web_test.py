"""Integration tests for the web API using FastAPI's TestClient.

These exercise the full path: upload -> background render -> progress -> download,
reusing the real ffmpeg engine. JOB_DIR is set by docker-compose.test.yml.
"""

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient  # noqa: E402
from web.app import app  # noqa: E402

# Realistically sized 320x240 test image. NOTE: a 1x1 PNG triggers an ffmpeg
# image2-demuxer bug ("Failed to reallocate parser buffer") when looped with
# -loop 1, so tests must use a non-degenerate image.
PNG = (Path(__file__).parent / "fixtures" / "red.png").read_bytes()


def _client():
    return TestClient(app)


def _post_render(client, n=2):
    files = [("files", (f"img{i}.png", PNG, "image/png")) for i in range(n)]
    data = {
        "delay": "2",
        "transition": "cut",
        "resolution": "320x240",
        "encoder": "auto",
    }
    return client.post("/api/render", files=files, data=data)


def _wait_done(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.5)
    return job


def test_render_flow_and_download():
    client = _client()
    resp = _post_render(client, n=2)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    job = _wait_done(client, job_id)
    assert job["status"] == "done", job.get("error")
    assert job["progress"] == 100.0
    assert job["download_url"]

    dl = client.get(f"/api/jobs/{job_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("video/mp4")
    assert len(dl.content) > 0


def test_no_files_rejected():
    client = _client()
    resp = client.post("/api/render", files=[], data={"delay": "1"})
    assert resp.status_code == 422


def test_unsupported_type_rejected():
    client = _client()
    files = [("files", ("notes.txt", b"hello", "text/plain"))]
    resp = client.post("/api/render", files=files, data={"delay": "1"})
    assert resp.status_code == 400


def test_list_and_delete():
    client = _client()
    resp = _post_render(client, n=2)
    job_id = resp.json()["job_id"]
    _wait_done(client, job_id)

    listing = client.get("/api/jobs").json()
    assert any(j["id"] == job_id for j in listing)

    deleted = client.delete(f"/api/jobs/{job_id}")
    assert deleted.status_code == 200
    # gallery no longer contains it
    listing = client.get("/api/jobs").json()
    assert not any(j["id"] == job_id for j in listing)
