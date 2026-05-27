"""
core/domain/entities/ptp.py
────────────────────────────────────────
Domain definition for manual customer commitments (Promises to Pay).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import uuid

class PTPStatus(str, Enum):
    PENDING = "PENDING"
    KEPT = "KEPT"
    BROKEN = "BROKEN"

@dataclass
class PromiseToPay:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str = ""
    officer_id: str = ""
    promised_date: date | None = None
    promised_amount: float = 0.0
    status: PTPStatus = PTPStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))