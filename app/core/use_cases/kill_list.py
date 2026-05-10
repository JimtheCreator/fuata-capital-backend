from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid


class EventStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class KillListEvent:
    """
    One scheduled outreach action per client per day.
    The AI writes the message_body and the reasoning.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    officer_id: str = ""
    job_id: str = ""
    client_id: str = ""

    # Scheduled execution
    scheduled_at: datetime | None = None
    status: EventStatus = EventStatus.SCHEDULED

    # AI-generated content
    message_body: str = ""
    ai_reasoning: str = ""          # Why this client, why this tone
    priority_tier: str = ""

    created_at: datetime = field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
    error_detail: str = ""