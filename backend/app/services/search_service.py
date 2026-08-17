"""User-facing thread search over draft-pipeline memory.

``keyword`` (the HTTP default) is Outlook-style full-text search.
``hybrid`` adds pgvector + RRF and is what chat/InboxAssistant uses for retrieval.
Snippets and thread ids are citation raw material; untrusted-content delimiters
belong in the chat prompt layer, not this API.

Read-only: no Graph calls, no mail writes. Results are always restricted to
``settings.mailbox_list``.
"""

from __future__ import annotations

import re
from uuid import UUID

import structlog
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import EmptySearchQueryError, SearchError, UnknownMailboxError
from app.core.mailbox_keys import resolve_mailbox_email
from app.llm.email_clean import clean_email_body
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.models.schemas.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    SEARCH_MODE_HYBRID,
    SEARCH_MODE_KEYWORD,
    SEARCH_SNIPPET_MAX_CHARS,
    SearchHit,
    SearchResponse,
)
from app.repositories import embedding_repo, thread_repo
from app.services import embedding_service
from app.services.rrf import (
    aggregate_conversation_scores,
    best_hit_for_conversation,
    rrf_fuse,
)

logger = structlog.get_logger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCOPE_SEP = "\x1f"
_PROPER_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_NAME_FILLER = frozenset(
    {
        "what",
        "whats",
        "happening",
        "happen",
        "give",
        "latest",
        "tell",
        "show",
        "about",
        "status",
        "update",
        "news",
        "please",
        "could",
        "would",
        "can",
        "you",
        "me",
        "the",
        "on",
        "with",
        "for",
        "any",
        "going",
        "something",
    }
)


def _trim_filler_name_parts(span: str) -> str | None:
    parts = span.split()
    while parts and parts[0].lower().strip("'") in _NAME_FILLER:
        parts = parts[1:]
    while parts and parts[-1].lower().strip("'") in _NAME_FILLER:
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    return " ".join(parts)


def build_fts_query(query: str) -> str:
    """Quote proper names so chatty asks do not AND filler tokens in FTS.

    ``websearch_to_tsquery('english', "What's happening on Ashley Cantrell…")``
    becomes ``happen & ashley & cantrel & give & latest`` and misses mail that
    only names the person. Quoted names keep the lookup.
    """
    cleaned = " ".join((query or "").split())
    if not cleaned:
        return cleaned
    names: list[str] = []
    seen: set[str] = set()
    for span in _PROPER_NAME_RE.findall(cleaned):
        name = _trim_filler_name_parts(span)
        if name is None:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    if not names:
        return cleaned
    return " OR ".join(f'"{name}"' for name in names)


def build_snippet(*, subject: str | None, body: str | None) -> str:
    """Subject + cleaned body fragment, truncated to ``SEARCH_SNIPPET_MAX_CHARS``."""
    raw_body = body or ""
    content_type = "html" if "<" in raw_body else "text"
    cleaned_body = clean_email_body(raw_body, content_type=content_type).body_clean
    cleaned_body = _HTML_TAG_RE.sub(" ", cleaned_body)
    cleaned_body = " ".join(cleaned_body.split())
    subject_part = " ".join((subject or "").split())
    if subject_part and cleaned_body:
        text = f"{subject_part} — {cleaned_body}"
    else:
        text = subject_part or cleaned_body
    if len(text) <= SEARCH_SNIPPET_MAX_CHARS:
        return text
    ellipsis = "..."
    budget = SEARCH_SNIPPET_MAX_CHARS - len(ellipsis)
    return text[:budget].rstrip() + ellipsis


def _scoped_mailboxes(settings: Settings, mailbox: str | None) -> list[str]:
    allowed = list(settings.mailbox_list)
    if mailbox is None or not mailbox.strip():
        return allowed
    email = resolve_mailbox_email(mailbox.strip(), allowed)
    if email is None or not settings.mailbox_allowed(email):
        raise UnknownMailboxError("Mailbox not found")
    return [email]


def _normalize_limit(limit: int) -> int:
    if limit < 1:
        return SEARCH_DEFAULT_LIMIT
    return min(limit, SEARCH_MAX_LIMIT)


def _scope_key(mailbox: str, conversation_id: str) -> str:
    return f"{mailbox}{_SCOPE_SEP}{conversation_id}"


def _split_scope_key(key: str) -> tuple[str, str]:
    mailbox, conversation_id = key.split(_SCOPE_SEP, 1)
    return mailbox, conversation_id


