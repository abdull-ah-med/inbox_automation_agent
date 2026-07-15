from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.api.debug.graph_check import router as debug_graph_check_router
from app.api.simulate.ingest import router as simulate_ingest_router
from app.api.simulate.send_confirm import router as simulate_send_confirm_router
from app.api.webhooks.graph import router as graph_webhook_router
from app.api.webhooks.slack_actions import router as slack_actions_router
from app.core.config import get_settings
from app.core.dependencies import close_graph_client, close_redis, get_redis
from app.core.exceptions import (
    AuditError,
    ClassificationError,
    DraftGenerationError,
    GraphClientError,
    InboxTriageError,
    RuleEngineError,
    ThreadStateError,
)
from app.core.logging import configure_logging
from app.db.session import dispose_engine
from app.workers.graph_subscription_worker import (
    run_subscription_reconcile,
    run_subscription_renewal,
)
from app.workers.poll_fallback_worker import run_poll_all_mailboxes

logger = structlog.get_logger(__name__)

EXCEPTION_STATUS_MAP: dict[type[InboxTriageError], int] = {
    ClassificationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RuleEngineError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    GraphClientError: status.HTTP_502_BAD_GATEWAY,
    DraftGenerationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ThreadStateError: status.HTTP_409_CONFLICT,
    AuditError: status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    await _ping_redis()

    settings = get_settings()
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
    await close_graph_client()
    await close_redis()
    await dispose_engine()
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    configure_logging(environment=get_settings().environment)

    app = FastAPI(
        title="Inbox Triage Automation",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", status_code=status.HTTP_200_OK)
    async def health_check() -> dict[str, str]:
        redis_status = "error"
        try:
            redis = await get_redis()
            await redis.ping()
            redis_status = "ok"
        except Exception:
            logger.warning("health_redis_ping_failed")
        return {"status": "ok", "redis": redis_status}

    app.include_router(graph_webhook_router)
    app.include_router(slack_actions_router)
    app.include_router(simulate_ingest_router)
    app.include_router(simulate_send_confirm_router)
    app.include_router(debug_graph_check_router)

    @app.exception_handler(InboxTriageError)
    async def inbox_triage_exception_handler(
        _: Request,
        exc: InboxTriageError,
    ) -> JSONResponse:
        status_code = EXCEPTION_STATUS_MAP.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc), "error_type": type(exc).__name__},
        )

    return app


app = create_app()
