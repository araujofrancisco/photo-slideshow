"""Integration tests for the web API using FastAPI's TestClient.

These exercise the full path: upload -> background render -> progress -> download,
reusing the real ffmpeg engine. JOB_DIR is set by docker-compose.test.yml.
"""

import json
import subprocess
import time
import wave
from pathlib import Path

from fastapi.testclient import TestClient  # noqa: E402

from web.app import app  # noqa: E402

# Disable the per-IP rate limit so the test suite isn't throttled when it
# issues many render requests in quick succession.
app.state.limiter.enabled = False

# Realistically sized 320x240 test image. NOTE: a 1x1 PNG triggers an ffmpeg
# image2-demuxer bug ("Failed to reallocate parser buffer") when looped with
# -loop 1, so tests must use a non-degenerate image.
PNG = (Path(__file__).parent / "fixtures" / "red.png").read_bytes()


def _make_wav(seconds: int = 2, rate: int = 44100) -> bytes:
    """Build a valid (silent) WAV so ffmpeg can decode it as background audio."""
    path = "/tmp/_slide_test_audio.wav"
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (rate * seconds))
    return Path(path).read_bytes()


def _has_audio_stream(path: Path) -> bool:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return "audio" in out.stdout.split()


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


def test_render_with_items_manifest_and_audio():
    client = _client()
    files = [("files", (f"img{i}.png", PNG, "image/png")) for i in range(2)]
    files.append(("audio", ("music.wav", _make_wav(seconds=5), "audio/wav")))
    items = [
        {"name": "0000_img0.png", "duration": 2, "caption": "first"},
        {"name": "0001_img1.png", "duration": 2, "caption": "second"},
    ]
    data = {
        "delay": "2",
        "transition": "cut",
        "resolution": "320x240",
        "encoder": "auto",
        "items": json.dumps(items),
        "audio_loop": "true",
    }
    resp = client.post("/api/render", files=files, data=data)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    job = _wait_done(client, job_id)
    assert job["status"] == "done", job.get("error")

    dl = client.get(f"/api/jobs/{job_id}/download")
    assert dl.status_code == 200
    out_path = Path(f"/tmp/_web_audio_out_{job_id}.mp4")
    out_path.write_bytes(dl.content)
    assert _has_audio_stream(out_path)


def test_invalid_items_json_rejected():
    client = _client()
    files = [("files", ("img0.png", PNG, "image/png"))]
    resp = client.post(
        "/api/render",
        files=files,
        data={"delay": "1", "items": "not-json"},
    )
    assert resp.status_code == 400


def test_cancel_stops_render():
    client = _client()
    # A long render we can cancel mid-flight.
    files = [("files", ("img0.png", PNG, "image/png"))]
    data = {
        "delay": "30",
        "transition": "cut",
        "resolution": "1280x720",
        "encoder": "libx264",
    }
    resp = client.post("/api/render", files=files, data=data)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    # Give the worker a moment to start, then cancel.
    time.sleep(0.5)
    cancel = client.delete(f"/api/jobs/{job_id}/cancel")
    assert cancel.status_code == 200

    deadline = time.time() + 15
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("cancelled", "done", "error"):
            break
        time.sleep(0.3)
    # The encode should have been cancelled (not left running to completion).
    assert job["status"] == "cancelled", job