def _fuse_tuples(
    matches: list[EmbeddingMatchSchema],
) -> list[tuple[UUID, str, UUID | None]]:
    tuples: list[tuple[UUID, str, UUID | None]] = []
    for match in matches:
        if not match.mailbox:
            continue
        tuples.append(
            (match.id, _scope_key(match.mailbox, match.conversation_id), match.message_id)
        )
    return tuples


async def search_threads(
    session: AsyncSession,
    settings: Settings,
    *,
    openai_client: AsyncOpenAI | None,
    query: str,
    mailbox: str | None = None,
    limit: int = SEARCH_DEFAULT_LIMIT,
    mode: str = SEARCH_MODE_HYBRID,
) -> SearchResponse:
    """Ranked threads. ``keyword`` is FTS only; ``hybrid`` adds embeddings. Blank query is a 422."""
    cleaned = (query or "").strip()
    if not cleaned:
        raise EmptySearchQueryError("query must not be blank")
    if mode not in (SEARCH_MODE_KEYWORD, SEARCH_MODE_HYBRID):
        mode = SEARCH_MODE_HYBRID

    mailboxes = _scoped_mailboxes(settings, mailbox)
    resolved = mailboxes[0] if mailbox is not None and mailbox.strip() else None
    limit = _normalize_limit(limit)

    if not mailboxes:
        logger.info("search_empty_mailbox_allowlist", query=cleaned)
        return SearchResponse(query=cleaned, mailbox=resolved, hits=[])

    vector_matches: list[EmbeddingMatchSchema] = []
    fts_matches: list[EmbeddingMatchSchema] = []
    vector_failed = False
    fts_failed = False
    top_k = max(settings.embedding_candidate_k, limit)
    use_vector = mode == SEARCH_MODE_HYBRID

    vector: list[float] | None = None
    if use_vector:
        if openai_client is None:
            logger.warning("search_embed_skipped_unconfigured", query_length=len(cleaned))
        else:
            try:
                vector = await embedding_service.embed_text(
                    cleaned,
                    client=openai_client,
                    settings=settings,
                )
            except Exception:
                logger.exception("search_embed_failed", query_length=len(cleaned))

        if vector is not None:
            try:
                vector_matches = await embedding_repo.search_similar(
                    session,
                    embedding=vector,
                    min_similarity=settings.embedding_min_similarity,
                    top_k=top_k,
                    mailboxes=mailboxes,
                )
            except Exception:
                vector_failed = True
                logger.exception("search_vector_failed", query_length=len(cleaned))

    try:
        fts_matches = await embedding_repo.search_fts(
            session,
            query_text=build_fts_query(cleaned),
            top_k=top_k,
            mailboxes=mailboxes,
        )
    except Exception:
        fts_failed = True
        logger.exception("search_fts_failed", query_length=len(cleaned))

    if not vector_matches and not fts_matches:
        if vector_failed or fts_failed:
            raise SearchError("Search is temporarily unavailable")
        return SearchResponse(query=cleaned, mailbox=resolved, hits=[])

    fused = rrf_fuse(
        _fuse_tuples(vector_matches),
        _fuse_tuples(fts_matches),
        k=settings.rrf_k,
    )
    conv_scores = aggregate_conversation_scores(
        fused,
        bonus=settings.embedding_corroboration_bonus,
        max_bonus_hits=settings.embedding_corroboration_max_hits,
    )
    ranked = sorted(conv_scores.items(), key=lambda kv: kv[1], reverse=True)
    pairs = [_split_scope_key(key) for key, _score in ranked]
    threads = await thread_repo.list_by_mailbox_conversations(session, pairs)
    by_embedding = {match.id: match for match in [*vector_matches, *fts_matches]}

    hits: list[SearchHit] = []
    for key, score in ranked:
        mailbox_email, conversation_id = _split_scope_key(key)
        thread = threads.get((mailbox_email, conversation_id))
        if thread is None:
            continue
        top_hit = best_hit_for_conversation(fused, key)
        preview = None
        if top_hit is not None:
            match = by_embedding.get(top_hit.embedding_id)
            if match is not None:
                preview = match.body_preview
        hits.append(
            SearchHit(
                thread_id=thread.id,
                mailbox=thread.mailbox,
                conversation_id=thread.conversation_id,
                subject=thread.subject or None,
                state=thread.state,
                urgency=thread.urgency,
                snippet=build_snippet(subject=thread.subject, body=preview),
                score=score,
                last_message_at=thread.last_message_at,
            )
        )
        if len(hits) >= limit:
            break

    logger.info(
        "search_completed",
        query_length=len(cleaned),
        mailbox=resolved,
        mode=mode,
        hit_count=len(hits),
        vector_count=len(vector_matches),
        fts_count=len(fts_matches),
    )
    return SearchResponse(query=cleaned, mailbox=resolved, hits=hits)
