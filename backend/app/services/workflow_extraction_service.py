"""Workflow extraction on resolve (feedback loops v2, plan §15).

After a thread is RESOLVED, one Haiku call classifies ROUTINE_WORKFLOW vs
SPECIAL_CASE vs ADMIN. ROUTINE only: atomize Elise's resolve actions and
queue a promotion proposal. Humans accept; nothing auto-enters live retrieval.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.prompts import WORKFLOW_CLASSIFY_SYSTEM_PROMPT
from app.repositories import promotion_proposal_repo
from app.services.feedback_atom_service import atomize_and_persist
from app.services.feedback_draft_context import paired_retrieval_enabled

logger = structlog.get_logger(__name__)

_VALID_CLASSIFICATIONS = frozenset({"ROUTINE_WORKFLOW", "SPECIAL_CASE", "ADMIN"})
_PROPOSAL_EXPIRY_DAYS = 30
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_classification(raw: str) -> str | None:
    stripped = _JSON_FENCE.sub("", (raw or "").strip())
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("workflow_classify_json_parse_failed", raw=(raw or "")[:200])
        return None
    value = str(obj.get("classification") or "").strip().upper()
    if value not in _VALID_CLASSIFICATIONS:
        logger.warning("workflow_classify_unknown_label", classification=value)
        return None
    return value


async def _classify_thread(
    client: AsyncAnthropic,
    settings: Settings,
    *,
    email_text: str,
    thread_summary: str = "",
) -> str | None:
    """Haiku classify. Conservative: anything other than a valid label is None."""
    parts = [f"Email:\n{email_text.strip()}"]
    if thread_summary.strip():
        parts.append(f"Thread summary:\n{thread_summary.strip()}")
    try:
        response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=64,
            system=WORKFLOW_CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
        )
    except Exception:
        logger.exception("workflow_classify_api_call_failed")
        return None
    if not response.content:
        return None
    raw = response.content[0].text if hasattr(response.content[0], "text") else ""
    return _parse_classification(raw)


async def maybe_extract_workflow(
    session: AsyncSession,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    openai_client: AsyncOpenAI | None,
    mailbox: str,
    thread_id: uuid.UUID,
    email_text: str,
    thread_summary: str = "",
    sender_address: str | None = None,
    sender_domain: str | None = None,
    routing_category: str | None = None,
    atomization_text: str | None = None,
) -> None:
    if not getattr(settings, "feedback_atoms_enabled", False):
        return
    if not paired_retrieval_enabled(settings, mailbox):
        return

    classification = await _classify_thread(
        client,
        settings,
        email_text=email_text,
        thread_summary=thread_summary,
    )
    if classification != "ROUTINE_WORKFLOW":
        return

    note = (atomization_text or "").strip() or email_text
    atoms = await atomize_and_persist(
        session,
        client,
        settings,
        openai_client,
        source_kind="workflow_extraction",
        source_id=thread_id,
        mailbox=mailbox,
        text=note,
        thread_id=thread_id,
        sender_address=sender_address,
        sender_domain=sender_domain,
        routing_category=routing_category,
        email_text_for_context=email_text,
    )
    if not atoms:
        return

    expires_at = datetime.now(UTC) + timedelta(days=_PROPOSAL_EXPIRY_DAYS)
    await promotion_proposal_repo.create_promotion_proposal(
        session,
        mailbox=mailbox,
        kind="atom_widening",
        payload={
            "source_kind": "workflow_extraction",
            "thread_id": str(thread_id),
            "person_bound": True,
        },
        impact_num=len(atoms),
        impact_den=len(atoms),
        evidence_ids=[atom.id for atom in atoms],
        expires_at=expires_at,
    )
