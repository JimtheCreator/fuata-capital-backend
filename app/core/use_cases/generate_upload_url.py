from dataclasses import dataclass
from ..domain.repositories.interfaces import IStorageRepository, IUploadJobRepository
from ..domain.entities.upload_job import UploadJob


@dataclass
class GenerateUploadUrlRequest:
    officer_id: str
    filename: str
    notify_via_sse: bool = True
    notify_via_firebase: bool = True


@dataclass
class GenerateUploadUrlResponse:
    job_id: str
    upload_url: str
    file_path: str
    expires_in: int
    confirm_endpoint: str


class GenerateUploadUrlUseCase:
    """
    Step 1 of the pipeline.
    Creates a job record and returns a pre-signed URL so the
    Android app can upload directly to Supabase Storage —
    the FastAPI server never touches the file bytes.
    """

    def __init__(
        self,
        storage_repo: IStorageRepository,
        job_repo: IUploadJobRepository,
    ) -> None:
        self._storage = storage_repo
        self._jobs = job_repo

    async def execute(
        self, req: GenerateUploadUrlRequest
    ) -> GenerateUploadUrlResponse:
        url_data = await self._storage.generate_presigned_upload_url(
            req.officer_id, req.filename
        )

        job = UploadJob(
            officer_id=req.officer_id,
            file_path=url_data["file_path"],
            original_filename=req.filename,
            notify_via_sse=req.notify_via_sse,
            notify_via_firebase=req.notify_via_firebase,
        )
        job = await self._jobs.create(job)

        return GenerateUploadUrlResponse(
            job_id=job.id,
            upload_url=url_data["upload_url"],
            file_path=url_data["file_path"],
            expires_in=int(url_data.get("expires_in", 900)),
            confirm_endpoint=f"/api/v1/upload/confirm/{job.id}",
        )