"""
Clients Router  /api/v1/clients
────────────────────────────────
GET /clients/    → Full client list for the authenticated officer
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

import os, sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from .dependencies import get_officer_uid
from infrastructure.database.supabase.supabase_client import get_supabase

router = APIRouter(prefix="/clients", tags=["Clients"])


# ── Response schema ───────────────────────────────────────────────────────────

class ClientOut(BaseModel):
    id: str
    job_id: str
    client_name: str
    phone_number: str
    national_id: Optional[str]
    product_type: str
    total_principal: float
    amount_due: float
    installment_amount: float
    due_date: Optional[str]
    status: str
    priority_tier: str

class ClientListResponse(BaseModel):
    officer_id: str
    total: int
    clients: list[ClientOut]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=ClientListResponse,
    summary="Get all clients for the authenticated officer.",
)
async def get_clients(
    officer_uid: str = Depends(get_officer_uid),
) -> ClientListResponse:
    db = get_supabase()
    result = (
        db.table("clients")
        .select(
            "id, job_id, client_name, phone_number, national_id, "
            "product_type, total_principal, amount_due, installment_amount, "
            "due_date, status, priority_tier"
        )
        .eq("officer_id", officer_uid)
        .order("client_name")
        .execute()
    )

    rows = result.data or []
    clients = [
        ClientOut(
            id               = r["id"],
            job_id           = r.get("job_id", ""),
            client_name      = r.get("client_name", ""),
            phone_number     = r.get("phone_number", ""),
            national_id      = r.get("national_id"),
            product_type     = r.get("product_type", ""),
            total_principal  = float(r.get("total_principal") or 0),
            amount_due       = float(r.get("amount_due") or 0),
            installment_amount = float(r.get("installment_amount") or 0),
            due_date         = r.get("due_date"),
            status           = r.get("status", "UNKNOWN"),
            priority_tier    = r.get("priority_tier", "UNKNOWN"),
        )
        for r in rows
    ]

    return ClientListResponse(
        officer_id=officer_uid,
        total=len(clients),
        clients=clients,
    )