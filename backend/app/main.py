from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.simulate.ingest import router as simulate_ingest_router
from app.api.simulate.send_confirm import router as simulate_send_confirm_router
from app.api.webhooks.graph import router as graph_webhook_router
from app.api.webhooks.slack_actions import router as slack_actions_router
from app.core.dependencies import close_redis
from app.core.exceptions import (
    AuditError,
    ClassificationError,
    DraftGenerationError,
    GraphClientError,
    InboxTriageError,
    RuleEngineError,
    ThreadStateError,
)
from app.db.session import dispose_engine

logger = structlog.get_logger(__name__)

EXCEPTION_STATUS_MAP: dict[type[InboxTriageError], int] = {
    ClassificationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RuleEngineError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    GraphClientError: status.HTTP_502_BAD_GATEWAY,
    DraftGenerationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ThreadStateError: status.HTTP_409_CONFLICT,
    AuditError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("app_startup")
    yield
    await close_redis()
    await dispose_engine()
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Inbox Triage Automation",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", status_code=status.HTTP_200_OK)
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(graph_webhook_router)
    app.include_router(slack_actions_router)
    app.include_router(simulate_ingest_router)
    app.include_router(simulate_send_confirm_router)

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
