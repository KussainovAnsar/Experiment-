"""FastAPI application factory: routers, CORS, request ids, JSON logging, error handling, lifespan."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, centers, health, lessons, media, parent, people, progress, reports
from app.config import get_settings
from app.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(level="DEBUG" if settings.app_env == "dev" else "INFO", json_output=settings.app_env != "dev")
log = get_logger("app.main")

try:
    from app.api.telegram import router as telegram_router
except ImportError as _exc:  # module C not present yet
    telegram_router = None
    log.warning("telegram_router_missing", error=str(_exc))

try:
    from app.bot.telegram import start_polling_background, stop_polling_background
except ImportError as _exc:  # module C not present yet
    start_polling_background = None  # type: ignore[assignment]
    stop_polling_background = None  # type: ignore[assignment]
    log.warning("telegram_bot_missing", error=str(_exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start/stop background services (Telegram polling when configured)."""
    settings.media_root.mkdir(parents=True, exist_ok=True)
    log.info("app_start", app_env=settings.app_env, pipeline_mode=settings.pipeline_mode)
    if start_polling_background is not None:
        try:
            await start_polling_background(app)
        except Exception as exc:  # never block API start on bot problems
            log.error("telegram_polling_start_failed", error=str(exc))
    try:
        yield
    finally:
        if stop_polling_background is not None:
            try:
                await stop_polling_background(app)
            except Exception as exc:
                log.error("telegram_polling_stop_failed", error=str(exc))
        log.info("app_stop")


def create_app() -> FastAPI:
    """Build the FastAPI app with all routers and middleware."""
    app = FastAPI(
        title="LessonLens API",
        version=health.APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error")
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Внутренняя ошибка сервера"}
            )
        response.headers["X-Request-ID"] = request_id
        if response.status_code >= 500:
            log.error("request_failed", status_code=response.status_code)
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Внутренняя ошибка сервера"}
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        parts = []
        for err in errors:
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            parts.append(f"{loc}: {err.get('msg', '')}".strip(": "))
        detail = "Ошибка валидации: " + "; ".join(parts) if parts else "Ошибка валидации"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": detail, "errors": _jsonable(errors)},
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(centers.router)
    app.include_router(people.router)
    app.include_router(lessons.router)
    app.include_router(reports.router)
    app.include_router(progress.router)
    app.include_router(parent.router)
    app.include_router(media.router)
    if telegram_router is not None:
        app.include_router(telegram_router)
    return app


def _jsonable(errors: list[dict]) -> list[dict]:
    """Strip non-serialisable context (exceptions, bytes) from pydantic error dicts."""
    cleaned = []
    for err in errors:
        item = {k: v for k, v in err.items() if k in ("type", "loc", "msg")}
        item["loc"] = [str(p) for p in err.get("loc", ())]
        cleaned.append(item)
    return cleaned


app = create_app()
