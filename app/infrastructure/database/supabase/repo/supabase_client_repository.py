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
───────────────────────────────────────────────────────────
Production-grade DB reads/writes for Client entities using 
idempotent, state-preserving upserts.
"""

from __future__ import annotations
from datetime import datetime, timezone
import uuid
import structlog
from typing import Any
from supabase import Client

from core.domain.entities.client import Client as ClientEntity, ClientStatus, PriorityTier
from core.domain.repositories.interfaces import IClientRepository

TABLE = "clients"
BATCH_SIZE = 200

log = structlog.get_logger(__name__)

class SupabaseClientRepository(IClientRepository):
    def __init__(self, db: Client) -> None:
        self._db = db

    @staticmethod
    def generate_deterministic_id(officer_id: str, unique_key: str) -> str:
        """
        Generates a stable, predictable UUID v5 to prevent duplicates
        and enable safe record upserting across distinct uploads.
        """
        namespace = uuid.uuid5(uuid.NAMESPACE_DNS, "fuata.capital")
        combined_key = f"{officer_id}:{unique_key.strip().upper()}"
        return str(uuid.uuid5(namespace, combined_key))

    async def bulk_insert(self, clients: list[ClientEntity]) -> int:
        """
        Idempotently inserts or updates portfolio records.
        Preserves client history and relationship integrity.
        """
        if not clients:
            return 0

        # Enforce deterministic IDs before persistence layer processing
        for client in clients:
            # Fallback natural key precedence: National ID -> Asset Identifier -> Phone Number
            natural_key = client.national_id or client.asset_identifier or client.phone_number
            if not natural_key:
                raise ValueError(f"Incomplete identifier data for client: {client.client_name}")
            
            client.id = self.generate_deterministic_id(client.officer_id, natural_key)

        rows = [_to_row(c) for c in clients]
        total = 0

        log.info("initiating_portfolio_upsert", total_records=len(rows), officer_id=clients[0].officer_id)

        # Process batch updates cleanly
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            
            # Using Supabase/PostgREST upsert with explicit merge strategy
            result = self._db.table(TABLE).upsert(
                batch, 
                on_conflict="id"
            ).execute()
            
            total += len(result.data or [])
            log.debug("upsert_batch_complete", current_count=total)

        return total

    async def get_by_job(self, job_id: str) -> list[ClientEntity]:
        result = self._db.table(TABLE).select("*").eq("job_id", job_id).execute()
        return [_from_row(r) for r in (result.data or [])]

    async def get_prioritised_for_officer(self, officer_id: str) -> dict[str, list[ClientEntity]]:
        """Returns non-settled clients grouped by active priority metrics."""
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
    
    async def get_all_active_officers(self) -> list[str]:
        """Fetches distinct IDs of all officers actively managing portfolios."""
        result = self._db.table(TABLE).select("officer_id").execute()
        return list(set([row["officer_id"] for row in (result.data or [])]))

    async def get_all_clients_for_officer(self, officer_id: str) -> list[ClientEntity]:
        """Retrieves entire persisted baseline portfolio for evaluation rule execution."""
        result = self._db.table(TABLE).select("*").eq("officer_id", officer_id).execute()
        return [_from_row(r) for r in (result.data or [])]

    async def get_active_promises_to_pay(self, officer_id: str) -> list[dict[str, Any]]:
        """Retrieves raw pending customer financial commitments."""
        result = self._db.table("promises_to_pay").select("*").eq("officer_id", officer_id).eq("status", "PENDING").execute()
        return result.data or []

    async def batch_update_broken_promises(self, broken_ids: list[str]) -> None:
        """Transitions failed promises to broken status."""
        if not broken_ids:
            return
        self._db.table("promises_to_pay").update({"status": "BROKEN", "updated_at": datetime.now(timezone.utc).isoformat()}).in_("id", broken_ids).execute()

# ── Extraction Mappings ───────────────────────────────────────────────────

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
        "priority_tier": c.priority_tier.value if hasattr(c.priority_tier, 'value') else str(c.priority_tier),
        "status": c.status.value if hasattr(c.status, 'value') else str(c.status),
        "raw_data": c.raw_data,
    }

def _from_row(row: dict[str, Any]) -> ClientEntity:
    # Hydrates the dataclass object accurately from table schemas
    c = ClientEntity()
    c.id = row["id"]
    c.officer_id = row["officer_id"]
    c.job_id = row["job_id"]
    c.client_name = row.get("client_name", "")
    c.phone_number = row.get("phone_number", "")
    c.national_id = row.get("national_id", "")
    c.product_type = row.get("product_type", "")
    c.asset_identifier = row.get("asset_identifier", "")
    c.asset_description = row.get("asset_description", "")
    c.tracking_identifier = row.get("tracking_identifier", "")
    c.total_principal = float(row.get("total_principal") or 0.0)
    c.total_payable = float(row.get("total_payable") or 0.0)
    c.amount_due = float(row.get("amount_due") or 0.0)
    c.installment_amount = float(row.get("installment_amount") or 0.0)
    c.overdue_amount = float(row.get("overdue_amount") or 0.0)
    c.penalty_amount = float(row.get("penalty_amount") or 0.0)
    return c