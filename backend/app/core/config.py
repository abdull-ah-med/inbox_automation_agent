from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load backend/.env regardless of process cwd (pytest/uvicorn cwd can vary).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_ROOT / ".env"

# Graph webhook payloads are small; 1 MiB is generous vs Learn examples.
WEBHOOK_MAX_BODY_BYTES = 1_048_576
WEBHOOK_RATE_LIMIT_PER_MINUTE = 120


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_tenant_id: str = ""
    graph_webhook_client_state: str = ""
    graph_notification_url: str = ""
    graph_lifecycle_url: str = ""
    target_mailboxes: str = ""
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_review_channel_id: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage"
    redis_url: str = "redis://localhost:6379/0"
    # Fernet key (Fernet.generate_key()) — required outside local for MSAL cache encryption.
    msal_cache_encryption_key: str = ""
    environment: Literal["local", "staging", "production"] = "production"
    # Local-only routers (/simulate, /debug) require both local env AND this flag.
    enable_dev_routes: bool = False
    # Shared secret for X-Dev-Api-Key on local/dev routers (required when enable_dev_routes).
    dev_api_key: str = ""
    staleness_threshold_hours: int = Field(default=24, ge=1)
    poll_interval_seconds: int = Field(default=300, ge=60)
    subscription_renew_interval_hours: int = Field(default=48, ge=1)
    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    poll_concurrency: int = Field(default=3, ge=1, le=20)
    redis_max_connections: int = Field(default=50, ge=1, le=500)
    webhook_max_body_bytes: int = Field(default=WEBHOOK_MAX_BODY_BYTES, ge=1024, le=10_485_760)
    webhook_rate_limit_per_minute: int = Field(
        default=WEBHOOK_RATE_LIMIT_PER_MINUTE, ge=10, le=10_000
    )

    classification_model: str = "claude-haiku-4-5"
    draft_model: str = "claude-sonnet-4-6"
    triage_max_tokens: int = Field(default=200, ge=64, le=1024)

    @field_validator("enable_dev_routes", mode="before")
    @classmethod
    def _coerce_bool(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off", ""}:
                return False
        return value

    @property
    def mailbox_list(self) -> list[str]:
        if not self.target_mailboxes.strip():
            return []
        return [m.strip() for m in self.target_mailboxes.split(",") if m.strip()]

    def mailbox_allowed(self, mailbox: str) -> bool:
        """Defense-in-depth allowlist.

        Empty ``TARGET_MAILBOXES`` is only allowed in ``local`` (dev convenience).
        Outside local, empty allowlist denies all mailboxes (fail-closed).
        """
        allowed = self.mailbox_list
        if not allowed:
            return self.environment == "local"
        needle = mailbox.strip().lower()
        return any(m.lower() == needle for m in allowed)

    @property
    def resolved_lifecycle_url(self) -> str:
        """Explicit GRAPH_LIFECYCLE_URL, or derive under the notification URL path prefix.

        Preserves prefixes such as ``/api/...`` — never wipe the path to a bare
        ``/webhooks/graph/lifecycle``.
        """
        if self.graph_lifecycle_url.strip():
            return self.graph_lifecycle_url.strip()
        notification = self.graph_notification_url.strip()
        if not notification:
            return ""
        parsed = urlparse(notification)
        path = parsed.path.rstrip("/") or ""
        if path.endswith("/notifications"):
            new_path = path[: -len("/notifications")] + "/lifecycle"
        elif path:
            new_path = f"{path}/lifecycle"
        else:
            new_path = "/lifecycle"
        return urlunparse(parsed._replace(path=new_path))

    @property
    def redis_uses_tls(self) -> bool:
        return urlparse(self.redis_url).scheme.lower() == "rediss"

    @property
    def redis_has_password(self) -> bool:
        return bool(urlparse(self.redis_url).password)

    def validate_production_security(self) -> list[str]:
        """Return human-readable config errors for non-local deployments.

        Callers should refuse to start the app when this list is non-empty.
        """
        if self.environment == "local":
            return []
        errors: list[str] = []
        if not self.mailbox_list:
            errors.append("TARGET_MAILBOXES must be set when ENVIRONMENT is not local")
        if not self.graph_webhook_client_state.strip():
            errors.append("GRAPH_WEBHOOK_CLIENT_STATE must be set when ENVIRONMENT is not local")
        if len(self.graph_webhook_client_state.strip()) < 32:
            errors.append("GRAPH_WEBHOOK_CLIENT_STATE must be at least 32 characters outside local")
        if not self.redis_has_password:
            errors.append(
                "REDIS_URL must include a password outside local "
                "(redis://:password@host:6379/0 or rediss://...)"
            )
        if not self.redis_uses_tls:
            errors.append(
                "REDIS_URL must use rediss:// (TLS) outside local — "
                "see https://redis.readthedocs.io/en/latest/connections.html"
            )
        if not self.msal_cache_encryption_key.strip():
            errors.append(
                "MSAL_CACHE_ENCRYPTION_KEY must be set outside local "
                "(Fernet key from cryptography.fernet.Fernet.generate_key())"
            )
        if self.enable_dev_routes:
            errors.append("ENABLE_DEV_ROUTES must be false outside local")
        db_host = (urlparse(self.database_url).hostname or "").lower()
        if db_host in {"", "localhost", "127.0.0.1", "::1"}:
            errors.append(
                "DATABASE_URL must not point at localhost outside local "
                f"(got host={db_host!r})"
            )
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()
