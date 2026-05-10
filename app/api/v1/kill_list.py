"""
Kill List Router  /api/v1/kill-list
─────────────────────────────────────
GET /kill-list/           → Full kill-list for the officer
GET /kill-list/job/{job_id} → Kill-list for a specific upload
"""

from fastapi import APIRouter, Depends, HTTPException

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from .dependencies import get_killlist_repo, get_officer_uid
from infrastructure.database.supabase.repo.supabase_killlist_repository import SupabaseKillListRepository
from core.schemas.kill_list import KillListEventOut, KillListResponse
from core.domain.entities.client import PriorityTier

router = APIRouter(prefix="/kill-list", tags=["Kill List"])


@router.get(
    "/",
    response_model=KillListResponse,
    summary="Get the officer's current kill-list (all jobs).",
)
async def get_kill_list(
    officer_uid: str = Depends(get_officer_uid),
    killlist_repo: SupabaseKillListRepository = Depends(get_killlist_repo),
) -> KillListResponse:
    events = await killlist_repo.get_by_officer(officer_uid)
    return _build_response(officer_uid, events)


@router.get(
    "/job/{job_id}",
    response_model=KillListResponse,
    summary="Get kill-list for a specific upload job.",
)
async def get_kill_list_by_job(
    job_id: str,
    officer_uid: str = Depends(get_officer_uid),
    killlist_repo: SupabaseKillListRepository = Depends(get_killlist_repo),
) -> KillListResponse:
    events = await killlist_repo.get_by_job(job_id)
    if events and events[0].officer_id != officer_uid:
        raise HTTPException(status_code=403, detail="Access denied.")
    return _build_response(officer_uid, events)


def _build_response(officer_uid: str, events) -> KillListResponse:
    overdue = sum(1 for e in events if e.priority_tier == PriorityTier.OVERDUE.value)
    due_tomorrow = sum(1 for e in events if e.priority_tier == PriorityTier.DUE_TOMORROW.value)
    due_this_week = sum(1 for e in events if e.priority_tier == PriorityTier.DUE_THIS_WEEK.value)

    return KillListResponse(
        officer_id=officer_uid,
        total=len(events),
        overdue=overdue,
        due_tomorrow=due_tomorrow,
        due_this_week=due_this_week,
        events=[
            KillListEventOut(
                id=e.id,
                client_id=e.client_id,
                scheduled_at=e.scheduled_at,
                priority_tier=e.priority_tier,
                message_body=e.message_body,
                ai_reasoning=e.ai_reasoning,
                status=e.status.value,
            )
            for e in events
        ],
    )