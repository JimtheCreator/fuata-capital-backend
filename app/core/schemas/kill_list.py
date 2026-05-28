"""
presentation/schemas/kill_list.py
───────────────────────────────────
Pydantic response models for /api/v1/kill-list endpoints.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class KillListEventOut(BaseModel):
    """Single outreach event — one client, one scheduled action."""

    id: str
    client_id: str
    client_name: str = ""
    amount_due: float = 0.0
    total_arrears: float = 0.0     # overdue_amount + amount_due (when due_date <= today)
    scheduled_at: Optional[datetime] = None
    priority_tier: str
    message_body: str
    ai_reasoning: str
    status: str

    model_config = {"from_attributes": True}


class KillListResponse(BaseModel):
    officer_id: str
    total: int = Field(..., description="Total events in this kill-list")
    overdue: int = Field(0, description="Count of OVERDUE clients")
    due_tomorrow: int = Field(0, description="Count of DUE_TOMORROW clients")
    due_this_week: int = Field(0, description="Count of DUE_THIS_WEEK clients")
    events: list[KillListEventOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}