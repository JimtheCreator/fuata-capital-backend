from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import uuid


class JobStatus(str, Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    PARSING = "PARSING"
    MAPPING_COLUMNS = "MAPPING_COLUMNS"
    INSERTING = "INSERTING"
    BUILDING_KILL_LIST = "BUILDING_KILL_LIST"
    DONE = "DONE"
    FAILED = "FAILED"


class FileType(str, Enum):
    CSV = "csv"
    EXCEL = "excel"
    PDF = "pdf"
    UNKNOWN = "unknown"


@dataclass
class UploadJob:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    officer_id: str = ""
    file_path: str = ""             # Path inside Supabase Storage
    file_type: FileType = FileType.UNKNOWN
    original_filename: str = ""

    status: JobStatus = JobStatus.PENDING
    progress_pct: int = 0
    current_step: str = ""
    error_message: str = ""

    # Schema detected by AI column mapper
    detected_schema: dict[str, Any] = field(default_factory=dict)

    # Stats
    total_rows: int = 0
    parsed_rows: int = 0
    failed_rows: int = 0

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    # Notification preference set by client
    notify_via_sse: bool = True
    notify_via_firebase: bool = True