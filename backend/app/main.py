from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.auth.routes import router as auth_router
from app.api.debug.graph_check import router as debug_graph_check_router
from app.api.simulate.ingest import router as simulate_ingest_router
from app.api.simulate.send_confirm import router as simulate_send_confirm_router
from app.api.web.dashboard import router as dashboard_router
from app.api.web.drafts import router as drafts_router
from app.api.web.mailboxes import router as mailboxes_router
from app.api.web.reply_memory import router as reply_memory_router
from app.api.web.skills import router as skills_router
from app.api.web.threads import router as threads_router
from app.api.webhooks.graph import router as graph_webhook_router
from app.api.webhooks.slack_actions import router as slack_actions_router
from app.core.config import get_settings
from app.core.dependencies import (
    close_graph_client,
    close_openai_client,
    close_redis,
    close_slack_app,
    get_redis,
    get_slack_app,
)
from app.core.exceptions import (
    AuditError,
    AuthError,
    ClassificationError,
    DraftGenerationError,
    DraftNotFoundError,
    GraphClientError,
    InboxTriageError,
    InvalidCredentialsError,
    InvalidCursorError,
    InvalidTokenError,
    ReplyMemoryNotFoundError,
    ReusedRefreshTokenError,
    RuleEngineError,
    SkillBudgetExceededError,
    SkillNameConflictError,
    SkillNotFoundError,
    ThreadNotFoundError,
    ThreadStateError,
    TriageError,
)
from app.core.logging import configure_logging
from app.core.middleware.security_headers import SecurityHeadersMiddleware
from app.core.rate_limit import limiter
from app.db.session import dispose_engine, get_session_factory
from app.workers.graph_subscription_worker import (
    run_subscription_reconcile,
    run_subscription_renewal,
)
from app.workers.poll_fallback_worker import run_poll_all_mailboxes

logger = structlog.get_logger(__name__)

EXCEPTION_STATUS_MAP: dict[type[InboxTriageError], int] = {
    ClassificationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    TriageError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RuleEngineError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    GraphClientError: status.HTTP_502_BAD_GATEWAY,
    DraftGenerationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DraftNotFoundError: status.HTTP_404_NOT_FOUND,
    ThreadNotFoundError: status.HTTP_404_NOT_FOUND,
    SkillNotFoundError: status.HTTP_404_NOT_FOUND,
    ReplyMemoryNotFoundError: status.HTTP_404_NOT_FOUND,
    SkillNameConflictError: status.HTTP_409_CONFLICT,
    SkillBudgetExceededError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ThreadStateError: status.HTTP_409_CONFLICT,
    AuditError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    InvalidTokenError: status.HTTP_401_UNAUTHORIZED,
    ReusedRefreshTokenError: status.HTTP_401_UNAUTHORIZED,
    AuthError: status.HTTP_401_UNAUTHORIZED,
    InvalidCursorError: status.HTTP_400_BAD_REQUEST,
}

_scheduler: AsyncIOScheduler | None = None


async def _ping_redis() -> Redis:
    redis = await get_redis()
    await redis.ping()
    logger.info("redis_connected")
    return redis


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _scheduler
    logger.info("app_startup")

    settings = get_settings()
    security_errors = settings.validate_production_security()
    if security_errors:
        for err in security_errors:
            logger.critical("startup_security_config_invalid", error=err)
        raise RuntimeError(
            "Refusing to start: production security configuration invalid: "
            + "; ".join(security_errors)
        )

    await _ping_redis()

    # Construct once at startup (or resolve None when Slack env vars are unset).
    get_slack_app(settings)

    if not settings.anthropic_api_key.strip():
        logger.warning(
            "anthropic_api_key_missing",
            hint="Set ANTHROPIC_API_KEY in backend/.env — triage will fail until set",
        )
    if not settings.openai_api_key.strip():
        logger.warning(
            "openai_api_key_missing",
            hint="Set OPENAI_API_KEY in backend/.env — email embeddings will fail until set",
        )
    try:
        await run_subscription_reconcile()
    except Exception:
        logger.exception("startup_subscription_reconcile_failed")

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        run_subscription_renewal,
        trigger="interval",
        hours=settings.subscription_renew_interval_hours,
        id="graph_subscription_renewal",
        replace_existing=True,
    )
    _scheduler.add_job(
        run_poll_all_mailboxes,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="graph_poll_fallback",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "scheduler_started",
        renew_hours=settings.subscription_renew_interval_hours,
        poll_seconds=settings.poll_interval_seconds,
    )

    yield

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    await close_slack_app()
    await close_openai_client()
    await close_graph_client()
    await close_redis()
    await dispose_engine()
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(environment=settings.environment)
    is_local = settings.environment == "local"
    # Dev routers require BOTH local environment and an explicit enable flag.
    mount_dev_routes = is_local and settings.enable_dev_routes

    app = FastAPI(
        title="Inbox Triage Automation",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if is_local else None,
        redoc_url="/redoc" if is_local else None,
        openapi_url="/openapi.json" if is_local else None,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Middleware order: last added = outermost. CORS outermost for preflight.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(SlowAPIMiddleware)
    if not is_local:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[settings.api_host, f"*.{settings.api_host}"],
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        expose_headers=[],
    )

    @app.get("/health", status_code=status.HTTP_200_OK, response_model=None)
    @limiter.limit(settings.api_default_rate_limit)
    async def health_check(request: Request) -> JSONResponse:
        redis_status = "error"
        db_status = "error"
        try:
            redis = await get_redis()
            await redis.ping()
            redis_status = "ok"
        except Exception:
            logger.warning("health_redis_ping_failed")
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            db_status = "ok"
        except Exception:
            logger.warning("health_database_ping_failed")

        healthy = redis_status == "ok" and db_status == "ok"
        # Public body is status-only (avoid advertising which subsystem failed).
        # Component detail stays in structured logs for operators.
        logger.info(
            "health_check",
            healthy=healthy,
            redis=redis_status,
            database=db_status,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "ok" if healthy else "error"},
        )

    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(mailboxes_router)
    app.include_router(threads_router)
    app.include_router(drafts_router)
    app.include_router(skills_router)
    app.include_router(reply_memory_router)
    app.include_router(graph_webhook_router)
    app.include_router(slack_actions_router)
    if mount_dev_routes:
        if not settings.dev_api_key.strip():
            logger.warning(
                "dev_routes_enabled_without_api_key",
                hint="Set DEV_API_KEY — simulate/debug will reject requests",
            )
        app.include_router(simulate_ingest_router)
        app.include_router(simulate_send_confirm_router)
        app.include_router(debug_graph_check_router)

    @app.exception_handler(InboxTriageError)
    async def inbox_triage_exception_handler(
        _: Request,
        exc: InboxTriageError,
    ) -> JSONResponse:
        status_code = EXCEPTION_STATUS_MAP.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        if isinstance(exc, GraphClientError):
            detail = "Upstream Microsoft Graph request failed"
        elif isinstance(exc, AuthError):
            detail = str(exc) or "Authentication failed"
        else:
            detail = str(exc)
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail, "error_type": type(exc).__name__},
        )

    return app


app = create_app()
