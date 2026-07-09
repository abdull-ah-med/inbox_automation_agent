"""Pytest configuration and shared fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models.schemas.graph import GraphMessageSchema


@pytest.fixture
def settings() -> Settings:
    return Settings(
        graph_client_id="test-client-id",
        graph_client_secret="test-client-secret",
        graph_tenant_id="test-tenant-id",
        graph_webhook_client_state="test-client-state",
        target_mailboxes="client@example.com",
        environment="local",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.fixture
def sample_graph_message() -> GraphMessageSchema:
    return GraphMessageSchema.model_validate(
        {
            "id": "AAMkAGMessageId",
            "subject": "Drug screen result",
            "bodyPreview": "Positive result for client",
            "body": {"contentType": "text", "content": "Positive result for client"},
            "sender": {
                "emailAddress": {"name": "Vendor", "address": "vendor@example.com"}
            },
            "from": {
                "emailAddress": {"name": "Vendor", "address": "vendor@example.com"}
            },
            "receivedDateTime": "2026-07-09T12:00:00Z",
            "conversationId": "AAQkAGConversationId",
            "isRead": False,
            "hasAttachments": False,
            "importance": "normal",
        }
    )


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    return redis
