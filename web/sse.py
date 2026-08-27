"""SSE progress streaming for live job updates."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from web import jobs


async def job_progress_stream(job_id: str) -> AsyncGenerator[str, None]:
    """Yield SSE events for a job's progress until it reaches a terminal state."""
    last_status = None
    last_progress = -1.0
    for _ in range(3600):  # max 1 hour
        job = jobs.store.get(job_id)
        if job is None:
            yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
            return
        if job.status != last_status or job.progress != last_progress:
            data = job.to_dict()
            yield f"data: {json.dumps(data)}\n\n"
            last_status = job.status
            last_progress = job.progress
            if job.status in ("done", "error", "cancelled"):
                return
        await asyncio.sleep(1)
