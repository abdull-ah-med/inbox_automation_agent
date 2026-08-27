"""On-demand Graph HTML for Outlook View — not persisted; ingest stays Prefer-text."""

from __future__ import annotations

import html
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ThreadNotFoundError
from app.core.tenant_scope import TenantScope
from app.graph.client import GraphClient
from app.models.schemas.dashboard import MessageHtmlBody
from app.models.schemas.graph import GraphFileAttachmentSchema
from app.repositories import message_repo, thread_repo

_CID_ANGLE_RE = re.compile(r"^<(.*)>$")


def replace_cid_images(
    body_html: str,
    attachments: list[GraphFileAttachmentSchema],
) -> str:
    """Replace cid: references with data: URIs from file attachments."""
    out = body_html
    for attachment in attachments:
        content_id = (attachment.content_id or "").strip()
        content_bytes = attachment.content_bytes
        if not content_id or not content_bytes:
            continue
        match = _CID_ANGLE_RE.match(content_id)
        if match:
            content_id = match.group(1)
        content_type = (attachment.content_type or "application/octet-stream").strip()
        data_uri = f"data:{content_type};base64,{content_bytes}"
        for candidate in (content_id, f"<{content_id}>"):
            out = out.replace(f"cid:{candidate}", data_uri)
            out = out.replace(f'cid:"{candidate}"', data_uri)
            out = out.replace(f"cid:'{candidate}'", data_uri)
    return out


async def get_message_html(
    session: AsyncSession,
    settings: Settings,
    graph_client: GraphClient,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> MessageHtmlBody:
    scope = TenantScope.from_settings(settings)
    thread = await thread_repo.get_by_id(session, thread_id, scope)
    if thread is None or not settings.mailbox_allowed(thread.mailbox):
        raise ThreadNotFoundError(f"Thread not found: {thread_id}")

    message = await message_repo.get_by_id(session, message_id, scope)
    if message is None or message.thread_id != thread_id:
        raise ThreadNotFoundError(f"Message not found: {message_id}")

    graph_message = await graph_client.get_message_html(
        thread.mailbox,
        message.graph_message_id,
    )
    body = graph_message.body
    raw = (body.content if body is not None else None) or ""
    content_type_raw = (body.content_type if body is not None else None) or "html"
    content_type = content_type_raw.strip().lower()

    if content_type != "html":
        escaped = html.escape(raw)
        return MessageHtmlBody(
            content_type="text",
            html=f'<pre style="white-space:pre-wrap;margin:0">{escaped}</pre>',
        )

    attachments = await graph_client.list_message_attachments(
        thread.mailbox,
        message.graph_message_id,
    )
    resolved = replace_cid_images(raw, attachments)
    return MessageHtmlBody(content_type="html", html=resolved)
