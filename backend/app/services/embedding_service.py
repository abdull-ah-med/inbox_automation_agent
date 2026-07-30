"""OpenAI embedding generation + persist for non-spam emails.

Official API (https://platform.openai.com/docs/guides/embeddings,
https://platform.openai.com/docs/api-reference/embeddings/create):

    await client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
        encoding_format="float",
        dimensions=1536,  # optional; default for text-embedding-3-small is 1536
    )

Failures are isolated — callers use ``embed_and_store_safe`` so ingest never crashes.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.pii_redact import scrub_email_for_llm
from app.models.schemas.email import EmailMessageSchema
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import embedding_repo

logger = structlog.get_logger(__name__)

# text-embedding-3-small max input is 8192 tokens (OpenAI embeddings guide).
# ~4 chars/token → stay well under the limit without tiktoken in the hot path.
_MAX_EMBED_CHARS = 24_000
_PREVIEW_CHARS = 500


def _build_embed_text(email: EmailMessageSchema) -> str:
    scrubbed = scrub_email_for_llm(email)
    subject = (scrubbed.subject or "").strip()
    body = (scrubbed.body_text or "").strip()
    combined = f"Subject: {subject}\n\n{body}".strip()
    if len(combined) > _MAX_EMBED_CHARS:
        combined = combined[:_MAX_EMBED_CHARS]
    return combined


def _body_preview_for_store(email: EmailMessageSchema) -> str:
    preview = (email.body_preview or email.body_text or email.subject or "").strip()
    if len(preview) > _PREVIEW_CHARS:
        return preview[:_PREVIEW_CHARS]
    return preview or "(empty)"


async def embed_email(
    email: EmailMessageSchema,
    *,
    client: AsyncOpenAI,
    settings: Settings,
) -> list[float]:
    """Scrub, call OpenAI embeddings, return a vector of ``settings.embedding_dimension``."""
    text = _build_embed_text(email)
    if not text:
        raise ValueError("embed_email requires non-empty subject/body after scrub")

    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=text,
        encoding_format="float",
        dimensions=settings.embedding_dimension,
    )
    if not response.data:
        raise RuntimeError("OpenAI embeddings response contained no data")
    vector = list(response.data[0].embedding)
    if len(vector) != settings.embedding_dimension:
        raise ValueError(
            f"embedding dimension mismatch: got {len(vector)}, "
            f"expected {settings.embedding_dimension}"
        )
    return vector


async def store_email_embedding(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    embedding: list[float],
) -> EmbeddingMatchSchema:
    """Persist embedding + participant metadata via the repository."""
    return await embedding_repo.insert_embedding(
        session,
        embedding=embedding,
        mailbox=email.mailbox,
        conversation_id=email.conversation_id,
        sender_email=email.sender,
        recipient_emails=list(email.to_recipients),
        cc_emails=list(email.cc_recipients),
        sent_at=email.received_at,
        body_preview=_body_preview_for_store(email),
        graph_message_id=email.message_id,
    )


async def embed_text(
    text: str,
    *,
    client: AsyncOpenAI,
    settings: Settings,
) -> list[float]:
    """Embed arbitrary text (scrubbed by caller). Used for reply memory RAG."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("embed_text requires non-empty text")
    if len(cleaned) > _MAX_EMBED_CHARS:
        cleaned = cleaned[:_MAX_EMBED_CHARS]

    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=cleaned,
        encoding_format="float",
        dimensions=settings.embedding_dimension,
    )
    if not response.data:
        raise RuntimeError("OpenAI embeddings response contained no data")
    vector = list(response.data[0].embedding)
    if len(vector) != settings.embedding_dimension:
        raise ValueError(
            f"embedding dimension mismatch: got {len(vector)}, "
            f"expected {settings.embedding_dimension}"
        )
    return vector


async def embed_and_store(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    client: AsyncOpenAI,
    settings: Settings,
) -> EmbeddingMatchSchema:
    """Generate + persist; raises on failure (use ``embed_and_store_safe`` in pipeline)."""
    vector = await embed_email(email, client=client, settings=settings)
    return await store_email_embedding(session, email=email, embedding=vector)


async def embed_and_store_safe(
    session: AsyncSession,
    *,
    email: EmailMessageSchema,
    client: AsyncOpenAI,
    settings: Settings,
) -> EmbeddingMatchSchema | None:
    """Embed + store; log and return None on any failure (never raises)."""
    try:
        stored = await embed_and_store(
            session,
            email=email,
            client=client,
            settings=settings,
        )
        logger.info(
            "email_embedding_stored",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            message_id=email.message_id,
            embedding_id=str(stored.id),
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )
        return stored
    except Exception:
        logger.exception(
            "email_embedding_failed",
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            message_id=email.message_id,
            model=settings.embedding_model,
        )
        return None
