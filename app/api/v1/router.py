"""
app/api/v1/router.py
"""

from fastapi import APIRouter
import os, sys

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from .upload    import router as upload_router
from .kill_list import router as kill_list_router
from .officers  import router as officers_router
from .clients   import router as clients_router        # NEW

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(upload_router)
api_v1_router.include_router(kill_list_router)
api_v1_router.include_router(officers_router)
api_v1_router.include_router(clients_router)           # NEW