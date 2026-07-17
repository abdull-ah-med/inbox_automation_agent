"""Unit tests for Settings helpers (lifecycle URL, mailbox allowlist, defaults)."""

from __future__ import annotations

from unittest.mock import patch

from app.core.config import Settings
from app.main import create_app


def test_environment_default_is_production() -> None:
    assert Settings.model_fields["environment"].default == "production"


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
        target_mailboxes="",
        graph_webhook_client_state="short",
        redis_url="redis://localhost:6379/0",
        msal_cache_encryption_key="",
    )
    errors = settings.validate_production_security()
    assert any("TARGET_MAILBOXES" in e for e in errors)
    assert any("GRAPH_WEBHOOK_CLIENT_STATE" in e for e in errors)
    assert any("REDIS_URL" in e for e in errors)
    assert any("MSAL_CACHE_ENCRYPTION_KEY" in e for e in errors)


def test_validate_production_security_passes_when_hardened() -> None:
    settings = Settings(
        environment="production",
        target_mailboxes="user@example.com",
        graph_webhook_client_state="x" * 32,
        redis_url="rediss://:secret@redis.example:6380/0",
        msal_cache_encryption_key="e0xmjKk-RnBXRjYz-Tsvjar3_Glouxk2n5tNImpxYLc=",
        enable_dev_routes=False,
    )
    assert settings.validate_production_security() == []
