"""Component-scoped context builders for DeepEval / RAGAS contracts.

Suite A — Flow B retrieval chunks only
Suite B — all grounding strings fed to Sonnet (skills, refs, Flow B, tone, negatives)
Suite C — tools_called / expected_tools for read_skill_reference
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.dependencies import anthropic_client_from_settings
from app.llm import draft_generator as draft_llm
from app.llm.context_pack import pack_cross_thread
from app.llm.pii_redact import scrub_text
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema
from app.repositories.skill_repo import SkillSelectionRow
from app.services import skill_archive_service, skill_selection_service
from tests.fixtures.samplelab_rebilling_archive import load_samplelab_rebilling_zip_bytes


@dataclass
class EvalPayload:
    """Normalized eval fields for both frameworks."""

    case_id: str
    input_text: str
    actual_output: str
    expected_output: str | None
    flow_b_chunks: list[str]
    grounding_contexts: list[str]
    tools_called: list[dict[str, Any]]
    expected_tools: list[dict[str, Any]]
    loaded_reference_paths: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    teaching_note: str | None = None
    urgency: str | None = None


def _settings_from_env() -> Settings:
    settings = Settings()
    if not settings.anthropic_api_key.strip():
        raise RuntimeError("ANTHROPIC_API_KEY required for LLM eval generation")
    return settings


def _triage_from_case(case: dict[str, Any]) -> TriageResultSchema:
    raw = case.get("triage") or {}
    return TriageResultSchema(
        is_spam=bool(raw.get("is_spam", False)),
        has_action_items=bool(raw.get("has_action_items", True)),
        needs_context=bool(raw.get("needs_context", False)),
        routing_category=str(raw.get("routing_category") or "general"),
        action_items_summary=raw.get("action_items_summary"),
        spam_reason=raw.get("spam_reason"),
        context_reason=raw.get("context_reason"),
    )


def _email_from_case(case: dict[str, Any]) -> EmailMessageSchema:
    subject = str(case["subject"])
    body = str(case["body"])
    mailbox = str(case.get("mailbox") or "evals@example.com")
    sender = str(case.get("sender") or "sender@example.com")
    return EmailMessageSchema(
        message_id=f"eval-{case['id']}",
        conversation_id=f"eval-conv-{case['id']}",
        mailbox=mailbox,
        sender=sender,
        subject=subject,
        body_text=body,
        body_preview=body[:120],
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=[mailbox],
        cc_recipients=[],
    )


def _msg(
    *,
    direction: str,
    sender: str,
    subject: str,
    body: str,
    offset_minutes: int,
    mailbox: str,
    conversation_id: str,
    message_id: str,
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id=conversation_id,
        mailbox=mailbox,
        sender=sender,
        subject=subject,
        body_text=body,
        body_preview=body[:120],
        received_at=datetime.now(UTC) - timedelta(minutes=offset_minutes),
        direction=(
            EmailDirectionEnum.INBOUND
            if direction.lower() == "inbound"
            else EmailDirectionEnum.OUTBOUND
        ),
        to_recipients=[mailbox],
        cc_recipients=[],
    )


def build_flow_b_context(case: dict[str, Any]) -> CrossThreadContextSchema | None:
    raw = case.get("flow_b")
    if not raw:
        return None
    mailbox = str(case.get("mailbox") or "evals@example.com")
    conv = str(raw["matched_conversation_id"])
    messages: list[EmailMessageSchema] = []
    for idx, item in enumerate(raw.get("messages") or []):
        messages.append(
            _msg(
                direction=str(item.get("direction") or "inbound"),
                sender=str(item.get("sender") or "prior@example.com"),
                subject=str(item.get("subject") or case["subject"]),
                body=str(item.get("body") or ""),
                offset_minutes=60 * (len(raw["messages"]) - idx),
                mailbox=mailbox,
                conversation_id=conv,
                message_id=f"{conv}-msg-{idx}",
            )
        )
    return CrossThreadContextSchema(
        matched_conversation_id=conv,
        similarity_score=float(raw.get("similarity_score") or 0.8),
        thread_messages=messages,
    )


def flow_b_chunks(cross: CrossThreadContextSchema | None) -> list[str]:
    """Suite A — ranked retrieval chunks (one string per related message)."""
    if cross is None or not cross.thread_messages:
        return []
    chunks: list[str] = []
    for msg in cross.thread_messages:
        body = (msg.body_clean or msg.body_text or msg.body_preview or "").strip()
        chunks.append(
            f"[{msg.direction.value}] from={msg.sender} subject={msg.subject!r}\n{body}"
        )
    return chunks


def _selection_row(
    *,
    skill_id: uuid.UUID,
    name: str,
    description: str,
    content: str,
    refs: list[str],
    category: str = "billing",
) -> SkillSelectionRow:
    return SkillSelectionRow(
        id=skill_id,
        name=name,
        description=description,
        content=content,
        category=category,
        always_apply=False,
        is_active=True,
        embedding=None,
        reference_manifest=refs,
        has_assets=False,
    )


def build_samplelab_skill() -> tuple[str, uuid.UUID, dict[str, dict[str, Any]]]:
    """Return (skill_block, skill_id, path→file map) from the CI samplelab fixture."""
    name, description, body, _extras, files, _warnings = skill_archive_service.parse_skill_archive(
        load_samplelab_rebilling_zip_bytes()
    )
    skill_id = uuid.uuid4()
    by_path = {str(f["relative_path"]): f for f in files}
    block = skill_selection_service.format_skill_block(
        _selection_row(
            skill_id=skill_id,
            name=name,
            description=description,
            content=body,
            refs=list(by_path),
        )
    )
    return block, skill_id, by_path


def make_reference_loader(
    skill_id: uuid.UUID,
    by_path: dict[str, dict[str, Any]],
) -> Any:
    async def loader(sid: uuid.UUID, path: str) -> dict[str, Any]:
        if sid != skill_id:
            return {
                "content": f"Skill {sid} is not active for this draft",
                "is_error": True,
                "bytes": 0,
            }
        item = by_path.get(path)
        if item is None:
            return {"content": f"Reference not found: {path}", "is_error": True, "bytes": 0}
        text = item["content"].decode("utf-8", errors="replace")
        return {"content": text, "bytes": len(text), "is_error": False}

    return loader


def build_grounding_contexts(
    *,
    skills: list[str],
    cross: CrossThreadContextSchema | None,
    tone_profile: str | None,
    tone_references: list[str],
    negative_constraints: list[str],
    loaded_reference_texts: list[str],
) -> list[str]:
    """Suite B — every grounding string the generator could use (no system prompt)."""
    contexts: list[str] = []
    for block in skills:
        text = block.strip()
        if text:
            contexts.append(text)
    packed = pack_cross_thread(cross)
    if packed and packed != "(none)":
        contexts.append(packed)
    if tone_profile and tone_profile.strip():
        contexts.append(f"Tone profile:\n{tone_profile.strip()}")
    for ref in tone_references:
        if ref.strip():
            contexts.append(f"Tone reference:\n{ref.strip()}")
    for item in negative_constraints:
        if item.strip():
            contexts.append(f"Negative constraint:\n{item.strip()}")
    for ref_text in loaded_reference_texts:
        if ref_text.strip():
            contexts.append(ref_text.strip())
    return contexts


def expected_tools_from_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    return list(case.get("expected_tools") or [])


def tools_called_from_result(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for call in tool_calls:
        if call.get("is_error"):
            continue
        out.append(
            {
                "name": "read_skill_reference",
                "input_parameters": {
                    "skill_id": call.get("skill_id"),
                    "path": call.get("path"),
                },
                "path": call.get("path"),
            }
        )
    return out


async def run_case(case: dict[str, Any], *, generate: bool = True) -> EvalPayload:
    """Build Suite A/B/C payloads; optionally call live Sonnet ``generate_draft``."""
    settings = _settings_from_env()
    email = _email_from_case(case)
    triage = _triage_from_case(case)
    thread = ThreadContextSchema(
        conversation_id=email.conversation_id,
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )
    cross = build_flow_b_context(case)
    tone_profile = case.get("tone_profile")
    tone_references = list(case.get("tone_references") or [])
    negative_constraints = list(case.get("negative_constraints") or [])

    skills: list[str] = []
    reference_loader = None
    skill_id: uuid.UUID | None = None
    by_path: dict[str, dict[str, Any]] = {}
    if case.get("use_samplelab_skill"):
        block, skill_id, by_path = build_samplelab_skill()
        skills = [block]
        reference_loader = make_reference_loader(skill_id, by_path)

    input_text = scrub_text(f"{email.subject}\n\n{email.body_text}")
    actual_output = ""
    teaching_note: str | None = None
    urgency: str | None = None
    tool_calls: list[dict[str, Any]] = []
    loaded_texts: list[str] = []

    if generate:
        client = anthropic_client_from_settings(settings)
        result = await draft_llm.generate_draft(
            email,
            thread,
            triage,
            client=client,
            settings=settings,
            cross_thread_context=cross,
            tone_profile=tone_profile,
            tone_references=tone_references or None,
            skills=skills or None,
            negative_constraints=negative_constraints or None,
            reference_loader=reference_loader,
        )
        actual_output = result.draft.reply_body
        teaching_note = result.draft.teaching_note
        urgency = result.draft.urgency
        tool_calls = list(result.tool_calls)
        for call in tool_calls:
            path = call.get("path")
            if call.get("is_error") or not path or skill_id is None:
                continue
            item = by_path.get(str(path))
            if item is None:
                continue
            loaded_texts.append(item["content"].decode("utf-8", errors="replace"))
    elif case.get("expected_output"):
        actual_output = str(case["expected_output"])

    return EvalPayload(
        case_id=str(case["id"]),
        input_text=input_text,
        actual_output=actual_output,
        expected_output=(str(case["expected_output"]) if case.get("expected_output") else None),
        flow_b_chunks=flow_b_chunks(cross),
        grounding_contexts=build_grounding_contexts(
            skills=skills,
            cross=cross,
            tone_profile=tone_profile,
            tone_references=tone_references,
            negative_constraints=negative_constraints,
            loaded_reference_texts=loaded_texts,
        ),
        tools_called=tools_called_from_result(tool_calls),
        expected_tools=expected_tools_from_case(case),
        loaded_reference_paths=[
            str(c.get("path")) for c in tool_calls if c.get("path") and not c.get("is_error")
        ],
        metadata={
            "suite_tags": case.get("suite_tags", []),
            "use_samplelab_skill": bool(case.get("use_samplelab_skill")),
            "geval_criteria": case.get("geval_criteria"),
            "forbidden_facts": case.get("forbidden_facts") or [],
            "expected_facts": case.get("expected_facts") or [],
        },
        teaching_note=teaching_note,
        urgency=urgency,
    )
