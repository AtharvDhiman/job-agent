"""FastAPI application entrypoint."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger, request_id_var
from app.db.session import engine
from app.schemas.common import HealthOut

VERSION = "1.0.0"
configure_logging(settings.log_level, settings.log_format)
log = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    log.info(
        "startup",
        environment=settings.app_env,
        llm="claude" if settings.llm_configured else "deterministic",
        automation_global_enabled=settings.automation_global_enabled,
        connectors=_connector_summary(),
    )
    if settings.is_production:
        problems = _production_preflight()
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    yield
    log.info("shutdown")


def _connector_summary() -> dict:
    from app.connectors import registry

    return {c.key: c.submission_policy_default.value for c in registry.all()}


def _production_preflight() -> list[str]:
    problems = []
    if not settings.encryption_key:
        problems.append("ENCRYPTION_KEY is required")
    if len(settings.secret_key) < 32 or settings.secret_key.startswith("dev-only"):
        problems.append("SECRET_KEY must be a strong unique value")
    if settings.database_url.startswith("sqlite"):
        problems.append("SQLite is not supported in production; use PostgreSQL")
    if settings.automation_global_enabled and not settings.browser_assistant_token:
        problems.append("BROWSER_ASSISTANT_TOKEN must be set when automation is enabled")
    return problems


app = FastAPI(
    title=settings.app_name,
    version=VERSION,
    description=(
        "AI job application agent. Discovery is limited to connectors with an explicit "
        "compliance tier; submission is review-first and requires per-platform authorization. "
        "See docs/COMPLIANCE.md."
    ),
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Assistant-Token"],
    max_age=600,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "request.unhandled", method=request.method, path=request.url.path, request_id=rid
        )
        raise
    finally:
        request_id_var.reset(token)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=elapsed_ms,
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed",
            "errors": [
                {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                for e in exc.errors()
            ],
            "request_id": request_id_var.get(),
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc), "request_id": request_id_var.get()},
    )


@app.get("/health", response_model=HealthOut, tags=["meta"])
def health() -> HealthOut:
    checks: dict = {}
    database = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        database = "error"
        checks["database_error"] = str(exc)[:300]

    redis_state = "not_configured"
    if settings.redis_url:
        try:
            import redis

            redis.Redis.from_url(settings.redis_url, socket_timeout=1).ping()
            redis_state = "ok"
        except Exception as exc:  # noqa: BLE001
            redis_state = "error"
            checks["redis_error"] = str(exc)[:300]

    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=VERSION,
        environment=settings.app_env,
        database=database,
        redis=redis_state,
        llm="claude" if settings.llm_configured else "deterministic",
        automation_enabled=settings.automation_global_enabled,
        checks=checks,
    )


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": VERSION,
        "docs": "/docs" if not settings.is_production else "disabled",
        "api": settings.api_v1_prefix,
        "compliance": (
            "Discovery uses only connectors with an explicit compliance tier. "
            "Submission is review-first and needs per-platform authorization. "
            "CAPTCHAs, logins and bot protection are never bypassed."
        ),
    }


app.include_router(api_router, prefix=settings.api_v1_prefix)
