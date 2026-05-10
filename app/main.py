"""
Fuata Capital Backend — FastAPI Entrypoint
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import os
import sys

# Simple absolute path setup
current_dir = os.path.dirname(os.path.abspath(__file__))  # /path/to/app
project_root = os.path.dirname(current_dir)              # /path/to/project
sys.path.insert(0, current_dir)    # Add src to path
sys.path.insert(0, project_root)   # Add project root to path

from config import get_settings
from api.v1.router import api_v1_router

import firebase_admin
from firebase_admin import credentials

log = structlog.get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Firebase once at startup
    s = get_settings()
    if not firebase_admin._apps:  # guard against double-init
        cred = credentials.Certificate(s.firebase_service_account_json)
        firebase_admin.initialize_app(
            cred,
            {"databaseURL": s.firebase_database_url},
        )
    log.info("startup", env=s.app_env)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    s = get_settings()

    app = FastAPI(
        title="Fuata Capital API",
        version="1.0.0",
        description=(
            "Client management & AI-powered collections strategy backend. "
            "Built for scale."
        ),
        docs_url="/docs" if not s.is_production else None,
        redoc_url="/redoc" if not s.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ─────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],          # Tighten in prod to your domains
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routes ───────────────────────────────────────────────────
    app.include_router(api_v1_router)

    # ── Global error handler ─────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        log.error("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred."},
        )

    # ── Health check ─────────────────────────────────────────────
    @app.get("/api/v1/health", tags=["Infra"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    s = get_settings()

    uvicorn.run(
        "main:app",   # change if file name differs
        host=s.app_host,
        port=s.app_port,
        reload=not s.is_production,
    )

# Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
# .venv\Scripts\activate
# ngrok http --url=stable-wholly-crappie.ngrok-free.app 8000
# celery -A app.infrastructure.workers.celery_app worker --loglevel=info -P solo