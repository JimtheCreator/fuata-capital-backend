"""
config.py
──────────
Centralised settings via pydantic-settings.
All values come from environment variables or a .env file.
Nothing in the codebase should hardcode credentials — use get_settings().
"""
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    # ── Supabase ──────────────────────────────────────────────────
    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(..., alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_storage_bucket: str = Field(
        default="client-lists", alias="SUPABASE_STORAGE_BUCKET"
    )
    # Pre-signed URL TTL (seconds). 15 min is plenty for mobile upload.
    upload_url_ttl_seconds: int = Field(default=900, alias="UPLOAD_URL_TTL_SECONDS")

    # ── Redis ─────────────────────────────────────────────────────
    redis_url: str = Field(default="REDIS_URL")

    # ── Firebase ─────────────────────────────────────────────────
    # Can be a file path  → "/etc/secrets/firebase.json"
    # or raw JSON string  → '{"type":"service_account", ...}'
    firebase_service_account_json: str = Field(
        ..., alias="FIREBASE_SERVICE_ACCOUNT_JSON"
    )
    firebase_database_url: str = Field(..., alias="FIREBASE_DATABASE_URL")

    # ── OpenRouter (LLM gateway) ──────────────────────────────────
    openrouter_api_key: str = Field(..., alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    # Default: a fast, cheap model. Swap to sonnet/gpt-4o for better quality.
    openrouter_model: str = Field(
        default="meta-llama/llama-3.1-8b-instruct", alias="OPENROUTER_MODEL"
    )

    # ── Kill List Schedule (stored/computed in EAT = UTC+3) ───────
    # Kill list is built for tomorrow, scheduled at 08:45 AM EAT.
    kill_list_send_hour: int = Field(default=8, alias="KILL_LIST_SEND_HOUR")
    kill_list_send_minute: int = Field(default=45, alias="KILL_LIST_SEND_MINUTE")

    # ── Celery ───────────────────────────────────────────────────
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0", alias="CELERY_BROKER_URL"
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1", alias="CELERY_RESULT_BACKEND"
    )


@lru_cache
def get_settings() -> Settings:
    """
    Cached singleton. Import and call this everywhere:
        from app.config import get_settings
        s = get_settings()
    """
    return Settings()
