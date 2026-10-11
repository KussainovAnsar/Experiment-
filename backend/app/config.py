"""Application settings loaded from environment / .env files."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # database
    database_url: str = "postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens"
    database_url_sync: str = "postgresql+psycopg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens"

    # app
    app_env: Literal["dev", "prod", "test"] = "dev"
    secret_key: str = "dev-secret-key-change-me-please-32-bytes"
    public_base_url: str = "http://localhost:5173"
    api_base_url: str = "http://localhost:8000"
    media_root: Path = Field(default=BACKEND_DIR / "media")
    cors_origins: str = "http://localhost:5173"
    staff_token_ttl_days: int = 7
    parent_token_ttl_days: int = 30
    magic_link_ttl_hours: int = 72

    # pipeline
    pipeline_mode: Literal["inline", "worker"] = "inline"
    max_upload_mb: int = 2048
    keyframe_interval_sec: int = 30
    keyframe_scene_threshold: float = 0.06
    keyframe_max: int = 40
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # providers
    stt_provider: str = "mock"
    whisper_model: str = "small"
    openai_api_key: str = ""
    deepgram_api_key: str = ""
    assemblyai_api_key: str = ""
    elevenlabs_api_key: str = ""
    google_application_credentials: str = ""
    yandex_api_key: str = ""
    yandex_folder_id: str = ""

    llm_provider: str = "mock"
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_max_frames: int = 20

    # messaging
    telegram_bot_token: str = ""
    telegram_mode: Literal["polling", "webhook"] = "polling"
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""
    whatsapp_enabled: bool = False
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""

    @field_validator("media_root", mode="before")
    @classmethod
    def _resolve_media_root(cls, v: object) -> Path:
        p = Path(str(v))
        if not p.is_absolute():
            p = (REPO_DIR / p).resolve()
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
