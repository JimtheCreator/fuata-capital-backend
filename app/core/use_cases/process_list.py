from app.infrastructure.workers.celery_app import process_file_task

class ListProcessorUseCase:
    def __init__(self, db_repo, storage_repo, notify_service):
        self.db = db_repo
        self.storage = storage_repo
        self.notify = notify_service

    async def execute(self, user_id: str, file_key: str):
        # 1. Update status to 'Processing' in Supabase/Firebase
        await self.db.update_job_status(user_id, "PROCESSING")
        
        # 2. Hand off to Celery (The Muscle)
        # This returns immediately, keeping FastAPI free for more traffic
        process_file_task.delay(user_id, file_key)
        
        return {"status": "queued", "message": "The Ghost is analyzing your list."}