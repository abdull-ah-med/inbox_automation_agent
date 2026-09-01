from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse, urlunparse

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
    # When false (default): poll-primary — no Graph subscriptions, no webhook
    # stream worker, notification POSTs ACK 202 without enqueue. Set true only
    # when GRAPH_NOTIFICATION_URL is a reachable public HTTPS endpoint.
    graph_webhooks_enabled: bool = False
    target_mailboxes: str = ""
    # Personal / user mailboxes whose Sent Items may close shared-inbox threads.
    # Polled outbound-only (Mail.Read). Not triaged as inboxes.
    reviewer_mailboxes: str = ""
    # Extra company domains treated as internal (CSV), in addition to the mailbox domain.
    internal_domains: str = ""
    # Personal mailbox owners: CSV of email:DisplayName (e.g. sampleagent@…:Elise).
    mailbox_owners: str = ""
    # When false, Slack Bolt is not constructed and review cards are skipped
    # (pipeline still completes; slack_delivery=skipped_unconfigured).
    slack_enabled: bool = False
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
    # Interval poller — primary ingest when graph_webhooks_enabled is false.
    poll_enabled: bool = True
    poll_interval_seconds: int = Field(default=300, ge=60)
    # Stuck NEW + open outbound-tip healer. Default off. Mail.Read only.
    heal_threads_enabled: bool = False
    heal_threads_interval_minutes: int = Field(default=60, ge=5, le=1440)
    heal_threads_max_per_run: int = Field(default=50, ge=1, le=500)
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
    # Haiku for ask latency; Sonnet stays on drafts. Override via CHAT_MODEL.
    chat_model: str = "claude-haiku-4-5"
    triage_max_tokens: int = Field(default=200, ge=64, le=1024)
    chat_max_tokens: int = Field(default=1024, ge=256, le=4096)
    chat_groundedness_enabled: bool = False
    chat_semantic_cache_enabled: bool = True
    # Global contact greeting names + bare "Hi," when no personal name is known.
    salute_directory_enabled: bool = False
    chat_semantic_cache_threshold: float = Field(default=0.92, ge=0.5, le=0.999)
    chat_semantic_cache_ttl_overview_sec: int = Field(default=300, ge=30, le=86_400)
    chat_semantic_cache_ttl_search_sec: int = Field(default=300, ge=30, le=86_400)
    chat_max_input_tokens: int = Field(default=160_000, ge=4_096, le=200_000)
    chat_rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)

    # OpenAI embeddings (official: text-embedding-3-small defaults to 1536 dims).
    # Docs: https://platform.openai.com/docs/guides/embeddings
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=1536, ge=1, le=3072)
    embedding_min_similarity: float = Field(default=0.78, ge=0.0, le=1.0)
    urgency_feedback_min_similarity: float = Field(default=0.80, ge=0.0, le=1.0)
    skill_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    # Hybrid retrieval: over-fetch per leg, then collapse to conversations.
    embedding_candidate_k: int = Field(default=50, ge=1, le=100)
    embedding_final_conversations: int = Field(default=1, ge=1, le=5)
    embedding_secondary_margin: float = Field(default=0.85, ge=0.0, le=1.0)
    embedding_corroboration_bonus: float = Field(default=0.15, ge=0.0, le=1.0)
    embedding_corroboration_max_hits: int = Field(default=3, ge=0, le=10)
    rrf_k: int = Field(default=60, ge=1)
    # pgvector HNSW query-time knobs (no index rebuild). Production: 80-200.
    hnsw_ef_search: int = Field(default=100, ge=20, le=1000)
    hnsw_iterative_scan_enabled: bool = True
    # text-embedding-3-* hard cap is 8191 tokens (OpenAI cookbook); stay under.
    embedding_max_input_tokens: int = Field(default=8000, ge=1, le=8191)
    # Same-thread prompt packing.
    thread_verbatim_tail: int = Field(default=2, ge=0, le=10)
    thread_full_if_at_most: int = Field(default=5, ge=1, le=50)

    # Web auth (JWT access + rotating refresh cookie).
    jwt_secret: str = ""
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "inbox-triage-automation"
    jwt_audience: str = "inbox-triage-web"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=604_800, ge=3600)  # 7 days
    refresh_cookie_name: str = "itr_refresh"
    csrf_cookie_name: str = "itr_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    frontend_origin: str = "http://localhost:3000"
    cookie_domain: str = ""
    cookie_secure: bool = True
    # Public Host header value for TrustedHostMiddleware (not localhost outside local).
    # https://fastapi.tiangolo.com/advanced/middleware/#trustedhostmiddleware
    api_host: str = "localhost"
    auth_login_rate_limit: str = "5/minute"
    auth_refresh_rate_limit: str = "30/minute"
    api_default_rate_limit: str = "120/minute"
    # Weekly ops report (PDF on disk + optional SMTP; never Graph send).
    ops_report_dir: str = ""
    ops_report_timezone: str = "America/New_York"
    ops_report_cron_day_of_week: str = "mon"
    ops_report_cron_hour: int = Field(default=8, ge=0, le=23)
    ops_report_cron_minute: int = Field(default=0, ge=0, le=59)
    ops_report_email_enabled: bool = False
    ops_report_smtp_host: str = ""
    ops_report_smtp_port: int = Field(default=587, ge=1, le=65535)
    ops_report_smtp_user: str = ""
    ops_report_smtp_password: str = ""
    ops_report_smtp_from: str = ""
    ops_report_smtp_to: str = ""
    # Enable only behind a reverse proxy that overwrites X-Real-IP (see rate_limit.py).
    trust_x_forwarded_for: bool = False
    # Concurrent refresh grace window (Auth0-style) to avoid false reuse detection.
    refresh_rotation_grace_seconds: int = Field(default=10, ge=0, le=60)
    # CA bundle for redis-py TLS verify (rediss://). Required outside local.
    # https://redis.readthedocs.io/en/latest/connections.html
    redis_ssl_ca_certs: str = ""

    @field_validator(
        "enable_dev_routes",
        "cookie_secure",
        "trust_x_forwarded_for",
        "slack_enabled",
        "ops_report_email_enabled",
        "hnsw_iterative_scan_enabled",
        "chat_groundedness_enabled",
        "chat_semantic_cache_enabled",
        "salute_directory_enabled",
        "graph_webhooks_enabled",
        "poll_enabled",
        "heal_threads_enabled",
        mode="before",
    )
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

    @property
    def internal_domain_list(self) -> list[str]:
        if not self.internal_domains.strip():
            return []
        return [
            d.strip().lower().lstrip("@") for d in self.internal_domains.split(",") if d.strip()
        ]

    @property
    def mailbox_owner_map(self) -> dict[str, str]:
        """Lowercase mailbox email → owner display name."""
        if not self.mailbox_owners.strip():
            return {}
        owners: dict[str, str] = {}
        for part in self.mailbox_owners.split(","):
            entry = part.strip()
            if not entry or ":" not in entry:
                continue
            email, _, name = entry.partition(":")
            email_key = email.strip().lower()
            display = name.strip()
            if email_key and display:
                owners[email_key] = display
        return owners

    def owner_for_mailbox(self, email: str) -> str | None:
        from app.core.mailbox_keys import owner_for_mailbox

        return owner_for_mailbox(email, self.mailbox_owner_map)

    @property
    def reviewer_mailbox_list(self) -> list[str]:
        """Reviewer addresses not already in TARGET_MAILBOXES (sent-items poll only)."""
        targets = {m.lower() for m in self.mailbox_list}
        return [
            m.strip()
            for m in self.reviewer_mailboxes.split(",")
            if m.strip() and m.strip().lower() not in targets
        ]

    def overlapping_target_and_reviewer_mailboxes(self) -> list[str]:
        """Addresses listed in both TARGET_MAILBOXES and REVIEWER_MAILBOXES."""
        targets = {item.strip().lower() for item in self.mailbox_list}
        reviewers = {
            item.strip().lower() for item in self.reviewer_mailboxes.split(",") if item.strip()
        }
        return sorted(targets & reviewers)

    def is_reviewer_address(self, address: str) -> bool:
        from app.core.internal_mail import extract_email_address

        needle = extract_email_address(address) or address.strip().lower()
        if not needle:
            return False
        allowed = {m.strip().lower() for m in self.reviewer_mailboxes.split(",") if m.strip()}
        return needle in allowed

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

    def outbound_mailbox_allowed(self, mailbox: str) -> bool:
        """Target inboxes plus reviewer mailboxes (Sent Items only)."""
        return self.mailbox_allowed(mailbox) or self.is_reviewer_address(mailbox)

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

    @property
    def redis_ssl_cert_reqs_query(self) -> str | None:
        """Value of ``ssl_cert_reqs`` query param on ``REDIS_URL``, if present."""
        raw = parse_qs(urlparse(self.redis_url).query).get("ssl_cert_reqs")
        if not raw:
            return None
        return raw[0].strip().lower() or None

    def _production_graph_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.anthropic_api_key.strip():
            errors.append(
                "ANTHROPIC_API_KEY must be set when ENVIRONMENT is not local "
                "(triage and draft generation cannot run without it)"
            )
        if not self.graph_client_id.strip():
            errors.append("GRAPH_CLIENT_ID must be set when ENVIRONMENT is not local")
        if not self.graph_client_secret.strip():
            errors.append("GRAPH_CLIENT_SECRET must be set when ENVIRONMENT is not local")
        if not self.graph_tenant_id.strip():
            errors.append("GRAPH_TENANT_ID must be set when ENVIRONMENT is not local")
        if not self.mailbox_list:
            errors.append("TARGET_MAILBOXES must be set when ENVIRONMENT is not local")
        if self.graph_webhooks_enabled:
            if not self.graph_notification_url.strip():
                errors.append("GRAPH_NOTIFICATION_URL must be set when GRAPH_WEBHOOKS_ENABLED=true")
            if not self.graph_webhook_client_state.strip():
                errors.append(
                    "GRAPH_WEBHOOK_CLIENT_STATE must be set when GRAPH_WEBHOOKS_ENABLED=true"
                )
            if len(self.graph_webhook_client_state.strip()) < 32:
                errors.append(
                    "GRAPH_WEBHOOK_CLIENT_STATE must be at least 32 characters when "
                    "GRAPH_WEBHOOKS_ENABLED=true"
                )
        return errors

    def _production_redis_errors(self) -> list[str]:
        errors: list[str] = []
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
            return errors
        if not self.redis_ssl_ca_certs.strip():
            errors.append(
                "REDIS_SSL_CA_CERTS must be set outside local "
                "(path to CA cert for redis-py ssl_ca_certs / server auth)"
            )
        cert_reqs = self.redis_ssl_cert_reqs_query
        if cert_reqs in {"none", "optional"}:
            errors.append(
                "REDIS_URL must not set ssl_cert_reqs=none|optional outside local — "
                "use REDIS_SSL_CA_CERTS with ssl_cert_reqs=required "
                "(redis-py default)"
            )
        return errors

    def _production_integration_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.msal_cache_encryption_key.strip():
            errors.append(
                "MSAL_CACHE_ENCRYPTION_KEY must be set outside local "
                "(Fernet key from cryptography.fernet.Fernet.generate_key())"
            )
        if self.slack_enabled:
            if not self.slack_bot_token.strip():
                errors.append("SLACK_BOT_TOKEN must be set when ENVIRONMENT is not local")
            if not self.slack_signing_secret.strip():
                errors.append("SLACK_SIGNING_SECRET must be set when ENVIRONMENT is not local")
            if not self.slack_review_channel_id.strip():
                errors.append("SLACK_REVIEW_CHANNEL_ID must be set when ENVIRONMENT is not local")
        if self.enable_dev_routes:
            errors.append("ENABLE_DEV_ROUTES must be false outside local")
        return errors

    def _production_host_auth_errors(self) -> list[str]:
        errors: list[str] = []
        db_host = (urlparse(self.database_url).hostname or "").lower()
        if db_host in {"", "localhost", "127.0.0.1", "::1"}:
            errors.append(
                f"DATABASE_URL must not point at localhost outside local (got host={db_host!r})"
            )
        api_host = self.api_host.strip().lower()
        if api_host in {"", "localhost", "127.0.0.1", "::1", "*"}:
            errors.append(
                "API_HOST must be the public hostname clients send in the Host header "
                "(e.g. your sslip.io / domain) outside local — TrustedHostMiddleware "
                "rejects other Host values; localhost breaks nginx-proxied traffic"
            )
        if len(self.jwt_secret.strip()) < 64:
            errors.append("JWT_SECRET must be at least 64 characters outside local")
        if not self.cookie_secure:
            errors.append("COOKIE_SECURE must be true outside local")
        if not self.frontend_origin.strip().lower().startswith("https://"):
            errors.append("FRONTEND_ORIGIN must be an https:// URL outside local")
        if not self.trust_x_forwarded_for:
            errors.append(
                "TRUST_X_FORWARDED_FOR must be true outside local when behind a "
                "reverse proxy that sets X-Real-IP / X-Forwarded-For "
                "(see deploy/nginx.conf.template) — otherwise login rate limits "
                "collapse onto 127.0.0.1 and lock out all users"
            )
        return errors

    def validate_production_security(self) -> list[str]:
        """Return human-readable config errors for non-local deployments.

        Callers should refuse to start the app when this list is non-empty.
        """
        if self.environment == "local":
            return []
        return [
            *self._production_graph_errors(),
            *self._production_redis_errors(),
            *self._production_integration_errors(),
            *self._production_host_auth_errors(),
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
