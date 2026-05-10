"""
infrastructure/repositories/supabase_storage_repository.py
────────────────────────────────────────────────────────────
Pre-signed upload URLs + file download from Supabase Storage.

Flow:
  1. Android app calls  POST /upload/url
     → generate_presigned_upload_url() returns a signed PUT URL
     → Android uploads the file DIRECTLY to Supabase (server never sees bytes)

  2. Android calls POST /upload/confirm/{job_id}
     → Celery worker calls download_bytes() to pull the file for processing

Storage path convention:
  {bucket}/{officer_id}/{job_id}/{filename}

This namespacing makes it easy to:
  • Set RLS policies per-officer on the storage bucket
  • Clean up old files by officer
  • Avoid collisions
"""

from __future__ import annotations
from datetime import datetime
import mimetypes
from typing import Any
import uuid

import structlog
from supabase import Client

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

import structlog


from config import get_settings
from core.domain.repositories.interfaces import IStorageRepository

log = structlog.get_logger(__name__)

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm", ".pdf"}


class SupabaseStorageRepository(IStorageRepository):
    def __init__(self, db: Client) -> None:
        self._db = db
        self._s = get_settings()
        self._bucket = self._s.supabase_storage_bucket

    # ── Upload URL ────────────────────────────────────────────────

    async def generate_presigned_upload_url(
        self,
        officer_id: str,
        filename: str,
    ) -> dict[str, str]:
        """
        Creates a signed URL the Android app can PUT the file to.
        Returns {upload_url, file_path, expires_in}.

        The job_id slug in the path makes every upload unique even if
        the same filename is used multiple times.
        """
        _validate_extension(filename)

        # Unique path per upload
        slug = str(uuid.uuid4())[:8]
        safe_name = filename.replace(" ", "_")
        file_path = f"{officer_id}/{slug}/{safe_name}"

        ttl = self._s.upload_url_ttl_seconds

        response = self._db.storage.from_(self._bucket).create_signed_upload_url(
            file_path
        )

        # supabase-py v2 returns a dict: {signedURL, path, token}
        signed_url = response.get("signedURL") or response.get("signed_url", "")

        log.info(
            "presigned_url_created",
            officer_id=officer_id,
            file_path=file_path,
        )

        return {
            "upload_url": signed_url,
            "file_path": file_path,
            "expires_in": str(ttl),
        }

    # ── Download ──────────────────────────────────────────────────

    async def download_bytes(self, file_path: str) -> bytes:
        """
        Downloads the file from Supabase Storage and returns raw bytes.
        Called by the Celery worker during the DOWNLOADING phase.
        """
        log.info("downloading_file", file_path=file_path)
        data = self._db.storage.from_(self._bucket).download(file_path)
        log.info(
            "file_downloaded",
            file_path=file_path,
            size_kb=len(data) // 1024,
        )
        return data

    # ── Cleanup (optional, call after processing is done) ─────────

    async def delete_file(self, file_path: str) -> None:
        """
        Deletes a file after successful processing to save storage costs.
        Optional — don't call this if you want to keep originals for audit.
        """
        try:
            self._db.storage.from_(self._bucket).remove([file_path])
            log.info("file_deleted", file_path=file_path)
        except Exception as exc:
            log.warning("file_delete_failed", file_path=file_path, error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────


def _validate_extension(filename: str) -> None:
    import os
    ext = os.path.splitext(filename.lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )
