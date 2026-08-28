"""Poll-primary mode: webhook ingestion can be fully disabled."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.workers import graph_subscription_worker


def test_graph_webhooks_enabled_defaults_false() -> None:
    """Oracle: poll-primary — webhooks off unless explicitly enabled."""
    settings = Settings(environment="local", _env_file=None)
    assert settings.graph_webhooks_enabled is False


def test_production_security_skips_webhook_client_state_when_webhooks_disabled() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="sk-ant-prod-key",
        graph_client_id="00000000-0000-0000-0000-000000000000",
        graph_client_secret="graph-secret-prod",
        graph_tenant_id="11111111-1111-1111-1111-111111111111",
        target_mailboxes="user@example.com",
        graph_webhooks_enabled=False,
        graph_webhook_client_state="",
        redis_url="rediss://:secret@redis.example:6380/0",
        redis_ssl_ca_certs="/tls/ca.crt",
        msal_cache_encryption_key="x" * 44,
        database_url="postgresql+asyncpg://u:p@db.example:5432/inbox_triage",
        api_host="api.example.com",
        frontend_origin="https://app.example.com",
        cookie_secure=True,
        jwt_secret="j" * 64,
        enable_dev_routes=False,
        slack_enabled=False,
        trust_x_forwarded_for=True,
        _env_file=None,
    )
    errors = settings.validate_production_security()
    assert not any("GRAPH_WEBHOOK_CLIENT_STATE" in e for e in errors)


def test_production_security_requires_webhook_client_state_when_webhooks_enabled() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="sk-ant-prod-key",
        graph_client_id="00000000-0000-0000-0000-000000000000",
        graph_client_secret="graph-secret-prod",
        graph_tenant_id="11111111-1111-1111-1111-111111111111",
        target_mailboxes="user@example.com",
        graph_webhooks_enabled=True,
        graph_notification_url="https://api.example.com/webhooks/graph/notifications",
        graph_webhook_client_state="short",
        redis_url="rediss://:secret@redis.example:6380/0",
        redis_ssl_ca_certs="/tls/ca.crt",
        msal_cache_encryption_key="x" * 44,
        database_url="postgresql+asyncpg://u:p@db.example:5432/inbox_triage",
        api_host="api.example.com",
        frontend_origin="https://app.example.com",
        cookie_secure=True,
        jwt_secret="j" * 64,
        enable_dev_routes=False,
        slack_enabled=False,
        trust_x_forwarded_for=True,
        _env_file=None,
    )
    errors = settings.validate_production_security()
    assert any("GRAPH_WEBHOOK_CLIENT_STATE" in e for e in errors)


@pytest.mark.asyncio
async def test_subscription_reconcile_skips_graph_auth_when_webhooks_disabled() -> None:
    """Bug this catches: poll-only startup still built MSAL and hit the network."""
    settings = Settings(
        environment="local",
        graph_webhooks_enabled=False,
        target_mailboxes="user@example.com",
        _env_file=None,
    )
    with (
        patch("app.workers.graph_subscription_worker.get_settings", return_value=settings),
        patch(
            "app.workers.graph_subscription_worker.get_graph_auth",
            new_callable=AsyncMock,
        ) as get_auth,
        patch(
            "app.workers.graph_subscription_worker.get_redis",
            new_callable=AsyncMock,
        ) as get_redis,
    ):
        await graph_subscription_worker.run_subscription_reconcile()
        await graph_subscription_worker.run_subscription_renewal()

    get_auth.assert_not_awaited()
    get_redis.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_notifications_ack_without_enqueue_when_disabled() -> None:
    from app.api.webhooks import graph as graph_mod
    from app.core.dependencies import get_redis, get_settings

    app = FastAPI()
    app.include_router(graph_mod.router)
    settings = Settings(
        environment="local",
        graph_webhooks_enabled=False,
        graph_webhook_client_state="secret",
        _env_file=None,
    )
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.eval = AsyncMock(return_value=1)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: redis

    with patch("app.api.webhooks.graph.enqueue_webhook_job", new_callable=AsyncMock) as enqueue:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhooks/graph/notifications",
                json={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "clientState": "secret",
                            "changeType": "created",
                            "resource": "users/user@example.com/messages/msg-1",
                            "resourceData": {"id": "msg-1"},
                        }
                    ]
                },
            )

    assert response.status_code == 202
    enqueue.assert_not_awaited()
