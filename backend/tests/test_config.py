"""Unit tests for Settings helpers (lifecycle URL, mailbox allowlist, defaults)."""

from __future__ import annotations

from unittest.mock import patch

from app.core.config import Settings
from app.main import create_app


def test_embedding_settings_defaults() -> None:
    settings = Settings(environment="local")
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimension == 1536
    assert settings.embedding_min_similarity == 0.78
    assert settings.embedding_candidate_k == 15
    assert settings.embedding_final_conversations == 1
    assert settings.embedding_max_input_tokens == 8000
    assert settings.thread_verbatim_tail == 2
    assert settings.thread_full_if_at_most == 5
    assert settings.rrf_k == 60


def test_create_app_production_omits_local_routers_and_docs() -> None:
    """Fail-closed: production must not mount simulate/debug or expose OpenAPI."""
    settings = Settings(environment="production")
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main.configure_logging"),
    ):
        app = create_app()

    paths = set(app.openapi()["paths"])
    assert "/simulate/ingest" not in paths
    assert "/debug/graph-check" not in paths
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_create_app_local_without_dev_flag_omits_simulate() -> None:
    settings = Settings(environment="local", enable_dev_routes=False)
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main.configure_logging"),
    ):
        app = create_app()

    paths = set(app.openapi()["paths"])
    assert "/simulate/ingest" not in paths
    assert app.docs_url == "/docs"


def test_create_app_local_with_dev_flag_mounts_simulate_and_docs() -> None:
    settings = Settings(
        environment="local",
        enable_dev_routes=True,
        dev_api_key="test-dev-key",
    )
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main.configure_logging"),
    ):
        app = create_app()

    paths = set(app.openapi()["paths"])
    assert "/simulate/ingest" in paths
    assert "/debug/graph-check" in paths
    assert app.docs_url == "/docs"


def test_resolved_lifecycle_url_swaps_notifications_segment() -> None:
    settings = Settings(
        environment="local",
        graph_notification_url="https://host/api/webhooks/graph/notifications",
        graph_lifecycle_url="",
    )
    assert settings.resolved_lifecycle_url == "https://host/api/webhooks/graph/lifecycle"


def test_resolved_lifecycle_url_appends_under_prefix() -> None:
    settings = Settings(
        environment="local",
        graph_notification_url="https://host/api/hooks",
        graph_lifecycle_url="",
    )
    assert settings.resolved_lifecycle_url == "https://host/api/hooks/lifecycle"


def test_resolved_lifecycle_url_explicit_wins() -> None:
    settings = Settings(
        environment="local",
        graph_notification_url="https://host/api/webhooks/graph/notifications",
        graph_lifecycle_url="https://other/lifecycle",
    )
    assert settings.resolved_lifecycle_url == "https://other/lifecycle"


def test_mailbox_allowed_case_insensitive() -> None:
    settings = Settings(
        environment="local",
        target_mailboxes="User@Example.com",
    )
    assert settings.mailbox_allowed("user@example.com")
    assert not settings.mailbox_allowed("other@example.com")


def test_mailbox_allowed_empty_list_local_allows() -> None:
    settings = Settings(environment="local", target_mailboxes="")
    assert settings.mailbox_allowed("anyone@example.com")


def test_mailbox_allowed_empty_list_production_denies() -> None:
    settings = Settings(environment="production", target_mailboxes="")
    assert not settings.mailbox_allowed("anyone@example.com")


def test_validate_production_security_requires_hardening() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="",
        graph_client_id="",
        graph_client_secret="",
        graph_tenant_id="",
        target_mailboxes="",
        graph_webhook_client_state="short",
        redis_url="redis://localhost:6379/0",
        redis_ssl_ca_certs="",
        msal_cache_encryption_key="",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage",
        api_host="localhost",
        slack_enabled=True,
        slack_bot_token="",
        slack_signing_secret="",
        slack_review_channel_id="",
    )
    errors = settings.validate_production_security()
    assert any("ANTHROPIC_API_KEY" in e for e in errors)
    assert any("GRAPH_CLIENT_ID" in e for e in errors)
    assert any("GRAPH_CLIENT_SECRET" in e for e in errors)
    assert any("GRAPH_TENANT_ID" in e for e in errors)
    assert any("TARGET_MAILBOXES" in e for e in errors)
    assert any("GRAPH_WEBHOOK_CLIENT_STATE" in e for e in errors)
    assert any("REDIS_URL" in e for e in errors)
    assert any("MSAL_CACHE_ENCRYPTION_KEY" in e for e in errors)
    assert any("DATABASE_URL" in e for e in errors)
    assert any("API_HOST" in e for e in errors)
    assert any("SLACK_BOT_TOKEN" in e for e in errors)
    assert any("SLACK_SIGNING_SECRET" in e for e in errors)
    assert any("SLACK_REVIEW_CHANNEL_ID" in e for e in errors)


