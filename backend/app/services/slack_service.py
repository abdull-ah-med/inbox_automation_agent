"""Slack review-queue card builder + idempotent poster.

Block Kit structure follows the SOW review card (no confidence badge).
Posting uses ``chat.postMessage`` via Bolt's async Web client
(https://docs.slack.dev/reference/methods/chat.postMessage).
Action handlers are intentionally out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import structlog
from redis.asyncio import Redis
from slack_bolt.async_app import AsyncApp

from app.core.config import Settings
from app.core.outlook_links import outlook_web_link
from app.core.redis_keys import SLACK_POSTED_TTL_SECONDS, slack_posted_key
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email_triage_state import EmailTriageState, SlackDelivery

logger = structlog.get_logger(__name__)

ACTION_ID_APPROVE = "inbox_triage_approve"
ACTION_ID_EDIT = "inbox_triage_edit"
ACTION_ID_REJECT = "inbox_triage_reject"

SlackPostStatus = Literal[
    "posted",
    "already_posted",
    "skipped_unconfigured",
    "skipped_no_draft",
    "failed",
]


@dataclass(frozen=True, slots=True)
class SlackPostResult:
    """Outcome of ``post_review_card`` — drives ingest dedup completion."""

    status: SlackPostStatus
    message_ts: str | None = None

    @property
    def slack_delivery(self) -> SlackDelivery:
        if self.status == "posted":
            return "posted"
        if self.status == "already_posted":
            return "already_posted"
        if self.status == "skipped_unconfigured":
            return "skipped_unconfigured"
        return "failed"

    @property
    def ok_for_dedup(self) -> bool:
        return self.status in {"posted", "already_posted", "skipped_unconfigured"}


_URGENCY_BADGE: dict[str, str] = {
    "CRITICAL": "🔴 CRITICAL",
    "HIGH": "🟠 HIGH",
    "NORMAL": "🟡 NORMAL",
    "LOW": "⚪ LOW",
}

_HEADER_MAX_LEN = 150
_SECTION_MAX_LEN = 2900


def _truncate(text: str, max_len: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    if max_len <= 1:
        return cleaned[:max_len]
    return cleaned[: max_len - 1] + "…"


def _urgency_badge(urgency: str) -> str:
    return _URGENCY_BADGE.get(urgency, f"• {urgency}")


def _thread_summary(state: EmailTriageState) -> str:
    triage = state.triage
    if triage is not None and triage.action_items_summary:
        return _truncate(triage.action_items_summary, 280)
    preview = state.original_email.body_preview or state.original_email.body_text
    if preview.strip():
        return _truncate(preview, 280)
    return _truncate(state.original_email.subject or "(no subject)", 280)


def _proposed_action(draft: DraftSchema) -> str:
    if draft.forward_to:
        return f"Forward to `{draft.forward_to}`"
    recipients = [r.role for r in draft.suggested_recipients if r.role.strip()]
    if recipients:
        joined = ", ".join(recipients)
        return f"Reply — suggested recipients: {joined}"
    return "Reply to sender"


def _draft_value(state: EmailTriageState) -> str:
    """Compact button value for Day-7 handlers (mailbox + message id)."""
    email = state.original_email
    return f"{email.mailbox}|{email.message_id}"


def build_review_card(state: EmailTriageState) -> list[dict[str, Any]]:
    """Pure Block Kit builder — no I/O. Requires ``state.draft`` to be set."""
    draft = state.draft
    if draft is None:
        raise ValueError("build_review_card requires state.draft")

    email = state.original_email
    subject = draft.subject_line or email.subject or "(no subject)"
    badge = _urgency_badge(draft.urgency)
    header_text = _truncate(f"{badge} · {email.mailbox} · {subject}", _HEADER_MAX_LEN)
    summary = _truncate(_thread_summary(state), _SECTION_MAX_LEN)
    action_text = _truncate(_proposed_action(draft), _SECTION_MAX_LEN)
    teaching = _truncate(draft.teaching_note, _SECTION_MAX_LEN)
    # Slack mrkdwn code fence keeps the draft readable without interpreting markup.
    body = _truncate(draft.reply_body, _SECTION_MAX_LEN - 10)
    draft_mrkdwn = f"```{body}```"
    button_value = _draft_value(state)
    open_url = outlook_web_link(email.message_id)

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Thread summary*\n{summary}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Open in Outlook*\n<{open_url}|Open email in Outlook>",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed action*\n{action_text}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Teaching note*\n{teaching}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Draft*\n{draft_mrkdwn}",
            },
        },
        {
            "type": "actions",
            "block_id": "inbox_triage_review_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "action_id": ACTION_ID_APPROVE,
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit", "emoji": True},
                    "action_id": ACTION_ID_EDIT,
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                    "style": "danger",
                    "action_id": ACTION_ID_REJECT,
                    "value": button_value,
                },
            ],
        },
    ]


def _slack_configured(settings: Settings, slack_app: AsyncApp | None) -> bool:
    return (
        settings.slack_enabled
        and slack_app is not None
        and bool(settings.slack_bot_token.strip())
        and bool(settings.slack_signing_secret.strip())
        and bool(settings.slack_review_channel_id.strip())
    )


async def post_review_card(
    state: EmailTriageState,
    *,
    redis: Redis,
    slack_app: AsyncApp | None,
    settings: Settings,
) -> SlackPostResult:
    """Post a review card once per ``(mailbox, message_id)``.

    Never logs email body or draft text — metadata only.
    On API failure or missing ``ts``, the idempotency key is released so poll
    can retry. ``already_posted`` / ``skipped_unconfigured`` are OK for dedup.
    """
    if not _slack_configured(settings, slack_app):
        logger.warning(
            "slack_post_skipped_unconfigured",
            mailbox=state.original_email.mailbox,
            message_id=state.original_email.message_id,
        )
        return SlackPostResult(status="skipped_unconfigured")

    if state.draft is None or state.draft_status != "DRAFTED":
        logger.warning(
            "slack_post_skipped_no_draft",
            mailbox=state.original_email.mailbox,
            message_id=state.original_email.message_id,
            draft_status=state.draft_status,
        )
        return SlackPostResult(status="skipped_no_draft")

    assert slack_app is not None  # narrowed by _slack_configured
    email = state.original_email
    lock_key = slack_posted_key(email.mailbox, email.message_id)
    acquired = await redis.set(lock_key, "1", nx=True, ex=SLACK_POSTED_TTL_SECONDS)
    if not acquired:
        logger.info(
            "slack_post_idempotent_skip",
            mailbox=email.mailbox,
            message_id=email.message_id,
        )
        return SlackPostResult(status="already_posted")

    blocks = build_review_card(state)
    channel = settings.slack_review_channel_id.strip()
    fallback_text = (
        f"Review: {_urgency_badge(state.draft.urgency)} · {email.mailbox} · "
        f"{state.draft.subject_line or email.subject}"
    )

    try:
        # chat.postMessage — https://docs.slack.dev/reference/methods/chat.postMessage
        response = await slack_app.client.chat_postMessage(
            channel=channel,
            text=_truncate(fallback_text, 300),
            blocks=blocks,
        )
    except Exception:
        # Allow a later retry to post — release the idempotency lock on failure.
        await redis.delete(lock_key)
        logger.exception(
            "slack_post_failed",
            mailbox=email.mailbox,
            message_id=email.message_id,
            block_count=len(blocks),
        )
        return SlackPostResult(status="failed")

    message_ts = response.get("ts") if hasattr(response, "get") else None
    if not message_ts and isinstance(response, dict):
        message_ts = response.get("ts")
    if not message_ts:
        # Unknown success shape — release lock so poll can retry cleanly.
        await redis.delete(lock_key)
        logger.error(
            "slack_post_missing_ts",
            mailbox=email.mailbox,
            message_id=email.message_id,
            block_count=len(blocks),
        )
        return SlackPostResult(status="failed")

    logger.info(
        "slack_card_posted",
        mailbox=email.mailbox,
        message_id=email.message_id,
        block_count=len(blocks),
        message_ts=message_ts,
        channel=channel,
    )
    return SlackPostResult(status="posted", message_ts=str(message_ts))
