"""
core/domain/entities/kill_list_event.py
────────────────────────────────────────
KillListEvent — one scheduled outreach action per client per day.

The kill list expires at 23:59 EAT. A fresh one is generated whenever
a new upload is processed (or on the nightly Celery beat at 00:00 EAT
for officers who haven't uploaded that day).

The AI writes message_body and ai_reasoning. We store both so the
officer can review the AI's logic in the app.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid


class EventStatus(str, Enum):
    SCHEDULED  = "SCHEDULED"   # Waiting to be actioned by the officer
    ACTIONED   = "ACTIONED"    # Officer marked as done (called / messaged)
    SENT       = "SENT"        # Automated message dispatched (future WhatsApp)
    FAILED     = "FAILED"      # Send attempt failed
    CANCELLED  = "CANCELLED"   # Skipped / client settled before action
    EXPIRED    = "EXPIRED"     # 11:59 PM passed, never actioned


@dataclass
class KillListEvent:
    """
    One actionable outreach item per client per kill-list cycle.

    Relationships:
        officer_id  → the collection officer who owns this
        job_id      → the upload that triggered this kill-list build
        client_id   → the debtor client record
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    officer_id: str = ""
    job_id: str = ""
    client_id: str = ""
    
    client_name: str = ""
    amount_due: float = 0.0

    # ── Scheduling ────────────────────────────────────────────────
    # Stored in UTC.  Display layer adds +3 for EAT.
    scheduled_at: datetime | None = None

    # Kill-list cycle: this event belongs to the day defined by
    # DATE(scheduled_at AT TIME ZONE 'Africa/Nairobi').
    # Expires automatically at 23:59:59 EAT of that day.
    expires_at: datetime | None = None   # Set to 23:59:59 EAT of scheduled day

    status: EventStatus = EventStatus.SCHEDULED

    # ── AI-generated content ──────────────────────────────────────
    message_body: str = ""
    ai_reasoning: str = ""   # One-sentence explanation of tone/approach
    priority_tier: str = ""  # OVERDUE | DUE_TOMORROW | DUE_THIS_WEEK

    # ── Audit ─────────────────────────────────────────────────────
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
    actioned_at: datetime | None = None
    error_detail: str = ""
