from collections.abc import AsyncGenerator
from typing import Annotated
from urllib.parse import urlparse

import structlog
from anthropic import AsyncAnthropic
from fastapi import Depends
from redis.asyncio import Redis
from slack_bolt.async_app import AsyncApp
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
_slack_app: AsyncApp | None = None
_slack_app_resolved: bool = False


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


def get_slack_app(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncApp | None:
    """Return process-scoped Slack Bolt ``AsyncApp``, or ``None`` if unconfigured.

    Per Slack Bolt async docs
    (https://docs.slack.dev/tools/bolt-python/concepts/async):
    ``AsyncApp(token=..., signing_secret=...)``. Missing token or signing secret
    → warn once and skip construction so local/offline runs never crash.
    """
    global _slack_app, _slack_app_resolved
    if _slack_app_resolved:
        return _slack_app

    token = settings.slack_bot_token.strip()
    signing_secret = settings.slack_signing_secret.strip()
    if not token or not signing_secret:
        logger.warning(
            "slack_unconfigured",
            hint="Set SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET — review cards skipped",
        )
        _slack_app = None
        _slack_app_resolved = True
        return None

    _slack_app = AsyncApp(token=token, signing_secret=signing_secret)
    _slack_app_resolved = True
    logger.info("slack_app_initialized")
    return _slack_app


def slack_app_from_settings(settings: Settings) -> AsyncApp | None:
    """Resolve Slack app outside FastAPI request DI (pipeline / workers)."""
    return get_slack_app(settings)


async def close_slack_app() -> None:
    """Drop the process-scoped Slack app; close aiohttp session if one was opened.

    ``AsyncWebClient`` has no public ``close()``; when a session was created it is
    exposed as ``client.session`` (aiohttp ``ClientSession``) — close that on shutdown.
    """
    global _slack_app, _slack_app_resolved
    if _slack_app is not None:
        session = getattr(_slack_app.client, "session", None)
        if session is not None and not session.closed:
            await session.close()
    _slack_app = None
    _slack_app_resolved = False


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]
GraphAuthDep = Annotated[GraphAuth, Depends(get_graph_auth)]
GraphClientDep = Annotated[GraphClient, Depends(get_graph_client)]
AnthropicClientDep = Annotated[AsyncAnthropic, Depends(get_anthropic_client)]
SlackAppDep = Annotated[AsyncApp | None, Depends(get_slack_app)]
