"""
Upload Router  /api/v1/upload
──────────────────────────────
POST /upload-url          → Get pre-signed URL + job_id
POST /upload/confirm/{job_id} → Tell server "upload done, start pipeline"
GET  /upload/status/{job_id}  → Poll job status (REST)
GET  /upload/stream/{job_id}  → SSE stream for real-time progress
"""

import asyncio
import json
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from .dependencies import (
    get_job_repo,
    get_killlist_repo,
    get_officer_uid,
    get_storage_repo,
)

from core.domain.entities.upload_job import JobStatus
from infrastructure.cache.redis_client import get_redis_client
from infrastructure.database.supabase.repo.supabase_job_repository import SupabaseJobRepository
from infrastructure.database.supabase.repo.supabase_killlist_repository import SupabaseKillListRepository
from infrastructure.database.supabase.repo.supabase_storage_repository import SupabaseStorageRepository
from infrastructure.workers.tasks import process_upload_job
from core.schemas.upload import (
    ConfirmUploadResponse,
    GetUploadUrlRequest,
    GetUploadUrlResponse,
    JobStatusResponse,
)
from core.use_cases.confirm_upload import (
    ConfirmUploadRequest, ConfirmUploadUseCase
)

from core.use_cases.generate_upload_url import (
    GenerateUploadUrlRequest,
    GenerateUploadUrlUseCase,
)

from core.use_cases.get_job_status import GetJobStatusUseCase
from utils.logger import logger

router = APIRouter(prefix="/upload", tags=["Upload"])
log = logger


# ── 1. Get Pre-signed Upload URL ─────────────────────────────

@router.post(
    "/url",
    response_model=GetUploadUrlResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a pre-signed URL to upload a client list directly to storage.",
)
async def get_upload_url(
    body: GetUploadUrlRequest,
    officer_uid: str = Depends(get_officer_uid),
    storage_repo: SupabaseStorageRepository = Depends(get_storage_repo),
    job_repo: SupabaseJobRepository = Depends(get_job_repo),
) -> GetUploadUrlResponse:
    use_case = GenerateUploadUrlUseCase(storage_repo, job_repo)
    result = await use_case.execute(
        GenerateUploadUrlRequest(
            officer_id=officer_uid,
            filename=body.filename,
            notify_via_sse=body.notify_via_sse,
            notify_via_firebase=body.notify_via_firebase,
        )
    )
    return GetUploadUrlResponse(
        job_id=result.job_id,
        upload_url=result.upload_url,
        file_path=result.file_path,
        expires_in=result.expires_in,
        confirm_endpoint=result.confirm_endpoint,
    )


# ── 2. Confirm Upload & Kick Off Pipeline ────────────────────

@router.post(
    "/confirm/{job_id}",
    response_model=ConfirmUploadResponse,
    summary="Call this after upload is complete. Starts the processing pipeline.",
)
async def confirm_upload(
    job_id: str,
    officer_uid: str = Depends(get_officer_uid),
    job_repo: SupabaseJobRepository = Depends(get_job_repo),
) -> ConfirmUploadResponse:
    def _enqueue(jid: str) -> None:
        process_upload_job.apply_async(args=[jid], countdown=1)

    use_case = ConfirmUploadUseCase(job_repo, enqueue_fn=_enqueue)
    try:
        result = await use_case.execute(
            ConfirmUploadRequest(job_id=job_id, officer_id=officer_uid)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return ConfirmUploadResponse(
        job_id=result.job_id,
        status=result.status,
        message=result.message,
    )


# ── 3. Poll Job Status ───────────────────────────────────────

@router.get(
    "/status/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll the status of a processing job.",
)
async def job_status(
    job_id: str,
    officer_uid: str = Depends(get_officer_uid),
    job_repo: SupabaseJobRepository = Depends(get_job_repo),
) -> JobStatusResponse:
    use_case = GetJobStatusUseCase(job_repo)
    try:
        result = await use_case.execute(job_id, officer_uid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return JobStatusResponse(**result.__dict__)


# ── 4. SSE Stream ────────────────────────────────────────────
@router.get(
    "/stream/{job_id}",
    summary="Server-Sent Events stream for real-time job progress.",
    response_class=StreamingResponse,
)
async def stream_job_progress(
    request: Request,
    job_id: str,
    officer_uid: str = Depends(get_officer_uid),
    job_repo: SupabaseJobRepository = Depends(get_job_repo),
) -> StreamingResponse:
    # Verify ownership
    job = await job_repo.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.officer_id != officer_uid:
        raise HTTPException(status_code=403, detail="Access denied.")

    async def event_generator() -> AsyncGenerator[str, None]:
        redis = get_redis_client()
        channel = f"job_progress:{officer_uid}:{job_id}"
        pubsub = redis.pubsub()
        pubsub.subscribe(channel)

        # Send current state immediately
        current = await job_repo.get(job_id)
        if current:
            data = json.dumps({
                "job_id": job_id,
                "status": current.status.value,
                "progress_pct": current.progress_pct,
                "step": current.current_step,
            })
            yield f"data: {data}\n\n"

        terminal_statuses = {JobStatus.DONE.value, JobStatus.FAILED.value}

        try:
            while True:
                # Check client disconnect
                if await request.is_disconnected():
                    break

                message = pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.1
                )
                if message and message["type"] == "message":
                    payload = message["data"]
                    yield f"data: {payload}\n\n"

                    # Unpack to check if terminal
                    try:
                        parsed = json.loads(payload)
                        if parsed.get("status") in terminal_statuses:
                            yield "event: done\ndata: {}\n\n"
                            break
                    except json.JSONDecodeError:
                        pass
                else:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(2)

        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )