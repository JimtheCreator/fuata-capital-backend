"""
Kill List Router  /api/v1/kill-list
─────────────────────────────────────
GET /kill-list/           → Full kill-list for the officer
GET /kill-list/job/{job_id} → Kill-list for a specific upload
"""

from fastapi import APIRouter, Depends, HTTPException

import os, sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from .dependencies import get_killlist_repo, get_officer_uid
from infrastructure.database.supabase.repo.supabase_killlist_repository import SupabaseKillListRepository
from core.schemas.kill_list import KillListEventOut, KillListResponse
from core.domain.entities.client import PriorityTier
from datetime import datetime, timezone
from app.infrastructure.database.supabase.supabase_client import get_supabase

from pydantic import BaseModel, Field
from app.core.domain.entities.kill_list_event import EventStatus

router = APIRouter(prefix="/kill-list", tags=["Kill List"])

class UpdateEventStatusRequest(BaseModel):
    status: EventStatus = Field(..., description="Target state: ACTIONED or CANCELLED")
    notes: str = Field(default="", description="Optional feedback or call disposition notes.")

@router.put(
    "/events/{event_id}/status",
    summary="Mark a kill-list item as actioned (called/messaged) or skipped.",
)
async def update_event_status(
    event_id: str,
    payload: UpdateEventStatusRequest,
    officer_uid: str = Depends(get_officer_uid),
    killlist_repo: SupabaseKillListRepository = Depends(get_killlist_repo),
):
    db = get_supabase()
    event_check = db.table("kill_list_events").select("*").eq("id", event_id).execute()

    if not event_check.data:
        raise HTTPException(status_code=404, detail="Kill list event not found.")

    event_data = event_check.data[0]
    if event_data["officer_id"] != officer_uid:
        raise HTTPException(status_code=403, detail="Unauthorized access to this event context.")

    update_data = {
        "status": payload.status.value,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    if payload.status == EventStatus.ACTIONED:
        update_data["actioned_at"] = datetime.now(timezone.utc).isoformat()
    if payload.notes:
        update_data["error_detail"] = payload.notes

    db.table("kill_list_events").update(update_data).eq("id", event_id).execute()
    return {"status": "success", "event_id": event_id, "new_state": payload.status.value}


@router.get("/", response_model=KillListResponse, summary="Get the officer's current kill-list.")
async def get_kill_list(
    officer_uid: str = Depends(get_officer_uid),
    killlist_repo: SupabaseKillListRepository = Depends(get_killlist_repo),
) -> KillListResponse:
    events = await killlist_repo.get_by_officer(officer_uid)
    return _build_response(officer_uid, events)


@router.get("/job/{job_id}", response_model=KillListResponse, summary="Get kill-list for a specific upload job.")
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
    overdue       = sum(1 for e in events if e.priority_tier == PriorityTier.OVERDUE.value)
    due_tomorrow  = sum(1 for e in events if e.priority_tier == PriorityTier.DUE_TOMORROW.value)
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
                client_name=e.client_name,
                amount_due=e.amount_due,
                total_arrears=e.total_arrears,
                scheduled_at=e.scheduled_at,
                priority_tier=e.priority_tier,
                message_body=e.message_body,
                ai_reasoning=e.ai_reasoning,
                status=e.status.value,
            )
            for e in events
        ],
    )