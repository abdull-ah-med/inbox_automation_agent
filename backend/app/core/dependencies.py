from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient

_redis_client: Redis | None = None
_graph_auth: GraphAuth | None = None


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


def get_graph_auth(settings: Annotated[Settings, Depends(get_settings)]) -> GraphAuth:
    global _graph_auth
    if _graph_auth is None:
        _graph_auth = GraphAuth(settings)
    return _graph_auth


def get_graph_client(
    auth: Annotated[GraphAuth, Depends(get_graph_auth)],
) -> GraphClient:
    return GraphClient(auth)


# TODO: Wire Slack AsyncApp when SLACK_BOT_TOKEN is available.

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]
GraphAuthDep = Annotated[GraphAuth, Depends(get_graph_auth)]
GraphClientDep = Annotated[GraphClient, Depends(get_graph_client)]
