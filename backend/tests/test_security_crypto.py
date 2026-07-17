"""Tests for MSAL cache encryption helpers and GraphAuth Redis persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.security import decrypt_cache_blob, encrypt_cache_blob
from app.graph.auth import GraphAuth


def test_fernet_roundtrip() -> None:
    key = Fernet.generate_key().decode()
    blob = encrypt_cache_blob('{"AccessToken":{}}', key)
    assert blob != '{"AccessToken":{}}'
    assert decrypt_cache_blob(blob, key) == '{"AccessToken":{}}'


@pytest.mark.asyncio
async def test_graph_auth_persists_encrypted_cache() -> None:
    key = Fernet.generate_key().decode()
    settings = Settings(
        environment="local",
        graph_client_id="id",
        graph_client_secret="secret",
        graph_tenant_id="tenant",
        msal_cache_encryption_key=key,
    )
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    mock_app = MagicMock()

    with patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app):
        auth = GraphAuth(settings, redis=redis)
        auth._cache.has_state_changed = True  # type: ignore[attr-defined]
        with patch.object(auth._cache, "serialize", return_value='{"tok":1}'):
            await auth._persist_cache_if_changed()

    redis.set.assert_awaited_once()
    stored = redis.set.await_args.args[1]
    assert decrypt_cache_blob(stored, key) == '{"tok":1}'


@pytest.mark.asyncio
async def test_graph_auth_loads_plaintext_fallback_then_works() -> None:
    key = Fernet.generate_key().decode()
    settings = Settings(
        environment="local",
        graph_client_id="id",
        graph_client_secret="secret",
        graph_tenant_id="tenant",
        msal_cache_encryption_key=key,
    )
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"legacy":true}')
    mock_app = MagicMock()

    with patch("app.graph.auth.msal.ConfidentialClientApplication", return_value=mock_app):
        auth = GraphAuth(settings, redis=redis)
        with patch.object(auth._cache, "deserialize") as deser:
            await auth._load_cache_from_redis()
    deser.assert_called_once_with('{"legacy":true}')
