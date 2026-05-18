"""
app/presentation/api/v1/router.py
───────────────────────────────────
Central router for /api/v1 — imported by main.py.
Add new feature routers here as the app grows.
"""

from fastapi import APIRouter, Depends
import os
import sys

from app.api.v1.dependencies import get_officer_uid
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)


from .upload import router as upload_router
from .kill_list import router as kill_list_router
from .officers import router as officers_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(upload_router)
api_v1_router.include_router(kill_list_router)
api_v1_router.include_router(officers_router)