def test_validate_production_security_rejects_ssl_cert_reqs_none() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="sk-ant-prod-key",
        graph_client_id="00000000-0000-0000-0000-000000000000",
        graph_client_secret="graph-secret-prod",
        graph_tenant_id="11111111-1111-1111-1111-111111111111",
        target_mailboxes="user@example.com",
        graph_webhook_client_state="x" * 32,
        redis_url="rediss://:secret@redis.example:6380/0?ssl_cert_reqs=none",
        redis_ssl_ca_certs="/tls/ca.crt",
        msal_cache_encryption_key="e0xmjKk-RnBXRjYz-Tsvjar3_Glouxk2n5tNImpxYLc=",
        database_url="postgresql+asyncpg://app:secret@db.example:5432/inbox_triage",
        enable_dev_routes=False,
        slack_enabled=False,
        jwt_secret="x" * 64,
        cookie_secure=True,
        frontend_origin="https://app.example.com",
        api_host="app.example.com",
        trust_x_forwarded_for=True,
    )
    errors = settings.validate_production_security()
    assert any("ssl_cert_reqs" in e for e in errors)


def test_validate_production_security_skips_slack_when_disabled() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="sk-ant-prod-key",
        graph_client_id="00000000-0000-0000-0000-000000000000",
        graph_client_secret="graph-secret-prod",
        graph_tenant_id="11111111-1111-1111-1111-111111111111",
        target_mailboxes="user@example.com",
        graph_webhook_client_state="x" * 32,
        redis_url="rediss://:secret@redis.example:6380/0",
        redis_ssl_ca_certs="/tls/ca.crt",
        msal_cache_encryption_key="e0xmjKk-RnBXRjYz-Tsvjar3_Glouxk2n5tNImpxYLc=",
        database_url="postgresql+asyncpg://app:secret@db.example:5432/inbox_triage",
        enable_dev_routes=False,
        slack_enabled=False,
        slack_bot_token="",
        slack_signing_secret="",
        slack_review_channel_id="",
        jwt_secret="x" * 64,
        cookie_secure=True,
        frontend_origin="https://app.example.com",
        api_host="app.example.com",
        trust_x_forwarded_for=True,
    )
    errors = settings.validate_production_security()
    assert not any("SLACK_" in e for e in errors)
    assert errors == []


def test_validate_production_security_requires_trust_x_forwarded_for() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="sk-ant-prod-key",
        graph_client_id="00000000-0000-0000-0000-000000000000",
        graph_client_secret="graph-secret-prod",
        graph_tenant_id="11111111-1111-1111-1111-111111111111",
        target_mailboxes="user@example.com",
        graph_webhook_client_state="x" * 32,
        redis_url="rediss://:secret@redis.example:6380/0",
        redis_ssl_ca_certs="/tls/ca.crt",
        msal_cache_encryption_key="e0xmjKk-RnBXRjYz-Tsvjar3_Glouxk2n5tNImpxYLc=",
        database_url="postgresql+asyncpg://app:secret@db.example:5432/inbox_triage",
        enable_dev_routes=False,
        slack_enabled=False,
        jwt_secret="x" * 64,
        cookie_secure=True,
        frontend_origin="https://app.example.com",
        api_host="app.example.com",
        trust_x_forwarded_for=False,
    )
    errors = settings.validate_production_security()
    assert any("TRUST_X_FORWARDED_FOR" in e for e in errors)


def test_validate_production_security_passes_when_hardened() -> None:
    settings = Settings(
        environment="production",
        anthropic_api_key="sk-ant-prod-key",
        graph_client_id="00000000-0000-0000-0000-000000000000",
        graph_client_secret="graph-secret-prod",
        graph_tenant_id="11111111-1111-1111-1111-111111111111",
        target_mailboxes="user@example.com",
        graph_webhook_client_state="x" * 32,
        redis_url="rediss://:secret@redis.example:6380/0",
        redis_ssl_ca_certs="/tls/ca.crt",
        msal_cache_encryption_key="e0xmjKk-RnBXRjYz-Tsvjar3_Glouxk2n5tNImpxYLc=",
        database_url="postgresql+asyncpg://app:secret@db.example:5432/inbox_triage",
        enable_dev_routes=False,
        slack_bot_token="xoxb-prod-token",
        slack_signing_secret="signing-secret-prod",
        slack_review_channel_id="C0123456789",
        jwt_secret="x" * 64,
        cookie_secure=True,
        frontend_origin="https://app.example.com",
        api_host="app.example.com",
        trust_x_forwarded_for=True,
    )
    assert settings.validate_production_security() == []
