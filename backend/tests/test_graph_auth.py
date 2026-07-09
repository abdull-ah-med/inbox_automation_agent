"""Unit tests for GraphAuth MSAL client credentials flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.graph.auth import GRAPH_SCOPES, GraphAuth


def _settings() -> Settings:
    return Settings(
        graph_client_id="client-id",
        graph_client_secret="client-secret",
        graph_tenant_id="tenant-id",
    )


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
    mock_app.acquire_token_silent.assert_called_once_with(GRAPH_SCOPES, account=None)
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
    mock_app.acquire_token_for_client.assert_called_once_with(scopes=GRAPH_SCOPES)


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
