"""
api/v1/ptp.py
───────────────
POST /api/v1/clients/{client_id}/ptp → Create a new promise
"""
from fastapi import APIRouter, Depends, HTTPException
import os, sys

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from .dependencies import get_officer_uid
from app.infrastructure.database.supabase.supabase_client import get_supabase
from app.infrastructure.database.supabase.repo.supabase_ptp_repository import SupabasePTPRepository
from app.core.schemas.ptp import CreatePTPRequest, PTPResponse
from core.domain.entities.ptp import PromiseToPay, PTPStatus
from datetime import datetime, timezone

router = APIRouter(prefix="/clients", tags=["Promises to Pay"])

@router.post(
    "/{client_id}/ptp",
    response_model=PTPResponse,
    summary="Log a new Promise to Pay for a client.",
)
async def create_promise_to_pay(
    client_id: str,
    payload: CreatePTPRequest,
    officer_uid: str = Depends(get_officer_uid),
) -> PTPResponse:
    
    db = get_supabase()
    
    # Optional: Verify the client actually belongs to this officer first
    client_check = db.table("clients").select("id").eq("id", client_id).eq("officer_id", officer_uid).execute()
    if not client_check.data:
        raise HTTPException(status_code=404, detail="Client not found or unauthorized.")

    ptp_repo = SupabasePTPRepository(db)
    
    new_ptp = PromiseToPay(
        client_id=client_id,
        officer_id=officer_uid,
        promised_date=payload.promised_date,
        promised_amount=payload.promised_amount,
        status=PTPStatus.PENDING
    )
    
    await ptp_repo.create_ptp(new_ptp)
    
    return PTPResponse(
        id=new_ptp.id,
        client_id=new_ptp.client_id,
        promised_date=new_ptp.promised_date,
        promised_amount=new_ptp.promised_amount,
        status=new_ptp.status.value
    )


@router.post(
    "/ptp/{ptp_id}/resolve",
    summary="Manually resolve a promise once payment confirmation is cleared.",
)
async def resolve_promise_to_pay(
    ptp_id: str,
    officer_uid: str = Depends(get_officer_uid),
):
    db = get_supabase()
    
    # 1. Check promise existence
    ptp_check = db.table("promises_to_pay").select("*").eq("id", ptp_id).execute()
    if not ptp_check.data:
        raise HTTPException(status_code=404, detail="Promise tracking profile not found.")
    
    ptp_record = ptp_check.data[0]
    if ptp_record["officer_id"] != officer_uid:
        raise HTTPException(status_code=403, detail="Unauthorized access constraints.")

    # 2. Settle the promise transaction status
    db.table("promises_to_pay").update({
        "status": "KEPT",
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", ptp_id).execute()

    # 3. Immediately cancel any active kill list item for today to give the officer a clean view
    db.table("kill_list_events").update({
        "status": "CANCELLED",
        "error_detail": "Client fulfilled payment promise directly."
    }).eq("client_id", ptp_record["client_id"]).eq("status", "SCHEDULED").execute()

    return {"status": "resolved", "ptp_id": ptp_id}