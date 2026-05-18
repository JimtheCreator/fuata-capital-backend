"""
api/v1/dependencies.py
────────────────────────
FastAPI dependency injection.

Every protected endpoint uses Depends(get_officer_uid) to:
  1. Extract the Firebase Bearer token from the Authorization header
  2. Verify it with Firebase Admin SDK
  3. Return the officer's uid (Firebase UID = officer_id everywhere)

Repository factories (get_job_repo, get_client_repo, etc.) are also
defined here so routers stay thin and dependencies are swappable for tests.

Usage in a router:
    from app.api.v1.dependencies import get_officer_uid, get_job_repo

    @router.get("/something")
    async def handler(
        officer_uid: str = Depends(get_officer_uid),
        job_repo: SupabaseJobRepository = Depends(get_job_repo),
    ):
        ...
"""

from __future__ import annotations

import structlog
from fastapi import Depends, Header, HTTPException, status
from firebase_admin import auth as firebase_auth
from firebase_admin.auth import InvalidIdTokenError, ExpiredIdTokenError

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)


from infrastructure.database.supabase.supabase_client import get_supabase
from infrastructure.database.supabase.repo.supabase_job_repository import SupabaseJobRepository
from infrastructure.database.supabase.repo.supabase_client_repository import SupabaseClientRepository
from infrastructure.database.supabase.repo.supabase_killlist_repository import SupabaseKillListRepository
from infrastructure.database.supabase.repo.supabase_storage_repository import SupabaseStorageRepository
from utils.logger import logger


log = logger


# ── Auth ──────────────────────────────────────────────────────────


async def get_officer_uid(
    authorization: str = Header(..., description="Firebase Bearer token"),
) -> str:
    """
    Validates the Firebase ID token sent by the Android app.
    Returns the Firebase UID, which we use as officer_id everywhere.

    Expected header:
        Authorization: Bearer <firebase_id_token>
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must start with 'Bearer '.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is empty.",
        )

    try:
        decoded = firebase_auth.verify_id_token(token, check_revoked=True)
        uid: str = decoded["uid"]
        
        db = get_supabase()
        db.table("officers").upsert(
            {
                "id": uid,
                "email": decoded.get("email", ""),
                "display_name": decoded.get("name", ""),
            },
            on_conflict="id"   # do nothing if row already exists
        ).execute()

    
        log.info("token_verified", uid=uid)
        return uid

    except ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please re-authenticate.",
        )
    except InvalidIdTokenError as exc:
        log.warning("invalid_token", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )
    except Exception as exc:
        log.error("auth_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service error.",
        )


# ── Repository factories ──────────────────────────────────────────
# Each one is a FastAPI dependency. The Supabase client is a singleton
# so there's no connection overhead per request.


def get_job_repo() -> SupabaseJobRepository:
    return SupabaseJobRepository(get_supabase())


def get_client_repo() -> SupabaseClientRepository:
    return SupabaseClientRepository(get_supabase())


def get_killlist_repo() -> SupabaseKillListRepository:
    return SupabaseKillListRepository(get_supabase())


def get_storage_repo() -> SupabaseStorageRepository:
    return SupabaseStorageRepository(get_supabase())
