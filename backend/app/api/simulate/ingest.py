"""Phase 0 mock email ingestion endpoint — persist-only (no AI pipeline)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import DbSessionDep, RedisDep, SettingsDep
from app.models.schemas.graph import IngestResultSchema, SimulateIngestRequestSchema
from app.services import ingestion_service

router = APIRouter(prefix="/simulate", tags=["simulate"])


@router.post(
    "/ingest",
    response_model=IngestResultSchema,
    status_code=status.HTTP_200_OK,
)
async def simulate_ingest(
    payload: SimulateIngestRequestSchema,
    settings: SettingsDep,
    session: DbSessionDep,
    redis: RedisDep,
) -> IngestResultSchema:
    """Accept a simulated email and persist via the shared ingestion path.

    Available in local/staging only. Does not call Graph, triage, drafts, or Slack.
    """
    if settings.environment == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        async with session.begin():
            result = await ingestion_service.ingest_simulated_message(
                session=session,
                redis=redis,
                payload=payload,
            )
        if result.status == "ingested":
            await ingestion_service.complete_ingest_dedup(redis, payload.mailbox, payload.message_id)
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Simulate ingest failed",
        ) from exc
