from dataclasses import dataclass
from ..domain.repositories.interfaces import IUploadJobRepository
from ..domain.entities.upload_job import JobStatus


@dataclass
class ConfirmUploadRequest:
    job_id: str
    officer_id: str


@dataclass
class ConfirmUploadResponse:
    job_id: str
    status: str
    message: str


class ConfirmUploadUseCase:
    """
    Called by the Android app once it finishes uploading to Supabase.
    Verifies ownership then enqueues the heavy-lift Celery task.
    The actual enqueueing is injected to keep this layer framework-free.
    """

    def __init__(
        self,
        job_repo: IUploadJobRepository,
        enqueue_fn,  # Callable[[str], None] — injected at runtime
    ) -> None:
        self._jobs = job_repo
        self._enqueue = enqueue_fn

    async def execute(
        self, req: ConfirmUploadRequest
    ) -> ConfirmUploadResponse:
        job = await self._jobs.get(req.job_id)

        if not job:
            raise ValueError(f"Job {req.job_id} not found.")

        if job.officer_id != req.officer_id:
            raise PermissionError("Job does not belong to this officer.")

        if job.status not in (JobStatus.PENDING,):
            raise ValueError(
                f"Job is already {job.status}. Cannot re-queue."
            )

        await self._jobs.update_status(
            req.job_id,
            JobStatus.DOWNLOADING,
            current_step="Queued for processing",
            progress_pct=5,
        )

        # Enqueue — the actual Celery call lives in infrastructure
        self._enqueue(req.job_id)

        return ConfirmUploadResponse(
            job_id=req.job_id,
            status=JobStatus.DOWNLOADING,
            message="File received. Analysis in progress. You will be notified when the kill-list is ready.",
        )