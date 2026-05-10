"""
Celery Tasks
─────────────
process_upload_job  — The entire ETL + AI pipeline for one upload job.

Progress is broadcast in two ways:
1. Redis pub/sub channel  → SSE endpoint picks it up
2. Firebase Realtime DB   → Android app listener fires
"""

from __future__ import annotations
import asyncio
import json
from datetime import datetime


import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)


from .celery_app import celery_app
from config import get_settings
from app.core.domain.entities.upload_job import JobStatus
from app.infrastructure.database.supabase.supabase_client import get_supabase
from app.infrastructure.database.supabase.repo.supabase_job_repository import SupabaseJobRepository
from app.infrastructure.database.supabase.repo.supabase_client_repository import SupabaseClientRepository
from app.infrastructure.database.supabase.repo.supabase_killlist_repository import SupabaseKillListRepository
from app.infrastructure.database.supabase.repo.supabase_storage_repository import SupabaseStorageRepository
from app.infrastructure.services.firebase_service import FirebaseAuthService
from app.infrastructure.parsers.file_parser import FileParserService
from ..services.column_mapper_service import ColumnMapperService
from ..services.data_cleaner_service import DataCleanerService
from ..services.ai_strategy_service import AIStrategyService
from ..cache.redis_client import get_redis_client

from utils.logger import logger as log

logger = log


def _broadcast(officer_id: str, job_id: str, payload: dict) -> None:
    """Publish progress to Redis channel for SSE consumers."""
    try:
        redis = get_redis_client()
        channel = f"job_progress:{officer_id}:{job_id}"
        redis.publish(channel, json.dumps(payload))
    except Exception as exc:
        logger.warning("redis_broadcast_failed", error=str(exc))


