"""Unit tests for GraphAuth MSAL client credentials flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.core.redis_keys import MSAL_TOKEN_CACHE_KEY
from app.graph.auth import GRAPH_SCOPES, GraphAuth


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "graph_client_id": "client-id",
        "graph_client_secret": "client-secret",
        "graph_tenant_id": "tenant-id",
        # Keep unit tests deterministic even if backend/.env has a Fernet key.
        "msal_cache_encryption_key": "",
        "environment": "local",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_graph_scopes_are_list_with_default_scope() -> None:
    assert GRAPH_SCOPES == ["https://graph.microsoft.com/.default"]
    assert isinstance(GRAPH_SCOPES, list)


def test_missing_credentials_raise_graph_client_error() -> None:
    with pytest.raises(GraphClientError, match="GRAPH_CLIENT_ID"):
        GraphAuth(Settings(graph_client_id="", graph_tenant_id="", graph_client_secret=""))


@pytest.mark.asyncio
async def test_get_access_token_uses_silent_cache_first() -> None:
    settings = _settings()
    mock_app = MagicMock()
    mock_app.acquire_token_silent.return_value = {
        "access_token": "cached-token",
        "expires_in": 3600,
    }

    with patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app):
        auth = GraphAuth(settings)
        token = await auth.get_access_token()

    assert token == "cached-token"
    mock_app.acquire_token_for_client.assert_not_called()


@pytest.mark.asyncio
async def test_get_access_token_falls_back_to_client_credentials() -> None:
    settings = _settings()
    mock_app = MagicMock()
    mock_app.acquire_token_silent.return_value = None
    mock_app.acquire_token_for_client.return_value = {
        "access_token": "fresh-token",
        "expires_in": 3599,
    }

    with patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app):
        auth = GraphAuth(settings)
        token = await auth.get_access_token()

    assert token == "fresh-token"


@pytest.mark.asyncio
async def test_get_access_token_raises_on_msal_error() -> None:
    settings = _settings()
    mock_app = MagicMock()
    mock_app.acquire_token_silent.return_value = None
    mock_app.acquire_token_for_client.return_value = {
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret",
    }

    with patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app):
        auth = GraphAuth(settings)
        with pytest.raises(GraphClientError, match="invalid_client"):
            await auth.get_access_token()


@pytest.mark.asyncio
async def test_token_cache_reloaded_from_redis_on_every_acquire() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"AccessToken":{}}')
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)

    mock_cache = MagicMock()
    mock_cache.has_state_changed = False
    mock_cache.serialize.return_value = "{}"
    mock_cache.deserialize = MagicMock()

    mock_app = MagicMock()
    mock_app.acquire_token_silent.return_value = {
        "access_token": "cached-token",
        "expires_in": 3600,
    }

    with (
        patch("app.graph.auth.msal.SerializableTokenCache", return_value=mock_cache),
        patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app),
    ):
        auth = GraphAuth(settings, redis=redis)
        assert not hasattr(auth, "_cache_loaded")
        await auth.get_access_token()
        await auth.get_access_token()

    assert redis.get.await_count == 2
    assert mock_cache.deserialize.call_count == 2
    # Lock acquire (SET NX) once per get_access_token; release via Lua eval
    assert redis.set.await_count == 2
    assert redis.eval.await_count == 2


@pytest.mark.asyncio
async def test_token_cache_loaded_and_persisted_via_redis() -> None:
    from app.core.redis_keys import MSAL_TOKEN_CACHE_LOCK_KEY

    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"AccessToken":{}}')
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)

    mock_cache = MagicMock()
    mock_cache.has_state_changed = True
    mock_cache.serialize.return_value = '{"AccessToken":{"t":1}}'
    mock_cache.deserialize = MagicMock()

    mock_app = MagicMock()
    mock_app.acquire_token_silent.return_value = None
    mock_app.acquire_token_for_client.return_value = {
        "access_token": "fresh-token",
        "expires_in": 3600,
    }

    with (
        patch("app.graph.auth.msal.SerializableTokenCache", return_value=mock_cache),
        patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app),
    ):
        auth = GraphAuth(settings, redis=redis)
        token = await auth.get_access_token()

    assert token == "fresh-token"
    redis.get.assert_awaited_once_with(MSAL_TOKEN_CACHE_KEY)
    mock_cache.deserialize.assert_called_once_with('{"AccessToken":{}}')
    # First SET is the NX lock; second persists the cache blob.
    assert redis.set.await_count == 2
    assert redis.set.await_args_list[0].args[0] == MSAL_TOKEN_CACHE_LOCK_KEY
    assert isinstance(redis.set.await_args_list[0].args[1], str)
    assert redis.set.await_args_list[0].kwargs.get("nx") is True
    assert redis.set.await_args_list[1].args == (
        MSAL_TOKEN_CACHE_KEY,
        '{"AccessToken":{"t":1}}',
    )
    redis.eval.assert_awaited_once()
    assert redis.eval.await_args.args[2] == MSAL_TOKEN_CACHE_LOCK_KEY


@pytest.mark.asyncio
async def test_token_cache_not_persisted_when_unchanged() -> None:
    from app.core.redis_keys import MSAL_TOKEN_CACHE_LOCK_KEY

    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)

    mock_cache = MagicMock()
    mock_cache.has_state_changed = False
    mock_cache.serialize.return_value = "{}"
    mock_cache.deserialize = MagicMock()

    mock_app = MagicMock()
    mock_app.acquire_token_silent.return_value = {
        "access_token": "cached-token",
        "expires_in": 3600,
    }

    with (
        patch("app.graph.auth.msal.SerializableTokenCache", return_value=mock_cache),
        patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app),
    ):
        auth = GraphAuth(settings, redis=redis)
        token = await auth.get_access_token()

    assert token == "cached-token"
    redis.get.assert_awaited_once_with(MSAL_TOKEN_CACHE_KEY)
    # Only the lock SET — no cache persist.
    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[0] == MSAL_TOKEN_CACHE_LOCK_KEY
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_token_cache_lock_timeout_raises_without_persist() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=False)  # never acquires NX lock
    redis.eval = AsyncMock(return_value=0)

    mock_app = MagicMock()

    with (
        patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app),
        patch("app.graph.auth._LOCK_RETRY_ATTEMPTS", 2),
        patch("app.graph.auth._LOCK_RETRY_DELAY_SECONDS", 0),
    ):
        auth = GraphAuth(settings, redis=redis)
        with pytest.raises(GraphClientError, match="token cache lock"):
            await auth.get_access_token()

    redis.get.assert_not_awaited()
    mock_app.acquire_token_silent.assert_not_called()
    mock_app.acquire_token_for_client.assert_not_called()


@pytest.mark.asyncio
async def test_missing_client_secret_raises() -> None:
    with pytest.raises(GraphClientError, match="GRAPH_CLIENT_SECRET"):
        GraphAuth(
            Settings(
                graph_client_id="client-id",
                graph_tenant_id="tenant-id",
                graph_client_secret="",
            )
        )
