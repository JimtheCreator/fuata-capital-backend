from dataclasses import dataclass
from ..domain.repositories.interfaces import IUploadJobRepository
from ..domain.entities.upload_job import UploadJob


@dataclass
class GetJobStatusResponse:
    job_id: str
    status: str
    progress_pct: int
    current_step: str
    total_rows: int
    parsed_rows: int
    failed_rows: int
    error_message: str
    completed_at: str | None

    @classmethod
    def from_job(cls, job: UploadJob) -> "GetJobStatusResponse":
        return cls(
            job_id=job.id,
            status=job.status,
            progress_pct=job.progress_pct,
            current_step=job.current_step,
            total_rows=job.total_rows,
            parsed_rows=job.parsed_rows,
            failed_rows=job.failed_rows,
            error_message=job.error_message,
            completed_at=(
                job.completed_at.isoformat() if job.completed_at else None
            ),
        )

class GetJobStatusUseCase:
    def __init__(self, job_repo: IUploadJobRepository) -> None:
        self._jobs = job_repo

    async def execute(
        self, job_id: str, officer_id: str
    ) -> GetJobStatusResponse:
        job = await self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found.")
        if job.officer_id != officer_id:
            raise PermissionError("Access denied.")
        return GetJobStatusResponse.from_job(job)