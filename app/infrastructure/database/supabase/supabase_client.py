"""
infrastructure/db/supabase_client.py
──────────────────────────────────────
Supabase client singleton.

We use the SERVICE ROLE key so the backend can bypass RLS where needed
(e.g. Celery workers inserting rows on behalf of any officer).
The Android app itself uses the anon key with RLS — never expose the
service role key to the client.

Usage:
    from app.infrastructure.db.supabase_client import get_supabase
    db = get_supabase()
    result = db.table("upload_jobs").select("*").eq("id", job_id).execute()
"""

from __future__ import annotations
from functools import lru_cache
from supabase import create_client, Client
from config import get_settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """
    Returns a cached Supabase client.
    Thread-safe: lru_cache guarantees one instance per process.
    Each Celery worker process gets its own instance — that's fine.
    """
    s = get_settings()
    client: Client = create_client(
        supabase_url=s.supabase_url,
        supabase_key=s.supabase_service_role_key,
    )
    return client
