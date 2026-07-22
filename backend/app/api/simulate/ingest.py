"""Local mock email ingestion — persist + triage + draft (no Slack / Graph)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.core.dependencies import AnthropicClientDep, DbSessionDep, RedisDep, SettingsDep
from app.core.dev_access import require_local_dev_access
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.graph import IngestResultSchema, SimulateIngestRequestSchema
from app.services import ingestion_service, pipeline_service

router = APIRouter(prefix="/simulate", tags=["simulate"])

_TRIAGE_ELIGIBLE = frozenset({"ingested", "retry_triage"})


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
    anthropic: AnthropicClientDep,
    x_dev_api_key: Annotated[str | None, Header(alias="X-Dev-Api-Key")] = None,
) -> IngestResultSchema:
    """Accept a simulated email, persist it, then run triage and draft when needed.

    Local-only (ENABLE_DEV_ROUTES + X-Dev-Api-Key). Does not call Graph or Slack.
    Duplicates skip triage/draft and return ingest status only.
    """
    require_local_dev_access(settings, x_dev_api_key=x_dev_api_key)

    if settings.mailbox_list and not settings.mailbox_allowed(payload.mailbox):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mailbox is not in TARGET_MAILBOXES",
        )

    try:
        async with session.begin():
            result = await ingestion_service.ingest_simulated_message(
                session=session,
                redis=redis,
                payload=payload,
            )
        if result.status not in _TRIAGE_ELIGIBLE:
            return result

        try:
            async with session.begin():
                state = await pipeline_service.run_after_ingest(
                    session=session,
                    redis=redis,
                    settings=settings,
                    client=anthropic,
                    ingest_result=result,
                )
        except Exception:
            await ingestion_service.release_ingest_dedup(redis, payload.mailbox, payload.message_id)
            raise

        if state.triage is None:
            await ingestion_service.release_ingest_dedup(redis, payload.mailbox, payload.message_id)
            return result.model_copy(
                update={
                    "triage": None,
                    "draft": state.draft,
                    "draft_status": state.draft_status,
                    "prompt_version": PROMPT_VERSION,
                }
            )

        if not pipeline_service.pipeline_ready_for_dedup(state):
            await ingestion_service.release_ingest_dedup(redis, payload.mailbox, payload.message_id)
            return result.model_copy(
                update={
                    "triage": state.triage,
                    "draft": state.draft,
                    "draft_status": state.draft_status,
                    "prompt_version": PROMPT_VERSION,
                }
            )

        await ingestion_service.complete_ingest_dedup(redis, payload.mailbox, payload.message_id)

        return result.model_copy(
            update={
                "triage": state.triage,
                "draft": state.draft,
                "draft_status": state.draft_status,
                "prompt_version": PROMPT_VERSION,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Simulate ingest failed",
        ) from exc
