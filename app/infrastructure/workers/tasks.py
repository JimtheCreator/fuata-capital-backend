"""
Celery Tasks
─────────────
process_upload_job  — The entire ETL + AI pipeline for one upload job.

Progress is broadcast in two ways:
1. Redis pub/sub channel  → SSE endpoint picks it up
2. Firebase Realtime DB   → Android app listener fires
───────────────────────────
Implements idempotent parsing and processing architectures for asset portfolio data.
"""


from __future__ import annotations
import asyncio
import json
from datetime import datetime
from celery import Celery

from .celery_app import celery_app
from app.core.domain.entities.upload_job import JobStatus
from app.infrastructure.database.supabase.supabase_client import get_supabase
from app.infrastructure.database.supabase.repo.supabase_job_repository import SupabaseJobRepository
from app.infrastructure.database.supabase.repo.supabase_client_repository import SupabaseClientRepository
from app.infrastructure.database.supabase.repo.supabase_killlist_repository import SupabaseKillListRepository
from app.infrastructure.database.supabase.repo.supabase_storage_repository import SupabaseStorageRepository
from app.infrastructure.services.firebase_service import FirebaseAuthService
from app.infrastructure.services.kill_list_evaluator import KillListEvaluatorService
from app.infrastructure.parsers.file_parser import FileParserService
from ..services.column_mapper_service import ColumnMapperService
from ..services.data_cleaner_service import DataCleanerService
from ..services.ai_strategy_service import AIStrategyService
from ..cache.redis_client import get_redis_client

from app.utils.logger import logger as log

def _broadcast(officer_id: str, job_id: str, payload: dict) -> None:
    try:
        redis = get_redis_client()
        channel = f"job_progress:{officer_id}:{job_id}"
        redis.publish(channel, json.dumps(payload))
    except Exception as exc:
        log.warning("redis_broadcast_failed", error=str(exc))

def _update_firebase(firebase: FirebaseAuthService, officer_id: str, job_id: str, payload: dict) -> None:
    try:
        firebase.notify_job_done(officer_id, job_id, payload)
    except Exception as exc:
        log.warning("firebase_update_failed", error=str(exc))

@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="fuata_capital_workers.process_upload_job",
)
def process_upload_job(self, job_id: str) -> dict:
    task_logger = log.bind(job_id=job_id)

    # ── Dependency Wiring ───────────────────────────────────────
    db = get_supabase()
    job_repo = SupabaseJobRepository(db)
    client_repo = SupabaseClientRepository(db)
    killlist_repo = SupabaseKillListRepository(db)
    storage_repo = SupabaseStorageRepository(db)
    firebase = FirebaseAuthService()
    parser = FileParserService()
    evaluator = KillListEvaluatorService()
    mapper = ColumnMapperService()
    cleaner = DataCleanerService()
    ai_service = AIStrategyService()

    # Shared tracking variable for standard exceptions
    officer_id = "SYSTEM"

    def step(status: JobStatus, pct: int, msg: str) -> None:
        asyncio.run(job_repo.update_status(job_id, status, progress_pct=pct, current_step=msg))
        _broadcast(officer_id, job_id, {
            "job_id": job_id,
            "status": status.value,
            "progress_pct": pct,
            "step": msg,
        })

    try:
        # ── Fetch Metadata State ────────────────────────────────
        job = asyncio.run(job_repo.get(job_id))
        if not job:
            raise ValueError(f"Job context reference {job_id} missing from DB core.")
        officer_id = job.officer_id
        task_logger = task_logger.bind(officer_id=officer_id)

        # ── Phase 1: Storage Retrieval ──────────────────────────
        step(JobStatus.DOWNLOADING, 10, "Extracting binary file from bucket storage")
        file_bytes = asyncio.run(storage_repo.download_bytes(job.file_path))

        # ── Phase 2: Schema Processing ──────────────────────────
        step(JobStatus.PARSING, 25, "Executing layout mapping and string parsing")
        file_type, raw_rows, col_names = parser.parse(file_bytes, job.original_filename)
        
        asyncio.run(job_repo.update_status(
            job_id, JobStatus.PARSING, 
            extra={"file_type": file_type.value, "total_rows": len(raw_rows)}
        ))

        if not raw_rows:
            raise ValueError("Parsed table structural data container holds zero index entities.")

        # ── Phase 3: AI Core Key Mapping ────────────────────────
        step(JobStatus.MAPPING_COLUMNS, 40, "AI matching structural headers to fields")
        column_mapping = asyncio.run(mapper.map_columns(col_names, raw_rows, job.original_filename))
        normalised_rows = mapper.apply_mapping(raw_rows, column_mapping)

        asyncio.run(job_repo.update_status(
            job_id, JobStatus.MAPPING_COLUMNS, 
            extra={"detected_schema": column_mapping}
        ))

        # ── Phase 4: Clean + High-Performance Upsert ────────────
        step(JobStatus.INSERTING, 60, "Executing portfolio data cleaning and upserting")
        clients, failed_rows = cleaner.clean_rows(normalised_rows, officer_id, job_id)

        # ➔ NEW: Pre-insertion Status & Priority Evaluation
        from app.core.domain.entities.client import PriorityTier, ClientStatus
        
        healthy_count = 0
        for client in clients:
            # Check the raw spreadsheet comments for manual "CLEARED" notes
            is_cleared_comment = str(client.raw_data.get("Comments", "")).upper() == "CLEARED"
            
            # This triggers the newly updated math in client.py
            client.priority_tier = client.compute_priority(today=datetime.now().date())
            
            if client.priority_tier == PriorityTier.UP_TO_DATE or is_cleared_comment:
                client.status = ClientStatus.SETTLED
                healthy_count += 1
            else:
                client.status = ClientStatus.ACTIVE

        log.info("portfolio_status_evaluation_complete", 
                 healthy_filtered_out=healthy_count, 
                 total_ingested=len(clients))

        # Perform history-preserving upserting using our deterministic IDs
        inserted_count = asyncio.run(client_repo.bulk_insert(clients))
        
        asyncio.run(job_repo.update_status(
            job_id, JobStatus.INSERTING,
            extra={"parsed_rows": inserted_count, "failed_rows": len(failed_rows)}
        ))

        # ── Phase 5: Filtered Onboarding AI Strategy Generation ─────
        step(JobStatus.BUILDING_KILL_LIST, 80, "AI analyzing segments to structure actionable queue")
        
        # Avoid pulling healthy accounts. Fetch current data points to apply the Triad rule engine.
        today_eat = datetime.now().date()
        active_promises = asyncio.run(client_repo.get_active_promises_to_pay(officer_id))
        
        # Execute identical domain strategy rules used by the nightly rotation engine
        prioritised = evaluator.extract_actionable_targets(clients, active_promises, today_eat)

        overdue_count = len(prioritised.get("OVERDUE", []))
        due_tomorrow_count = len(prioritised.get("DUE_TOMORROW", []))

        # Pass only the prioritized segments to the AI service
        events = []
        if sum(len(v) for v in prioritised.values()) > 0:
            events = asyncio.run(ai_service.generate_kill_list(prioritised, officer_id, job_id))
            asyncio.run(killlist_repo.bulk_insert_events(events))

        # ── Phase 6: Pipeline Execution Completion ──────────────
        asyncio.run(job_repo.update_status(job_id, JobStatus.DONE, progress_pct=100))

        done_payload = {
            "job_id": job_id,
            "status": "DONE",
            "progress_pct": 100,
            "step": "Daily actionable strategy queue finalized",
            "stats": {
                "total_clients": len(clients),
                "overdue": overdue_count,
                "due_tomorrow": due_tomorrow_count,
                "messages_scheduled": len(events),
            },
        }

        _broadcast(officer_id, job_id, done_payload)
        _update_firebase(firebase, officer_id, job_id, done_payload)
        firebase.update_kill_list_status(officer_id, "LIST_READY", job_id)

        return done_payload

    except Exception as exc:
        task_logger.error("pipeline_aborted_by_critical_error", error=str(exc))
        try:
            asyncio.run(job_repo.update_status(
                job_id, JobStatus.FAILED, 
                error_message=str(exc), current_step="Aborted"
            ))
            fail_payload = {"job_id": job_id, "status": "FAILED", "error": str(exc)}
            _broadcast(officer_id, job_id, fail_payload)
            _update_firebase(firebase, officer_id, job_id, fail_payload)
        except Exception:
            pass
        
        raise self.retry(exc=exc)
    

