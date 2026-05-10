from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any
import uuid

class PriorityTier(str, Enum):
    OVERDUE = "OVERDUE"          # Past due date
    DUE_TOMORROW = "DUE_TOMORROW"
    DUE_THIS_WEEK = "DUE_THIS_WEEK"
    UP_TO_DATE = "UP_TO_DATE"
    UNKNOWN = "UNKNOWN"

class ClientStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SETTLED = "SETTLED"
    DEFAULTED = "DEFAULTED"
    UNKNOWN = "UNKNOWN"

@dataclass
class Client:
    """
    Core domain entity. Schema-agnostic — the AI column mapper
    normalises whatever the officer uploads into these fields.
    raw_data holds the original row so nothing is ever lost.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    officer_id: str = ""
    job_id: str = ""

    # Normalised fields
    client_name: str = ""
    phone_number: str = ""          # Always stored as +2547XXXXXXXX
    national_id: str = ""

    # Financial
    product_type: str = ""          # e.g. "car_hp", "loan", "furniture_hp"
    total_principal: float = 0.0
    amount_due: float = 0.0         # Outstanding balance
    installment_amount: float = 0.0

    # Dates
    due_date: date | None = None
    installment_date: date | None = None
    last_payment_date: date | None = None

    # Derived / enriched
    days_overdue: int = 0
    priority_tier: PriorityTier = PriorityTier.UNKNOWN
    status: ClientStatus = ClientStatus.UNKNOWN

    # Original row preserved for audit
    raw_data: dict[str, Any] = field(default_factory=dict)

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def compute_priority(self, today: date | None = None) -> PriorityTier:
        """Business rule: derive priority from due_date vs today."""
        from datetime import date as date_cls, timedelta
        ref = today or date_cls.today()

        if not self.due_date:
            return PriorityTier.UNKNOWN

        delta = (ref - self.due_date).days

        if delta > 0:
            self.days_overdue = delta
            return PriorityTier.OVERDUE
        elif delta == 0 or delta == -1:
            return PriorityTier.DUE_TOMORROW
        elif -7 <= delta < 0:
            return PriorityTier.DUE_THIS_WEEK
        else:
            return PriorityTier.UP_TO_DATE