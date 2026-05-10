from abc import ABC, abstractmethod
from typing import Any
from ..entities.client import Client
from ..entities.upload_job import UploadJob, JobStatus
from ..entities.kill_list_event import KillListEvent


class IClientRepository(ABC):
    @abstractmethod
    async def bulk_insert(self, clients: list[Client]) -> int:
        """Returns number of inserted rows."""
        ...

    @abstractmethod
    async def get_by_job(self, job_id: str) -> list[Client]:
        ...

    @abstractmethod
    async def get_prioritised_for_officer(
        self, officer_id: str
    ) -> dict[str, list[Client]]:
        """Returns {OVERDUE: [...], DUE_TOMORROW: [...], DUE_THIS_WEEK: [...]}"""
        ...


class IUploadJobRepository(ABC):
    @abstractmethod
    async def create(self, job: UploadJob) -> UploadJob:
        ...

    @abstractmethod
    async def get(self, job_id: str) -> UploadJob | None:
        ...

    @abstractmethod
    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        progress_pct: int | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        ...


class IKillListRepository(ABC):
    @abstractmethod
    async def bulk_insert_events(
        self, events: list[KillListEvent]
    ) -> int:
        ...

    @abstractmethod
    async def get_by_officer(
        self, officer_id: str
    ) -> list[KillListEvent]:
        ...

    @abstractmethod
    async def get_by_job(self, job_id: str) -> list[KillListEvent]:
        ...


class IStorageRepository(ABC):
    @abstractmethod
    async def generate_presigned_upload_url(
        self,
        officer_id: str,
        filename: str,
    ) -> dict[str, str]:
        """Returns {upload_url, file_path, expires_in}"""
        ...

    @abstractmethod
    async def download_bytes(self, file_path: str) -> bytes:
        ...