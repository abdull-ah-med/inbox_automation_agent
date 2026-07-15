"""Local-only Graph connectivity check."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import GraphAuthDep, GraphClientDep, SettingsDep
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
) -> GraphCheckResponseSchema:
    """Verify MSAL auth and read-only message listing against the first mailbox."""
    if settings.environment != "local":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

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
