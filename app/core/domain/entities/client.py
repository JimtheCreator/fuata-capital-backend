from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
import uuid

class PriorityTier(str, Enum):
    OVERDUE = "OVERDUE"
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
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    officer_id: str = ""
    job_id: str = ""

    # ── Identity ──────────────────────────────────────────────────
    client_name: str = ""
    phone_number: str = ""
    national_id: str = ""

    # ── Asset / Product ───────────────────────────────────────────
    product_type: str = ""
    asset_identifier: str = ""      # Plate no, loan acc no, serial no
    asset_description: str = ""     # Human label: "TOYOTA HILUX", "iPhone 14", "Sofa Set"
    tracking_identifier: str = ""   # Chassis no, GPS ID, IMEI

    # ── Financial ─────────────────────────────────────────────────
    total_principal: float = 0.0
    total_payable: float = 0.0      # Principal + total interest
    amount_due: float = 0.0         # Current installment / amount due now
    installment_amount: float = 0.0 # Expected periodic payment
    overdue_amount: float = 0.0     # Accumulated past-due balance (was current_arrears)
    penalty_amount: float = 0.0     # Late fees / penalties charged separately

    # ── Timeline ──────────────────────────────────────────────────
    contract_start_date: date | None = None
    contract_end_date: date | None = None
    due_date: date | None = None        # Next/current payment due date
    # NOTE: installment_date is the same as due_date — not stored separately
    last_payment_date: date | None = None

    # ── Derived ───────────────────────────────────────────────────
    days_overdue: int = 0
    priority_tier: PriorityTier = PriorityTier.UNKNOWN
    status: ClientStatus = ClientStatus.UNKNOWN

    # ── Audit ─────────────────────────────────────────────────────
    raw_data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def compute_priority(self, today: date | None = None) -> PriorityTier:
        """Business rule: derive priority from due_date vs today."""
        from datetime import date as date_cls
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