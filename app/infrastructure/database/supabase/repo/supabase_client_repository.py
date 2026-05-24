"""
infrastructure/repositories/supabase_client_repository.py
───────────────────────────────────────────────────────────
DB reads/writes for Client (debtor) entities.

Table: clients
  id                  uuid PK
  officer_id          text NOT NULL
  job_id              text NOT NULL
  client_name         text
  phone_number        text
  national_id         text
  product_type        text
  asset_identifier    text
  asset_description   text          ← car model, phone model, etc.
  tracking_identifier text
  total_principal     numeric DEFAULT 0
  total_payable       numeric DEFAULT 0
  amount_due          numeric DEFAULT 0   ← current instalment due
  installment_amount  numeric DEFAULT 0
  overdue_amount      numeric DEFAULT 0   ← accumulated past-due balance
  penalty_amount      numeric DEFAULT 0   ← late fees charged
  contract_start_date date
  contract_end_date   date
  due_date            date                ← same as instalment date
  last_payment_date   date
  days_overdue        int DEFAULT 0
  priority_tier       text DEFAULT 'UNKNOWN'
  status              text DEFAULT 'UNKNOWN'
  raw_data            jsonb DEFAULT '{}'
  created_at          timestamptz DEFAULT now()
  updated_at          timestamptz DEFAULT now()
"""

from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Any

import structlog
from supabase import Client

from core.domain.entities.client import Client as ClientEntity, ClientStatus, PriorityTier
from core.domain.repositories.interfaces import IClientRepository

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

TABLE = "clients"
BATCH_SIZE = 500

log = structlog.get_logger(__name__)


class SupabaseClientRepository(IClientRepository):
    def __init__(self, db: Client) -> None:
        self._db = db

    # ── Write ─────────────────────────────────────────────────────

    async def bulk_insert(self, clients: list[ClientEntity]) -> int:
        """
        Full refresh per officer — deletes existing rows first.
        Each upload replaces the previous list entirely.
        """
        if not clients:
            return 0

        officer_id = clients[0].officer_id
        self._db.table(TABLE).delete().eq("officer_id", officer_id).execute()
        log.debug("existing_rows_cleared", officer_id=officer_id)

        rows = [_to_row(c) for c in clients]
        total = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            result = self._db.table(TABLE).insert(batch).execute()
            total += len(result.data)
            log.debug("batch_inserted", batch_num=i // BATCH_SIZE + 1, count=len(result.data))

        return total

    # ── Read ──────────────────────────────────────────────────────

    async def get_by_job(self, job_id: str) -> list[ClientEntity]:
        result = (
            self._db.table(TABLE)
            .select("*")
            .eq("job_id", job_id)
            .execute()
        )
        return [_from_row(r) for r in (result.data or [])]

    async def get_prioritised_for_officer(
        self, officer_id: str
    ) -> dict[str, list[ClientEntity]]:
        """
        Returns clients grouped by priority tier for kill-list building.
        Excludes settled clients.
        """
        tiers = [
            PriorityTier.OVERDUE.value,
            PriorityTier.DUE_TOMORROW.value,
            PriorityTier.DUE_THIS_WEEK.value,
        ]

        result = (
            self._db.table(TABLE)
            .select("*")
            .eq("officer_id", officer_id)
            .in_("priority_tier", tiers)
            .neq("status", ClientStatus.SETTLED.value)
            .order("days_overdue", desc=True)
            .execute()
        )

        grouped: dict[str, list[ClientEntity]] = {t: [] for t in tiers}
        for row in (result.data or []):
            tier = row.get("priority_tier", "UNKNOWN")
            if tier in grouped:
                grouped[tier].append(_from_row(row))

        return grouped


# ── Serialisation helpers ─────────────────────────────────────────

def _to_row(c: ClientEntity) -> dict[str, Any]:
    return {
        "id": c.id,
        "officer_id": c.officer_id,
        "job_id": c.job_id,
        "client_name": c.client_name,
        "phone_number": c.phone_number,
        "national_id": c.national_id,
        "product_type": c.product_type,
        "asset_identifier": c.asset_identifier,
        "asset_description": c.asset_description,
        "tracking_identifier": c.tracking_identifier,
        "total_principal": c.total_principal,
        "total_payable": c.total_payable,
        "amount_due": c.amount_due,
        "installment_amount": c.installment_amount,
        "overdue_amount": c.overdue_amount,
        "penalty_amount": c.penalty_amount,
        "contract_start_date": c.contract_start_date.isoformat() if c.contract_start_date else None,
        "contract_end_date": c.contract_end_date.isoformat() if c.contract_end_date else None,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "last_payment_date": c.last_payment_date.isoformat() if c.last_payment_date else None,
        "days_overdue": c.days_overdue,
        "priority_tier": c.priority_tier.value if isinstance(c.priority_tier, PriorityTier) else c.priority_tier,
        "status": c.status.value if isinstance(c.status, ClientStatus) else c.status,
        "raw_data": c.raw_data,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _from_row(row: dict[str, Any]) -> ClientEntity:
    c = ClientEntity()
    c.id = row["id"]
    c.officer_id = row.get("officer_id", "")
    c.job_id = row.get("job_id", "")
    c.client_name = row.get("client_name", "")
    c.phone_number = row.get("phone_number", "")
    c.national_id = row.get("national_id", "")
    c.product_type = row.get("product_type", "")
    c.asset_identifier = row.get("asset_identifier", "")
    c.asset_description = row.get("asset_description", "")
    c.tracking_identifier = row.get("tracking_identifier", "")
    c.total_principal = float(row.get("total_principal") or 0)
    c.total_payable = float(row.get("total_payable") or 0)
    c.amount_due = float(row.get("amount_due") or 0)
    c.installment_amount = float(row.get("installment_amount") or 0)
    c.overdue_amount = float(row.get("overdue_amount") or 0)
    c.penalty_amount = float(row.get("penalty_amount") or 0)
    c.contract_start_date = _parse_date(row.get("contract_start_date"))
    c.contract_end_date = _parse_date(row.get("contract_end_date"))
    c.due_date = _parse_date(row.get("due_date"))
    c.last_payment_date = _parse_date(row.get("last_payment_date"))
    c.days_overdue = row.get("days_overdue", 0)
    c.priority_tier = PriorityTier(row.get("priority_tier", "UNKNOWN"))
    c.status = ClientStatus(row.get("status", "UNKNOWN"))
    c.raw_data = row.get("raw_data") or {}
    c.created_at = _parse_dt(row.get("created_at"))
    c.updated_at = _parse_dt(row.get("updated_at"))
    return c


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)