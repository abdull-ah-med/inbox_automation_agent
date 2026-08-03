"""OpenAI embedding generation + persist for non-spam emails.

Official API (https://platform.openai.com/docs/guides/embeddings,
https://platform.openai.com/docs/api-reference/embeddings/create):

    await client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
        encoding_format="float",
        dimensions=1536,  # optional; default for text-embedding-3-small is 1536
    )

Token budgeting uses tiktoken ``cl100k_base`` (OpenAI embedding cookbook:
EMBEDDING_CTX_LENGTH = 8191). Failures are isolated — callers use
``embed_and_store_safe`` so ingest never crashes.
"""

from __future__ import annotations

import structlog
import tiktoken
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.email_clean import CLEAN_VERSION, effective_body_text
from app.llm.pii_redact import scrub_email_for_llm, scrub_text
from app.models.schemas.email import EmailMessageSchema
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.repositories import embedding_repo

logger = structlog.get_logger(__name__)

_PREVIEW_CHARS = 500
_ENCODING: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def _truncate_to_token_budget(text: str, *, max_tokens: int) -> str:
    encoding = _get_encoding()
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return encoding.decode(tokens[:max_tokens])


def build_search_document(email: EmailMessageSchema) -> str:
    """Metadata-prefixed cleaned body used for embed + FTS (unsrubbed for FTS store)."""
    body = effective_body_text(
        body_clean=email.body_clean,
        body_text=email.body_text,
        body_content_type=email.body_content_type,
    )
    to_list = ", ".join(email.to_recipients) if email.to_recipients else ""
    cc_list = ", ".join(email.cc_recipients) if email.cc_recipients else ""
    subject = (email.subject or "").strip()
    return (
        f"From: {email.sender} | To: {to_list} | CC: {cc_list} | Subject: {subject}\n\n{body}"
    ).strip()


def _build_embed_text(email: EmailMessageSchema, *, max_tokens: int) -> str:
    """Scrubbed metadata + cleaned body, truncated by token budget.

    The metadata prefix is never truncated — only the body portion is cut so
    sender/recipient/subject always survive.
    """
    scrubbed = scrub_email_for_llm(email)
    body = effective_body_text(
        body_clean=scrubbed.body_clean,
        body_text=scrubbed.body_text,
        body_content_type=scrubbed.body_content_type,
    )
    to_list = ", ".join(scrubbed.to_recipients) if scrubbed.to_recipients else ""
    cc_list = ", ".join(scrubbed.cc_recipients) if scrubbed.cc_recipients else ""
    subject = (scrubbed.subject or "").strip()
    prefix = f"From: {scrubbed.sender} | To: {to_list} | CC: {cc_list} | Subject: {subject}\n\n"
    encoding = _get_encoding()
    prefix_tokens = len(encoding.encode(prefix))
    body_budget = max(1, max_tokens - prefix_tokens)
    body_truncated = _truncate_to_token_budget(body, max_tokens=body_budget)
    return f"{prefix}{body_truncated}".strip()


def _body_preview_for_store(email: EmailMessageSchema) -> str:
    preview = (
        email.body_preview
        or effective_body_text(
            body_clean=email.body_clean,
            body_text=email.body_text,
            body_content_type=email.body_content_type,
        )
        or email.subject
        or ""
    ).strip()
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
    text = _build_embed_text(email, max_tokens=settings.embedding_max_input_tokens)
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
    search_document: str | None = None,
    embed_clean_version: int | None = None,
) -> EmbeddingMatchSchema:
    """Persist embedding + participant metadata via the repository."""
    doc = search_document if search_document is not None else build_search_document(email)
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
        search_document=doc,
        embed_clean_version=(
            embed_clean_version if embed_clean_version is not None else CLEAN_VERSION
        ),
    )


async def embed_text(
    text: str,
    *,
    client: AsyncOpenAI,
    settings: Settings,
) -> list[float]:
    """Embed arbitrary text (scrubbed by caller). Used for reply memory RAG."""
    cleaned = scrub_text((text or "").strip())
    if not cleaned:
        raise ValueError("embed_text requires non-empty text")
    cleaned = _truncate_to_token_budget(
        cleaned,
        max_tokens=settings.embedding_max_input_tokens,
    )

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
