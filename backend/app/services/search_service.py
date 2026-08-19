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
from app.core.sanitize import sanitize_user_text
from app.llm.email_clean import clean_email_body
from app.models.schemas.embedding import EmbeddingMatchSchema
from app.models.schemas.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    SEARCH_MODE_HYBRID,
    SEARCH_MODE_KEYWORD,
    SEARCH_SNIPPET_MAX_CHARS,
    SearchColumnFilters,
    SearchHit,
    SearchResponse,
)
from app.repositories import embedding_repo, thread_repo
from app.services import embedding_service
from app.services.nl_mailbox_scope import extract_nl_mailbox_scope
from app.services.rrf import (
    aggregate_conversation_scores,
    best_hit_for_conversation,
    rrf_fuse,
)
from app.services.search_query import (
    ParsedSearchQuery,
    format_search_query,
    parse_search_query,
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
        "from",
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
    only names the person. Quoted names keep the lookup. Non-filler keywords
    beside the name (``Ashley Cantrell invoice``) are kept.
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
    remainder = cleaned
    for name in names:
        remainder = re.sub(re.escape(name), " ", remainder, count=1, flags=re.IGNORECASE)
    kept: list[str] = []
    seen_tokens: set[str] = set()
    for token in _FTS_WORD_RE.findall(remainder):
        lowered = token.lower()
        if len(lowered) < 2 or lowered in _NAME_FILLER:
            continue
        if lowered in seen_tokens:
            continue
        seen_tokens.add(lowered)
        kept.append(token)
    quoted = " OR ".join(f'"{name}"' for name in names)
    if not kept:
        return quoted
    return f"{quoted} {' '.join(kept)}"


_FTS_WORD_RE = re.compile(r"\w+", re.UNICODE)
_FTS_FILLER = _NAME_FILLER | frozenset(
    {
        "i",
        "im",
        "ive",
        "id",
        "ill",
        "we",
        "our",
        "ours",
        "your",
        "yours",
        "they",
        "them",
        "their",
        "this",
        "that",
        "these",
        "those",
        "here",
        "there",
        "then",
        "than",
        "too",
        "also",
        "just",
        "still",
        "really",
        "very",
        "how",
        "why",
        "when",
        "where",
        "who",
        "which",
        "does",
        "did",
        "doing",
        "done",
        "do",
        "have",
        "has",
        "had",
        "was",
        "were",
        "been",
        "being",
        "am",
        "is",
        "are",
        "not",
        "no",
        "yes",
        "first",
        "last",
        "next",
        "now",
        "today",
        "need",
        "needs",
        "needed",
        "focus",
        "focusing",
        "to",
        "of",
        "in",
        "at",
        "as",
        "or",
        "an",
        "a",
        "it",
        "its",
        "so",
        "if",
        "but",
        "get",
        "got",
        "make",
        "want",
        "should",
        "must",
        "will",
        "whats",
        "and",
        "thread",
        "threads",
        "email",
        "emails",
        "mail",
        "message",
        "messages",
        "mailbox",
        "inbox",
        "folder",
        "summarize",
        "summary",
        "overview",
        "recap",
        "anything",
        "everything",
        "hello",
        "hey",
        "hi",
        "thanks",
        "thank",
        "more",
        "else",
        "again",
        "additional",
        "detail",
        "details",
    }
)


def build_prefix_tsquery(query: str) -> str:
    """Outlook-style prefix match: SampleH hits SampleHelpdesk.

    Chatty asks AND every token unless filler is dropped — ``give me the latest
    on info`` must not require the document to contain give/latest/happening.
    Tokens are ``\\w+`` only so operators cannot break the query.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _FTS_WORD_RE.findall(query or ""):
        lowered = token.lower()
        if len(lowered) < 2 or lowered in _FTS_FILLER:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        tokens.append(token)
    if not tokens:
        return ""
    return " & ".join(f"{token}:*" for token in tokens)


def build_snippet(*, body: str | None, highlight: str | None = None) -> str:
    """Cleaned body window, truncated to ``SEARCH_SNIPPET_MAX_CHARS``.

    Subject is shown separately in the UI, so it is not prefixed here. When
    ``highlight`` is set, the window centers on the first matching term.
    """
    raw_body = body or ""
    content_type = "html" if "<" in raw_body else "text"
    cleaned_body = clean_email_body(raw_body, content_type=content_type).body_clean
    cleaned_body = _HTML_TAG_RE.sub(" ", cleaned_body)
    cleaned_body = " ".join(cleaned_body.split())
    if not cleaned_body:
        return ""

    text = cleaned_body
    highlight_terms = [
        term
        for term in _FTS_WORD_RE.findall(highlight or "")
        if len(term) >= 2 and term.lower() not in _FTS_FILLER
    ]
    if highlight_terms:
        lower_body = cleaned_body.lower()
        match_at: int | None = None
        match_len = 0
        for term in highlight_terms:
            idx = lower_body.find(term.lower())
            if idx < 0:
                continue
            if match_at is None or idx < match_at:
                match_at = idx
                match_len = len(term)
        if match_at is not None:
            # Prefer a window that keeps the match near the middle.
            budget = SEARCH_SNIPPET_MAX_CHARS
            if len(cleaned_body) > budget:
                half = max(0, (budget - match_len) // 2)
                start = max(0, match_at - half)
                end = min(len(cleaned_body), start + budget)
                if end - start < budget:
                    start = max(0, end - budget)
                text = cleaned_body[start:end].strip()
                if start > 0:
                    text = "..." + text.lstrip()
                if end < len(cleaned_body):
                    text = text.rstrip() + "..."
                return text

    if len(text) <= SEARCH_SNIPPET_MAX_CHARS:
        return text
    ellipsis = "..."
    budget = SEARCH_SNIPPET_MAX_CHARS - len(ellipsis)
    return text[:budget].rstrip() + ellipsis


def _scoped_mailboxes(settings: Settings, mailbox: str | None) -> list[str]:
    allowed = list(settings.mailbox_list)
    if mailbox is None or not mailbox.strip():
        return allowed
    email = resolve_mailbox_email(sanitize_user_text(mailbox.strip()), allowed)
    if email is None or not settings.mailbox_allowed(email):
        raise UnknownMailboxError("Mailbox not found")
    return [email]


def _mailboxes_for_search(
    settings: Settings,
    *,
    mailbox: str | None,
    mailbox_tokens: tuple[str, ...],
) -> list[str]:
    scoped = _scoped_mailboxes(settings, mailbox)
    if not mailbox_tokens:
        return scoped
    wanted: list[str] = []
    for token in mailbox_tokens:
        email = resolve_mailbox_email(sanitize_user_text(token), list(settings.mailbox_list))
        if email is None or not settings.mailbox_allowed(email):
            raise UnknownMailboxError("Mailbox not found")
        if email not in wanted:
            wanted.append(email)
    return [item for item in scoped if item in set(wanted)]


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
    allow_empty: bool = False,
) -> SearchResponse:
    """Ranked threads. ``keyword`` is FTS only; ``hybrid`` adds embeddings.

    Blank / filler-only queries raise unless ``allow_empty`` (chat overview).
    """
    parsed = parse_search_query(query)
    nl = extract_nl_mailbox_scope(parsed.free_text, list(settings.mailbox_list))
    if nl.mailboxes:
        merged = parsed.mailboxes + tuple(
            token for token in nl.mailboxes if token not in parsed.mailboxes
        )
        parsed = ParsedSearchQuery(
            free_text=nl.free_text,
            senders=parsed.senders,
            contains=parsed.contains,
            subjects=parsed.subjects,
            directions=parsed.directions,
            mailboxes=merged,
        )
    # Chatty leftover ("what should I focus on") must not be embedded or AND-ed.
    if parsed.free_text and not build_prefix_tsquery(build_fts_query(parsed.free_text)):
        parsed = ParsedSearchQuery(
            free_text="",
            senders=parsed.senders,
            contains=parsed.contains,
            subjects=parsed.subjects,
            directions=parsed.directions,
            mailboxes=parsed.mailboxes,
        )
    if parsed.is_empty() and not allow_empty:
        raise EmptySearchQueryError("query must not be blank")
    cleaned = format_search_query(parsed)
    if mode not in (SEARCH_MODE_KEYWORD, SEARCH_MODE_HYBRID):
        mode = SEARCH_MODE_HYBRID

    mailboxes = _mailboxes_for_search(
        settings,
        mailbox=mailbox,
        mailbox_tokens=parsed.mailboxes,
    )
    resolved = mailboxes[0] if mailbox is not None and mailbox.strip() else None
    if parsed.mailboxes and len(mailboxes) == 1:
        resolved = mailboxes[0]
    limit = _normalize_limit(limit)
    column_filters = SearchColumnFilters(
        senders=parsed.senders,
        contains=parsed.contains,
        subjects=parsed.subjects,
        directions=parsed.directions,
    )
    filters = column_filters if column_filters.active() else None

    if not mailboxes:
        logger.info("search_empty_mailbox_allowlist", query=cleaned)
        return SearchResponse(query=cleaned, mailbox=resolved, hits=[])

    vector_matches: list[EmbeddingMatchSchema] = []
    fts_matches: list[EmbeddingMatchSchema] = []
    vector_failed = False
    fts_failed = False
    top_k = max(settings.embedding_candidate_k, limit)
    use_vector = mode == SEARCH_MODE_HYBRID and bool(parsed.free_text)

    vector: list[float] | None = None
    if use_vector:
        if openai_client is None:
            logger.warning("search_embed_skipped_unconfigured", query_length=len(cleaned))
        else:
            try:
                vector = await embedding_service.embed_text(
                    parsed.free_text,
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
                    filters=filters,
                )
            except Exception:
                vector_failed = True
                logger.exception("search_vector_failed", query_length=len(cleaned))

    try:
        prefix_query = (
            build_prefix_tsquery(build_fts_query(parsed.free_text))
            if parsed.free_text
            else ""
        )
        # mailbox: or a filler-only chat overview: empty FTS + recency listing.
        recency_listing = (
            not parsed.free_text
            and filters is None
            and (bool(parsed.mailboxes) or allow_empty)
        )
        fts_matches = await embedding_repo.search_fts(
            session,
            query_text=prefix_query,
            top_k=top_k,
            mailboxes=mailboxes,
            use_prefix=True,
            filters=filters,
            allow_empty=recency_listing,
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
    vector_ids = {match.id for match in vector_matches}

    hits: list[SearchHit] = []
    for key, score in ranked:
        mailbox_email, conversation_id = _split_scope_key(key)
        thread = threads.get((mailbox_email, conversation_id))
        if thread is None:
            continue
        top_hit = best_hit_for_conversation(fused, key)
        preview = None
        cosine: float | None = None
        if top_hit is not None:
            match = by_embedding.get(top_hit.embedding_id)
            if match is not None:
                preview = match.body_preview
                if top_hit.embedding_id in vector_ids:
                    cosine = match.similarity_score
        highlight_parts = [*parsed.contains, *parsed.subjects]
        if parsed.free_text:
            highlight_parts.append(parsed.free_text)
        hits.append(
            SearchHit(
                thread_id=thread.id,
                mailbox=thread.mailbox,
                conversation_id=thread.conversation_id,
                subject=thread.subject or None,
                state=thread.state,
                urgency=thread.urgency,
                snippet=build_snippet(
                    body=preview,
                    highlight=" ".join(highlight_parts) or None,
                ),
                score=score,
                last_message_at=thread.last_message_at,
                similarity_score=cosine,
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
