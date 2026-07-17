"""Local-only Graph connectivity check."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.core.dependencies import GraphAuthDep, GraphClientDep, SettingsDep
from app.core.dev_access import require_local_dev_access
from app.models.schemas.graph import GraphCheckResponseSchema

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get(
    "/graph-check",
    response_model=GraphCheckResponseSchema,
    status_code=status.HTTP_200_OK,
)
async def graph_check(
    settings: SettingsDep,
    graph_auth: GraphAuthDep,
    graph_client: GraphClientDep,
    x_dev_api_key: Annotated[str | None, Header(alias="X-Dev-Api-Key")] = None,
) -> GraphCheckResponseSchema:
    """Verify MSAL auth and read-only message listing against the first mailbox."""
    require_local_dev_access(settings, x_dev_api_key=x_dev_api_key)

    if not settings.mailbox_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TARGET_MAILBOXES is empty",
        )

    mailbox = settings.mailbox_list[0]
    await graph_auth.get_access_token()
    messages = await graph_client.list_messages(mailbox, top=5)
    subjects = [m.subject or "(no subject)" for m in messages]

    return GraphCheckResponseSchema(
        auth="ok",
        mailbox=mailbox,
        message_count=len(messages),
        sample_subjects=subjects,
    )
