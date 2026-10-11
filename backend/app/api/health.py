"""GET /api/health — liveness + database check."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import AppSettings, DbSession
from app.logging import get_logger
from app.schemas.api import HealthOut

router = APIRouter(prefix="/api", tags=["health"])
log = get_logger(__name__)

APP_VERSION = "0.1.0"


@router.get("/health", response_model=HealthOut)
async def health(session: DbSession, settings: AppSettings) -> HealthOut:
    """Return service status; ``degraded`` when the database is unreachable."""
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - only on infra failure
        log.warning("health_db_error", error=str(exc))
        db_status = "error"
    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        database=db_status,  # type: ignore[arg-type]
        version=APP_VERSION,
        app_env=settings.app_env,
    )
