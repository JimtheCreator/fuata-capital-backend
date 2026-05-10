"""
infrastructure/repositories/supabase_job_repository.py
────────────────────────────────────────────────────────
DB reads/writes for UploadJob entities.

Table: upload_jobs
  id                uuid PK
  officer_id        text NOT NULL
  file_path         text NOT NULL
  file_type         text
  original_filename text
  status            text NOT NULL DEFAULT 'PENDING'
  progress_pct      int  DEFAULT 0
  current_step      text DEFAULT ''
  error_message     text DEFAULT ''
  detected_schema   jsonb DEFAULT '{}'
  total_rows        int  DEFAULT 0
  parsed_rows       int  DEFAULT 0
  failed_rows       int  DEFAULT 0
  notify_via_sse    bool DEFAULT true
  notify_via_firebase bool DEFAULT true
  created_at        timestamptz DEFAULT now()
  updated_at        timestamptz DEFAULT now()
  completed_at      timestamptz
"""

from __future__ import annotations
from datetime import datetime
from typing import Any

import structlog
from supabase import Client

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

import structlog

from core.domain.entities.upload_job import FileType, JobStatus, UploadJob
from core.domain.repositories.interfaces import IUploadJobRepository

TABLE = "upload_jobs"

log = structlog.get_logger(__name__)


class SupabaseJobRepository(IUploadJobRepository):
    def __init__(self, db: Client) -> None:
        self._db = db

    # ── Create ────────────────────────────────────────────────────

    async def create(self, job: UploadJob) -> UploadJob:
        row = _to_row(job)
        result = self._db.table(TABLE).insert(row).execute()
        return _from_row(result.data[0])

    # ── Read ──────────────────────────────────────────────────────

    async def get(self, job_id: str) -> UploadJob | None:
        result = (
            self._db.table(TABLE)
            .select("*")
            .eq("id", job_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            return None
        return _from_row(result.data)

    # ── Update ────────────────────────────────────────────────────

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        progress_pct: int | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        patch: dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if progress_pct is not None:
            patch["progress_pct"] = progress_pct
        if current_step is not None:
            patch["current_step"] = current_step
        if error_message is not None:
            patch["error_message"] = error_message
        if status in (JobStatus.DONE, JobStatus.FAILED):
            patch["completed_at"] = datetime.utcnow().isoformat()
        if extra:
            patch.update(extra)

        self._db.table(TABLE).update(patch).eq("id", job_id).execute()
        log.debug("job_status_updated", job_id=job_id, status=status.value)


# ── Serialisation helpers ─────────────────────────────────────────


def _to_row(job: UploadJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "officer_id": job.officer_id,
        "file_path": job.file_path,
        "file_type": job.file_type.value,
        "original_filename": job.original_filename,
        "status": job.status.value,
        "progress_pct": job.progress_pct,
        "current_step": job.current_step,
        "error_message": job.error_message,
        "detected_schema": job.detected_schema,
        "total_rows": job.total_rows,
        "parsed_rows": job.parsed_rows,
        "failed_rows": job.failed_rows,
        "notify_via_sse": job.notify_via_sse,
        "notify_via_firebase": job.notify_via_firebase,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def _from_row(row: dict[str, Any]) -> UploadJob:
    job = UploadJob()
    job.id = row["id"]
    job.officer_id = row["officer_id"]
    job.file_path = row["file_path"]
    job.file_type = FileType(row.get("file_type", "unknown"))
    job.original_filename = row.get("original_filename", "")
    job.status = JobStatus(row["status"])
    job.progress_pct = row.get("progress_pct", 0)
    job.current_step = row.get("current_step", "")
    job.error_message = row.get("error_message", "")
    job.detected_schema = row.get("detected_schema") or {}
    job.total_rows = row.get("total_rows", 0)
    job.parsed_rows = row.get("parsed_rows", 0)
    job.failed_rows = row.get("failed_rows", 0)
    job.notify_via_sse = row.get("notify_via_sse", True)
    job.notify_via_firebase = row.get("notify_via_firebase", True)
    job.created_at = _parse_dt(row.get("created_at"))
    job.updated_at = _parse_dt(row.get("updated_at"))
    job.completed_at = _parse_dt(row.get("completed_at")) if row.get("completed_at") else None
    return job


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.utcnow()
