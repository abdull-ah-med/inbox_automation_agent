from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load backend/.env regardless of process cwd (pytest/uvicorn cwd can vary).
# Note: a real OS env var TARGET_MAILBOXES still overrides the file — unset it if stuck.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_ROOT / ".env"


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
    environment: Literal["local", "staging", "production"] = "local"
    staleness_threshold_hours: int = Field(default=24, ge=1)
    poll_interval_seconds: int = Field(default=300, ge=60)
    subscription_renew_interval_hours: int = Field(default=48, ge=1)

    classification_model: str = "claude-haiku-4-5"
    draft_model: str = "claude-sonnet-4-6"

    @property
    def mailbox_list(self) -> list[str]:
        if not self.target_mailboxes.strip():
            return []
        return [m.strip() for m in self.target_mailboxes.split(",") if m.strip()]

    @property
    def resolved_lifecycle_url(self) -> str:
        """Explicit GRAPH_LIFECYCLE_URL, or derive from notification URL path."""
        if self.graph_lifecycle_url.strip():
            return self.graph_lifecycle_url.strip()
        notification = self.graph_notification_url.strip()
        if not notification:
            return ""
        if notification.rstrip("/").endswith("/notifications"):
            return notification.rstrip("/").removesuffix("/notifications") + "/lifecycle"
        parsed = urlparse(notification)
        return urlunparse(parsed._replace(path="/webhooks/graph/lifecycle"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
