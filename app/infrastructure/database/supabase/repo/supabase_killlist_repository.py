"""
infrastructure/repositories/supabase_killlist_repository.py
─────────────────────────────────────────────────────────────
DB reads/writes for KillListEvent entities.

Table: kill_list_events
  id             uuid PK
  officer_id     text NOT NULL
  job_id         text NOT NULL
  client_id      text NOT NULL
  scheduled_at   timestamptz
  expires_at     timestamptz          ← 23:59:59 EAT of the scheduled day
  status         text DEFAULT 'SCHEDULED'
  message_body   text
  ai_reasoning   text
  priority_tier  text
  created_at     timestamptz DEFAULT now()
  updated_at     timestamptz DEFAULT now()
  sent_at        timestamptz
  actioned_at    timestamptz
  error_detail   text DEFAULT ''

Kill-list lifecycle:
  • Built by Celery → all events status=SCHEDULED, expires_at=23:59:59 EAT today
  • Officer actions them in the app → status=ACTIONED
  • At 00:00 EAT, a Celery beat task marks remaining SCHEDULED events as EXPIRED
    and triggers a fresh kill-list build.

The get_by_officer query filters to today's active (non-expired) events.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from supabase import Client

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from utils.logger import logger

from core.domain.entities.kill_list_event import EventStatus, KillListEvent
from core.domain.repositories.interfaces import IKillListRepository

TABLE = "kill_list_events"
BATCH_SIZE = 200
EAT_OFFSET = timedelta(hours=3)   # East Africa Time = UTC+3

log = logger


class SupabaseKillListRepository(IKillListRepository):
    def __init__(self, db: Client) -> None:
        self._db = db

    # ── Write ─────────────────────────────────────────────────────

    async def bulk_insert_events(self, events: list[KillListEvent]) -> int:
        if not events:
            return 0

        rows = [_to_row(e) for e in events]
        total = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            result = self._db.table(TABLE).insert(batch).execute()
            total += len(result.data)

        log.info("kill_list_events_inserted", count=total)
        return total

    # ── Read ──────────────────────────────────────────────────────
    async def get_by_officer(self, officer_id: str) -> list[KillListEvent]:
        result = (
            self._db.table(TABLE)
            .select("*")
            .eq("officer_id", officer_id)
            .not_.in_("status", [EventStatus.EXPIRED.value, EventStatus.CANCELLED.value])
            .order("priority_tier")
            .execute()
        )
        events = [_from_row(r) for r in (result.data or [])]

        # Hydrate client fields: name, amount_due, overdue_amount, due_date → total_arrears
        client_ids = [e.client_id for e in events]
        if client_ids:
            clients_result = (
                self._db.table("clients")
                .select("id, client_name, amount_due, overdue_amount, due_date")
                .in_("id", client_ids)
                .execute()
            )
            client_map = {c["id"]: c for c in (clients_result.data or [])}
            for e in events:
                c = client_map.get(e.client_id, {})
                e.client_name  = c.get("client_name", "")
                e.amount_due   = float(c.get("amount_due") or 0)
                e.total_arrears = _compute_total_arrears(
                    overdue_amount=float(c.get("overdue_amount") or 0),
                    amount_due=float(c.get("amount_due") or 0),
                    due_date_str=c.get("due_date"),
                )

        return events

    async def get_by_job(self, job_id: str) -> list[KillListEvent]:
        result = (
            self._db.table(TABLE)
            .select("*")
            .eq("job_id", job_id)
            .order("priority_tier")
            .execute()
        )
        return [_from_row(r) for r in (result.data or [])]

    # ── Expiry (called by Celery beat at 00:00 EAT) ───────────────
    async def expire_todays_events(self, officer_id: str) -> int:
        """
        Marks all SCHEDULED events from today as EXPIRED.
        Called at the nightly rollover.
        Returns number of events expired.
        """
        now_eat = datetime.now(tz=timezone.utc) + EAT_OFFSET
        today_start_eat = now_eat.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = (today_start_eat - EAT_OFFSET).isoformat()

        result = (
            self._db.table(TABLE)
            .update({
                "status": EventStatus.EXPIRED.value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("officer_id", officer_id)
            .eq("status", EventStatus.SCHEDULED.value)
            .gte("scheduled_at", window_start)
            .execute()
        )
        count = len(result.data or [])
        log.info("events_expired", officer_id=officer_id, count=count)
        return count
    
    async def expire_active_day_events(self, target_date_str: str) -> int:
        """
        Gracefully sweeps and expires any un-actioned 'SCHEDULED' items 
        from the previous cycle date to cleanly refresh the day's dashboard view.
        """
        result = (
            self._db.table(TABLE)
            .update({"status": "EXPIRED", "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("status", "SCHEDULED")
            .le("expires_at", target_date_str)
            .execute()
        )
        return len(result.data or [])


# ── Arrears helper ───────────────────────────────────────────────────────────

def _compute_total_arrears(
    overdue_amount: float,
    amount_due: float,
    due_date_str: "str | None",
) -> float:
    """
    Mirrors clients.py:
      total = overdue_amount
      + amount_due only if due_date <= today (installment is currently due or past)
    """
    from datetime import date as date_cls
    total = overdue_amount
    if due_date_str and amount_due > 0:
        try:
            due = date_cls.fromisoformat(due_date_str[:10])
            if due <= date_cls.today():
                total += amount_due
        except (ValueError, TypeError):
            pass
    return total


# ── Serialisation helpers ─────────────────────────────────────────────────────


def _compute_expires_at(scheduled_at: datetime | None) -> str | None:
    """
    Given a scheduled_at (UTC), compute 23:59:59 EAT of that same day (in UTC).
    This is what gets stored as expires_at.
    """
    if not scheduled_at:
        return None
    # Convert to EAT
    eat = scheduled_at + EAT_OFFSET
    eod_eat = eat.replace(hour=23, minute=59, second=59, microsecond=0)
    # Back to UTC for storage
    eod_utc = eod_eat - EAT_OFFSET
    return eod_utc.isoformat()


def _to_row(e: KillListEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "officer_id": e.officer_id,
        "job_id": e.job_id,
        "client_id": e.client_id,
        "scheduled_at": e.scheduled_at.isoformat() if e.scheduled_at else None,
        "expires_at": _compute_expires_at(e.scheduled_at),
        "status": e.status.value if isinstance(e.status, EventStatus) else e.status,
        "message_body": e.message_body,
        "ai_reasoning": e.ai_reasoning,
        "priority_tier": e.priority_tier,
        "created_at": e.created_at.isoformat(),
        "updated_at": e.updated_at.isoformat(),
        "sent_at": e.sent_at.isoformat() if e.sent_at else None,
        "actioned_at": e.actioned_at.isoformat() if e.actioned_at else None,
        "error_detail": e.error_detail,
    }


def _from_row(row: dict[str, Any]) -> KillListEvent:
    e = KillListEvent()
    e.id = row["id"]
    e.officer_id = row.get("officer_id", "")
    e.job_id = row.get("job_id", "")
    e.client_id = row.get("client_id", "")
    e.scheduled_at = _parse_dt(row.get("scheduled_at")) if row.get("scheduled_at") else None
    e.expires_at = _parse_dt(row.get("expires_at")) if row.get("expires_at") else None
    e.status = EventStatus(row.get("status", "SCHEDULED"))
    e.message_body = row.get("message_body", "")
    e.ai_reasoning = row.get("ai_reasoning", "")
    e.priority_tier = row.get("priority_tier", "")
    e.created_at = _parse_dt(row.get("created_at"))
    e.updated_at = _parse_dt(row.get("updated_at"))
    e.sent_at = _parse_dt(row.get("sent_at")) if row.get("sent_at") else None
    e.actioned_at = _parse_dt(row.get("actioned_at")) if row.get("actioned_at") else None
    e.error_detail = row.get("error_detail", "")
    return e


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)