@celery_app.task(name="tasks.run_nightly_kill_list_rotation")
def run_nightly_kill_list_rotation() -> str:
    """
    Executes nightly at 00:00 EAT via Celery Beat.
    Closes the previous day's metrics loop and populates the fresh daily outreach campaign.
    """
    import asyncio
    from datetime import date, datetime, timedelta, timezone
    
    db = get_supabase()
    kill_repo = SupabaseKillListRepository(db)
    client_repo = SupabaseClientRepository(db)
    ai_service = AIStrategyService()
    evaluator = KillListEvaluatorService()

    # Define strict temporal cycle bounds in local EAT representation
    today_eat = date.today() 
    yesterday_end_str = datetime.combine(today_eat - timedelta(days=1), datetime.max.time()).isoformat()

    log.info("nightly_rotation_started", cycle_date=today_eat.isoformat())

    # Step 1: Gracefully sweep and close out yesterday's remaining scheduled list items
    expired_count = asyncio.run(kill_repo.expire_active_day_events(yesterday_end_str))
    log.info("yesterdays_events_expired", count=expired_count)

    # Step 2: Extract active officer profiles to update independent workspaces
    officers = asyncio.run(client_repo.get_all_active_officers())
    
    total_new_events = 0

    for officer_id in officers:
        # Step 3: Fetch portfolio data points
        portfolio = asyncio.run(client_repo.get_all_clients_for_officer(officer_id))
        promises = asyncio.run(client_repo.get_active_promises_to_pay(officer_id))

        # Check for and update broken promises
        broken_ptp_ids = [
            p["id"] for p in promises 
            if datetime.strptime(p["promised_date"], "%Y-%m-%d").date() < today_eat
        ]
        if broken_ptp_ids:
            asyncio.run(client_repo.batch_update_broken_promises(broken_ptp_ids))
            # Refresh promises array post-mutation
            promises = asyncio.run(client_repo.get_active_promises_to_pay(officer_id))

        # Step 4: Extract the core action items using the evaluator service
        prioritised_targets = evaluator.extract_actionable_targets(portfolio, promises, today_eat)
        
        # Guard against zero-target allocations
        target_count = sum(len(v) for v in prioritised_targets.values())
        if target_count == 0:
            log.info("skipping_officer_rotation_zero_targets", officer_id=officer_id)
            continue

        # Step 5: Synthesize and schedule outreach strings via OpenRouter
        try:
            events = asyncio.run(ai_service.generate_kill_list(prioritised_targets, officer_id, job_id=f"CRON_{today_eat.isoformat()}"))
            if events:
                asyncio.run(kill_repo.bulk_insert_events(events))
                total_new_events += len(events)
        except Exception as ai_err:
            log.error("ai_generation_failed_during_rotation_retry", officer_id=officer_id, error=str(ai_err))
            continue

    log.info("nightly_rotation_completed", total_new_events_scheduled=total_new_events)
    return f"Successfully generated {total_new_events} outreach events for today."