def _update_firebase(
    firebase: FirebaseAuthService,
    officer_id: str,
    job_id: str,
    payload: dict,
) -> None:
    try:
        firebase.notify_job_done(officer_id, job_id, payload)
    except Exception as exc:
        logger.warning("firebase_update_failed", error=str(exc))


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="fuata_capital_workers.process_upload_job",
)
def process_upload_job(self, job_id: str) -> dict:
    """
    Full pipeline:
    DOWNLOADING → PARSING → MAPPING_COLUMNS → INSERTING → BUILDING_KILL_LIST → DONE
    """
    log = logger.bind(job_id=job_id)

    # ── Wire up dependencies ─────────────────────────────────────
    db = get_supabase()
    job_repo = SupabaseJobRepository(db)
    client_repo = SupabaseClientRepository(db)
    killlist_repo = SupabaseKillListRepository(db)
    storage_repo = SupabaseStorageRepository(db)
    firebase = FirebaseAuthService()
    parser = FileParserService()
    mapper = ColumnMapperService()
    cleaner = DataCleanerService()
    ai_strategy = AIStrategyService()

    def step(status: JobStatus, pct: int, msg: str) -> None:
        asyncio.run(
            job_repo.update_status(
                job_id, status, progress_pct=pct, current_step=msg
            )
        )
        broadcast_payload = {
            "job_id": job_id,
            "status": status.value,
            "progress_pct": pct,
            "step": msg,
        }
        _broadcast(officer_id, job_id, broadcast_payload)

    try:
        # ── Fetch job ────────────────────────────────────────────
        job = asyncio.run(job_repo.get(job_id))
        if not job:
            raise ValueError(f"Job {job_id} not found in DB.")
        officer_id = job.officer_id

        log = log.bind(officer_id=officer_id)
        log.info("pipeline_start")

        # ── Phase 1: Download ────────────────────────────────────
        step(JobStatus.DOWNLOADING, 10, "Downloading file from storage")
        file_bytes = asyncio.run(storage_repo.download_bytes(job.file_path))
        log.info("file_downloaded", size_kb=len(file_bytes) // 1024)

        # ── Phase 2: Parse ───────────────────────────────────────
        step(JobStatus.PARSING, 25, "Parsing file")
        file_type, raw_rows, col_names = parser.parse(
            file_bytes, job.original_filename
        )

        asyncio.run(
            job_repo.update_status(
                job_id,
                JobStatus.PARSING,
                extra={
                    "file_type": file_type.value,
                    "total_rows": len(raw_rows),
                },
            )
        )
        log.info("file_parsed", rows=len(raw_rows), columns=col_names)

        if not raw_rows:
            raise ValueError(
                "No data rows found in the file. "
                "Please ensure the file has headers and data rows."
            )

        # ── Phase 3: AI Column Mapping ───────────────────────────
        step(JobStatus.MAPPING_COLUMNS, 40, "AI is mapping your columns")
        column_mapping = asyncio.run(
            mapper.map_columns(col_names, raw_rows, job.original_filename)
        )
        normalised_rows = mapper.apply_mapping(raw_rows, column_mapping)

        asyncio.run(
            job_repo.update_status(
                job_id,
                JobStatus.MAPPING_COLUMNS,
                extra={"detected_schema": column_mapping},
            )
        )
        log.info("columns_mapped", mapping=column_mapping)

        # ── Phase 4: Clean + Insert ──────────────────────────────
        step(JobStatus.INSERTING, 60, "Cleaning and storing client data")
        clients, failed_rows = cleaner.clean_rows(
            normalised_rows, officer_id, job_id
        )
        log.info("data_cleaned", valid=len(clients), failed=len(failed_rows))

        inserted = asyncio.run(client_repo.bulk_insert(clients))
        asyncio.run(
            job_repo.update_status(
                job_id,
                JobStatus.INSERTING,
                extra={
                    "parsed_rows": inserted,
                    "failed_rows": len(failed_rows),
                },
            )
        )

        # ── Phase 5: AI Kill-List ────────────────────────────────
        step(JobStatus.BUILDING_KILL_LIST, 80, "AI is building your kill-list")
        prioritised = asyncio.run(
            client_repo.get_prioritised_for_officer(officer_id)
        )

        overdue_count = len(prioritised.get("OVERDUE", []))
        due_tomorrow_count = len(prioritised.get("DUE_TOMORROW", []))
        log.info(
            "kill_list_inputs",
            overdue=overdue_count,
            due_tomorrow=due_tomorrow_count,
        )

        events = asyncio.run(
            ai_strategy.generate_kill_list(prioritised, officer_id, job_id)
        )
        asyncio.run(killlist_repo.bulk_insert_events(events))
        log.info("kill_list_built", events=len(events))

        # ── Phase 6: Done ────────────────────────────────────────
        asyncio.run(
            job_repo.update_status(job_id, JobStatus.DONE, progress_pct=100)
        )

        done_payload = {
            "job_id": job_id,
            "status": "DONE",
            "progress_pct": 100,
            "step": "Kill-list ready",
            "stats": {
                "total_clients": len(clients),
                "overdue": overdue_count,
                "due_tomorrow": due_tomorrow_count,
                "messages_scheduled": len(events),
            },
        }

        # Notify via both channels
        _broadcast(officer_id, job_id, done_payload)
        _update_firebase(firebase, officer_id, job_id, done_payload)
        firebase.update_kill_list_status(officer_id, "LIST_READY", job_id)

        log.info("pipeline_complete", events=len(events))
        return done_payload

    except Exception as exc:
        log.error("pipeline_failed", error=str(exc))
        try:
            asyncio.run(
                job_repo.update_status(
                    job_id,
                    JobStatus.FAILED,
                    error_message=str(exc),
                    current_step="Failed",
                )
            )
            fail_payload = {
                "job_id": job_id,
                "status": "FAILED",
                "error": str(exc),
            }
            _broadcast(officer_id, job_id, fail_payload)
            _update_firebase(firebase, officer_id, job_id, fail_payload)
        except Exception:
            pass

        raise self.retry(exc=exc)