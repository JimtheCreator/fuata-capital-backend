"""
presentation/schemas/upload.py
────────────────────────────────
Pydantic request/response models for /api/v1/upload endpoints.

Step 1 — Get upload URL:
    POST /upload/url
    Request:  GetUploadUrlRequest
    Response: GetUploadUrlResponse

Step 2 — Confirm upload done, kick off pipeline:
    POST /upload/confirm/{job_id}
    Response: ConfirmUploadResponse

Step 3 — Poll or stream status:
    GET /upload/status/{job_id}  → JobStatusResponse
    GET /upload/stream/{job_id}  → SSE stream (no Pydantic model needed)
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Request bodies ────────────────────────────────────────────────


class GetUploadUrlRequest(BaseModel):
    """Body for POST /upload/url"""

    filename: str = Field(
        ...,
        description="Original filename including extension (e.g. 'clients_june.csv')",
        examples=["clients_june.csv", "hp_list_q2.xlsx"],
    )
    notify_via_sse: bool = Field(
        default=True,
        description="Stream progress via SSE while app is in foreground.",
    )
    notify_via_firebase: bool = Field(
        default=True,
        description="Push final result via Firebase when app is backgrounded.",
    )


# ── Response bodies ───────────────────────────────────────────────


class GetUploadUrlResponse(BaseModel):
    """Response for POST /upload/url — hand this to the Android upload SDK."""

    job_id: str = Field(..., description="Track processing status with this ID.")
    upload_url: str = Field(..., description="Pre-signed PUT URL. Upload directly here.")
    file_path: str = Field(..., description="Storage path — needed internally.")
    expires_in: int = Field(..., description="Seconds until the upload URL expires.")
    confirm_endpoint: str = Field(
        ..., description="Call this endpoint after upload completes."
    )

    model_config = {"from_attributes": True}


class ConfirmUploadResponse(BaseModel):
    """Response for POST /upload/confirm/{job_id}"""

    job_id: str
    status: str   # Should be DOWNLOADING at this point
    message: str  # Human-readable — display in the app

    model_config = {"from_attributes": True}


class JobStatusResponse(BaseModel):
    """Response for GET /upload/status/{job_id} — REST poll."""

    job_id: str
    status: str                         # JobStatus enum value
    progress_pct: int = 0
    current_step: str = ""
    total_rows: int = 0
    parsed_rows: int = 0
    failed_rows: int = 0
    error_message: str = ""
    completed_at: Optional[str] = None  # ISO string or None

    model_config = {"from_attributes": True}