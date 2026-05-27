"""
Clients Router  /api/v1/clients
────────────────────────────────
GET /clients/    → Full client list for the authenticated officer
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import date

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
    overdue_amount: float
    # overdue_amount + amount_due only when due_date <= today.
    # This is the single number the Android app should display as "arrears".
    total_arrears: float
    installment_amount: float
    due_date: Optional[str]
    status: str
    priority_tier: str

class ClientListResponse(BaseModel):
    officer_id: str
    total: int
    clients: list[ClientOut]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_total_arrears(overdue_amount: float, amount_due: float, due_date_str: Optional[str]) -> float:
    """
    Total amount the client owes right now:
      - Always include overdue_amount (accumulated arrears).
      - Only add amount_due if due_date <= today (installment is currently due or already passed).
    """
    total = overdue_amount

    if due_date_str and amount_due > 0:
        try:
            due = date.fromisoformat(due_date_str[:10])  # handles both date and datetime strings
            if due <= date.today():
                total += amount_due
        except (ValueError, TypeError):
            pass  # malformed date — skip the amount_due addition

    return total


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
            "product_type, total_principal, amount_due, overdue_amount, "
            "installment_amount, due_date, status, priority_tier"
        )
        .eq("officer_id", officer_uid)
        .order("client_name")
        .execute()
    )

    rows = result.data or []
    clients = []
    for r in rows:
        amount_due     = float(r.get("amount_due") or 0)
        overdue_amount = float(r.get("overdue_amount") or 0)
        due_date_str   = r.get("due_date")

        clients.append(ClientOut(
            id               = r["id"],
            job_id           = r.get("job_id", ""),
            client_name      = r.get("client_name", ""),
            phone_number     = r.get("phone_number", ""),
            national_id      = r.get("national_id"),
            product_type     = r.get("product_type", ""),
            total_principal  = float(r.get("total_principal") or 0),
            amount_due       = amount_due,
            overdue_amount   = overdue_amount,
            total_arrears    = _compute_total_arrears(overdue_amount, amount_due, due_date_str),
            installment_amount = float(r.get("installment_amount") or 0),
            due_date         = due_date_str,
            status           = r.get("status", "UNKNOWN"),
            priority_tier    = r.get("priority_tier", "UNKNOWN"),
        ))

    return ClientListResponse(
        officer_id=officer_uid,
        total=len(clients),
        clients=clients,
    )