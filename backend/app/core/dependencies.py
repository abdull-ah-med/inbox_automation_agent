from collections.abc import AsyncGenerator
from typing import Annotated
from urllib.parse import urlparse

import structlog
from anthropic import AsyncAnthropic
from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient

logger = structlog.get_logger(__name__)

_redis_client: Redis | None = None
_graph_auth: GraphAuth | None = None
_graph_client: GraphClient | None = None
_anthropic_client: AsyncAnthropic | None = None


async def get_redis() -> Redis:
    """Process-scoped Redis client with an explicit connection pool.

    Per redis-py docs (https://redis.readthedocs.io/en/latest/connections.html):
    use ``from_url`` with ``max_connections``, connect/read timeouts, and
    ``rediss://`` for TLS. Never open a new TCP connection per request.
    """
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        # Avoid logging credentials — only scheme/host/db.
        parsed = urlparse(settings.redis_url)
        logger.info(
            "redis_connecting",
            scheme=parsed.scheme,
            host=parsed.hostname,
            db=(parsed.path or "/0").lstrip("/") or "0",
            tls=settings.redis_uses_tls,
            max_connections=settings.redis_max_connections,
        )
        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_graph_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> GraphAuth:
    global _graph_auth
    if _graph_auth is None:
        _graph_auth = GraphAuth(settings, redis=redis)
    return _graph_auth


def get_graph_client(
    auth: Annotated[GraphAuth, Depends(get_graph_auth)],
) -> GraphClient:
    """Return the process-scoped GraphClient (persistent httpx pool)."""
    global _graph_client
    if _graph_client is None:
        _graph_client = GraphClient(auth)
    return _graph_client


async def close_graph_client() -> None:
    global _graph_client, _graph_auth
    if _graph_client is not None:
        await _graph_client.aclose()
        _graph_client = None
    _graph_auth = None


def get_anthropic_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncAnthropic:
    """Return a process-scoped AsyncAnthropic client (not created at import time)."""
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    return _anthropic_client


def anthropic_client_from_settings(settings: Settings) -> AsyncAnthropic:
    """Resolve Anthropic client outside FastAPI request DI (webhook/poll workers)."""
    return get_anthropic_client(settings)


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]
GraphAuthDep = Annotated[GraphAuth, Depends(get_graph_auth)]
GraphClientDep = Annotated[GraphClient, Depends(get_graph_client)]
AnthropicClientDep = Annotated[AsyncAnthropic, Depends(get_anthropic_client)]
