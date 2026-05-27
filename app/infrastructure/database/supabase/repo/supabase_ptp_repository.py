"""
infrastructure/repositories/supabase_ptp_repository.py
────────────────────────────────────────────────────────
DB reads/writes for PromiseToPay entities.
"""
from __future__ import annotations
from datetime import datetime, timezone
import structlog
from supabase import Client

from core.domain.entities.ptp import PromiseToPay, PTPStatus

log = structlog.get_logger(__name__)
TABLE = "promises_to_pay"

class SupabasePTPRepository:
    def __init__(self, db: Client) -> None:
        self._db = db

    async def create_ptp(self, ptp: PromiseToPay) -> PromiseToPay:
        """
        Creates a new PTP and gracefully marks any existing 
        PENDING promises for this client as CANCELLED.
        """
        # 1. Invalidate old pending promises
        self._db.table(TABLE).update({
            "status": "CANCELLED",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("client_id", ptp.client_id).eq("status", PTPStatus.PENDING.value).execute()

        # 2. Insert the new promise
        data = {
            "id": ptp.id,
            "client_id": ptp.client_id,
            "officer_id": ptp.officer_id,
            "promised_date": ptp.promised_date.isoformat() if ptp.promised_date else None,
            "promised_amount": ptp.promised_amount,
            "status": ptp.status.value,
            "created_at": ptp.created_at.isoformat(),
            "updated_at": ptp.updated_at.isoformat()
        }
        
        self._db.table(TABLE).insert(data).execute()
        log.info("ptp_created", client_id=ptp.client_id, amount=ptp.promised_amount)
        
        return ptp