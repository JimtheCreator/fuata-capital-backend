# app/api/v1/officers.py  — new file
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from .dependencies import get_officer_uid
from infrastructure.database.supabase.supabase_client import get_supabase

router = APIRouter(prefix="/officers", tags=["Officers"])

class RegisterOfficerRequest(BaseModel):
    display_name: str
    email: str

@router.post("/me")
async def register_officer(
    body: RegisterOfficerRequest,
    officer_uid: str = Depends(get_officer_uid),
):
    db = get_supabase()
    db.table("officers").upsert(
        {
            "id": officer_uid,
            "email": body.email,
            "display_name": body.display_name,
        },
        on_conflict="id"
    ).execute()
    return {"id": officer_uid, "status": "ok"}