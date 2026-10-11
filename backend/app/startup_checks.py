"""Fail-fast configuration checks shared by the API (``app.main``) and the worker (``app.worker``)."""

from __future__ import annotations

from app.config import Settings
from app.logging import get_logger

log = get_logger("app.startup_checks")

# Publicly known placeholder keys (config default, .env.example). With one of these anyone can mint staff and
# parent JWTs, so a prod process must refuse to start.
PLACEHOLDER_SECRET_KEYS: frozenset[str] = frozenset(
    {"dev-secret-key-change-me-please-32-bytes", "change-me-please-32-bytes-minimum"}
)
MIN_SECRET_KEY_LENGTH = 32


def check_secret_key(cfg: Settings) -> None:
    """Fail fast in ``APP_ENV=prod`` when ``SECRET_KEY`` is a placeholder or shorter than 32 characters.

    Outside prod the problem is only logged, so local development keeps working with the defaults.
    """
    weak = cfg.secret_key in PLACEHOLDER_SECRET_KEYS or len(cfg.secret_key) < MIN_SECRET_KEY_LENGTH
    if not weak:
        return
    if cfg.app_env == "prod":
        raise RuntimeError(
            "SECRET_KEY is a placeholder or shorter than 32 characters; set a random SECRET_KEY "
            "(e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`) before running APP_ENV=prod"
        )
    log.warning("weak_secret_key", app_env=cfg.app_env)
