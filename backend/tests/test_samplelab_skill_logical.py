"""Logical contract tests for Elise's samplelab-rebilling Claude Skill.

These assert Claude-style progressive disclosure as adapted in this app:

Level 1 — Discovery: selector sees name + description only (not SKILL.md body,
           not reference file contents).
Level 2 — Activation: after selection, draft prompt gets full SKILL.md body +
           a reference *manifest* (paths only).
Level 3 — Execution: reference bytes enter context only via read_skill_reference.

Also asserts packaging of the samplelab-rebilling archive fixture (CI builds it
in-memory; prefers ``misc/samplelab-rebilling-skill.zip`` when present locally)
and selection precision (billing match vs scheduling miss).

Live opt-in (real Anthropic Haiku + Sonnet):

    cd backend
    set -a && source .env && set +a
    RUN_LIVE_SKILL=1 .venv/bin/pytest tests/test_samplelab_skill_logical.py -vv -s -k live
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.dependencies import anthropic_client_from_settings
from app.llm import draft_generator as draft_llm
from app.llm import skill_selector
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.repositories.skill_repo import SkillSelectionRow
from app.services import skill_archive_service, skill_embedding_service, skill_selection_service
from app.services.skill_reference_service import (
    PER_DRAFT_REFERENCE_BUDGET,
    load_skill_reference,
)
from tests.fixtures.samplelab_rebilling_archive import load_samplelab_rebilling_zip_bytes
from tests.live_helpers import env_flag

EXPECTED_REFS = {
    "references/client_rules.md",
    "references/output_format.md",
    "references/harmeyer_departments.csv",
}


def _settings(**overrides: object) -> Settings:
    base = {
        "environment": "local",
        "jwt_secret": "c" * 64,
        "frontend_origin": "http://localhost:3000",
        "cookie_secure": False,
        "anthropic_api_key": "sk-ant-test",
        "draft_model": "claude-sonnet-4-6",
        "classification_model": "claude-haiku-4-5-20251001",
        "database_url": "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        "redis_url": "redis://localhost:6379/15",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _parse_fixture():
    return skill_archive_service.parse_skill_archive(load_samplelab_rebilling_zip_bytes())


def _selection_row(
    *,
    skill_id: uuid.UUID | None = None,
    name: str,
    description: str,
    content: str,
    refs: list[str],
    category: str = "billing",
) -> SkillSelectionRow:
    return SkillSelectionRow(
        id=skill_id or uuid.uuid4(),
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


def _email(*, subject: str, body: str) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m-samplelab-1",
        conversation_id="c-samplelab-1",
        mailbox="elise@example.com",
        sender="accounting@client.com",
        subject=subject,
        body_text=body,
        body_preview=body[:120],
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )


def _billing_triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        needs_context=False,
        routing_category="billing",
        action_items_summary="Process SampleLab rebilling",
    )


def _scheduling_triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        needs_context=False,
        routing_category="scheduling",
        action_items_summary="Schedule drug screen",
    )


# ---------------------------------------------------------------------------
# Packaging / Level 1–3 offline contract
# ---------------------------------------------------------------------------


def test_fixture_packages_like_claude_agent_skill() -> None:
    """Anthropic packaging: root folder == name, SKILL.md frontmatter, refs only."""
    name, description, body, extras, files, warnings = _parse_fixture()
    assert name == "samplelab-rebilling"
    assert "samplelab" in description.lower()
    assert "rebill" in description.lower()
    # Description is the discovery gate — must carry trigger vocabulary.
    assert "Invoicing_Detail" in description or "process the samplelab" in description.lower()
    assert body.strip()
    assert "references/output_format.md" in body
    assert warnings == []
    paths = {str(f["relative_path"]) for f in files}
    assert paths == EXPECTED_REFS
    assert all(f["kind"] == "reference" for f in files)
    # No scripts shipped in Elise's package (and none should ever execute).
    assert not any(str(f["relative_path"]).startswith("scripts/") for f in files)


def test_level1_discovery_embedding_uses_metadata_plus_body_head_only() -> None:
    """Retrieval signal ≈ Claude discovery: name + description + lean body head."""
    name, description, body, *_rest = _parse_fixture()
    source = skill_embedding_service.build_embedding_source(
        name=name,
        description=description,
        body=body,
    )
    assert source.startswith(name)
    assert description.strip() in source
    # First ~400 body chars only — late workflow steps must not dominate retrieval.
    assert body.strip()[:400] in source
    assert "Step 7" not in source
    assert len(body) > 400


def test_level1_selector_prompt_exposes_name_description_not_body() -> None:
    """Haiku candidate list is id|name|description — never SKILL.md body."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    skill_id = uuid.uuid4()
    row = _selection_row(
        skill_id=skill_id,
        name=name,
        description=description,
        content=body,
        refs=[str(f["relative_path"]) for f in files],
    )
    schema = skill_selection_service._to_selector_schema(row)
    user = skill_selector._build_user_content(
        subject="SampleLab rebilling for Harmeyer",
        body_preview="Please process the samplelab invoice and rebill Harmeyer.",
        triage=_billing_triage(),
        candidates=[schema],
    )
    assert str(skill_id) in user
    assert name in user
    assert "process the samplelab" in user.lower() or "samplelab" in user.lower()
    # Body instructions must not leak into discovery.
    assert "FULL MONTHLY WORKFLOW" not in user
    assert "Step 1 — Load Invoice" not in user
    # Reference *contents* must not leak; paths may appear in description ref note.
    client_rules = next(
        f["content"].decode() for f in files if f["relative_path"] == "references/client_rules.md"
    )
    assert "Correct spelling: Trolinder" in client_rules
    assert "Correct spelling: Trolinder" not in user


def test_level2_activation_injects_body_and_manifest_not_reference_bytes() -> None:
    """After selection, draft sees SKILL.md + path list; not client_rules body."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    refs = [str(f["relative_path"]) for f in files]
    row = _selection_row(
        name=name,
        description=description,
        content=body,
        refs=refs,
    )
    block = skill_selection_service.format_skill_block(row)
    assert f"## Skill: {name}" in block
    assert "FULL MONTHLY WORKFLOW" in block
    assert "### Available reference files" in block
    for path in EXPECTED_REFS:
        assert path in block
    assert "read_skill_reference" in block

    for f in files:
        text = f["content"].decode("utf-8", errors="replace")
        # Distinctive content from each reference must stay out of activation.
        if f["relative_path"] == "references/client_rules.md":
            assert "Correct spelling: Trolinder" in text
            assert "Correct spelling: Trolinder" not in block
        if f["relative_path"] == "references/output_format.md":
            assert "Collection Date" in text and "SSN Last 4" in text
            assert "SSN Last 4" not in block
        if f["relative_path"] == "references/harmeyer_departments.csv":
            assert "Trolinder" in text or "Alvis" in text
            assert "Randy,Alvis,Maintenance" not in block


@pytest.mark.asyncio
async def test_level3_loader_returns_real_reference_bytes_on_demand() -> None:
    """Execution phase: tool returns file contents; unknown skill_id is rejected."""
    _name, _desc, _body, _extras, files, _warnings = _parse_fixture()
    skill_id = uuid.uuid4()
    by_path = {str(f["relative_path"]): f for f in files}

    async def fake_get_by_path(session, *, skill_id, relative_path):  # noqa: ANN001
        _ = session, skill_id
        item = by_path.get(relative_path)
        if item is None:
            return None
        row = MagicMock()
        row.kind = item["kind"]
        row.size_bytes = len(item["content"])  # type: ignore[arg-type]
        row.mime_type = item["mime_type"]
        row.content = item["content"]
        return row

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)

    with patch(
        "app.services.skill_reference_service.skill_files_repo.get_by_path",
        AsyncMock(side_effect=fake_get_by_path),
    ):
        loaded = await load_skill_reference(
            skill_id=skill_id,
            path="references/client_rules.md",
            active_skill_ids={skill_id},
            session_factory=factory,
        )
        assert loaded["is_error"] is False
        assert "Trolinder" in loaded["content"]
        assert loaded["bytes"] > 0
        assert loaded["bytes"] <= PER_DRAFT_REFERENCE_BUDGET

        denied = await load_skill_reference(
            skill_id=uuid.uuid4(),
            path="references/client_rules.md",
            active_skill_ids={skill_id},
            session_factory=factory,
        )
        assert denied["is_error"] is True
        # First call opened a session; second (unknown skill) must not.
        assert factory.call_count == 1


@pytest.mark.asyncio
async def test_selection_funnel_keeps_skill_out_of_wrong_category_pool() -> None:
    """Scheduling triage must not pull billing-only samplelab skill from category pool."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    skill = _selection_row(
        name=name,
        description=description,
        content=body,
        refs=[str(f["relative_path"]) for f in files],
        category="billing",
    )
    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[skill]),
        ),
        patch(
            "app.services.skill_selection_service.skill_selector.select_skills",
            AsyncMock(),
        ) as selector,
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        selected = await skill_selection_service.select_skills(
            AsyncMock(),
            client=MagicMock(),
            settings=_settings(),
            openai_client=None,
            email=_email(
                subject="Schedule quarterly screens",
                body="Please schedule the Livingston Parish drug screen pool.",
            ),
            triage=_scheduling_triage(),
        )
    # Category pool empty for scheduling → Haiku never consulted; skill not applied.
    selector.assert_not_awaited()
    assert selected.skill_ids == []
    assert selected.blocks == []


@pytest.mark.asyncio
async def test_selection_funnel_offers_skill_to_haiku_for_billing_email() -> None:
    """Billing triage offers samplelab skill to Haiku; selected id enters draft blocks."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    skill_id = uuid.uuid4()
    skill = _selection_row(
        skill_id=skill_id,
        name=name,
        description=description,
        content=body,
        refs=[str(f["relative_path"]) for f in files],
    )
    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[skill]),
        ),
        patch(
            "app.services.skill_selection_service.skill_selector.select_skills",
            AsyncMock(return_value=[skill_id]),
        ) as selector,
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        selected = await skill_selection_service.select_skills(
            AsyncMock(),
            client=MagicMock(),
            settings=_settings(),
            openai_client=None,
            email=_email(
                subject="Process SampleLab invoice / rebill Harmeyer",
                body=(
                    "Please process the samplelab invoice and rebill Harmeyer "
                    "Transport for last month's screenings."
                ),
            ),
            triage=_billing_triage(),
        )
    selector.assert_awaited_once()
    # Candidates passed to Haiku are schemas with description (+ ref note), not raw body.
    cand_arg = selector.await_args.kwargs["candidates"]
    assert len(cand_arg) == 1
    assert cand_arg[0].id == skill_id
    assert "refs:" in (cand_arg[0].description or "")
    assert skill_id in selected.skill_ids
    assert any("samplelab-rebilling" in b for b in selected.blocks)
    assert any("references/client_rules.md" in b for b in selected.blocks)


@pytest.mark.asyncio
async def test_draft_tool_loop_loads_client_rules_then_uses_content() -> None:
    """Sonnet tool_use → tool_result with Elise client_rules → final draft."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    skill_id = uuid.uuid4()
    client_rules = next(
        f["content"].decode()
        for f in files
        if f["relative_path"] == "references/client_rules.md"
    )
    block = skill_selection_service.format_skill_block(
        _selection_row(
            skill_id=skill_id,
            name=name,
            description=description,
            content=body,
            refs=[str(f["relative_path"]) for f in files],
        )
    )

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tu-client-rules"
    tool_block.name = "read_skill_reference"
    tool_block.input = {
        "skill_id": str(skill_id),
        "path": "references/client_rules.md",
    }
    tool_resp = MagicMock(
        stop_reason="tool_use",
        content=[tool_block],
        usage=MagicMock(input_tokens=20, output_tokens=10),
    )
    end_resp = MagicMock(
        stop_reason="end_turn",
        content=[MagicMock(type="text", text="ok")],
        usage=MagicMock(input_tokens=10, output_tokens=5),
    )
    from app.models.schemas.draft import DraftSchema

    parsed = MagicMock()
    parsed.parsed_output = DraftSchema(
        subject_line="Re: Process SampleLab invoice / rebill Harmeyer",
        reply_body=(
            "We'll rebill Harmeyer Transport with department tabs "
            "(Tank / HDV / Maintenance / Admin) and correct Trolinder spelling."
        ),
        suggested_recipients=[],
        forward_to=None,
        teaching_note="SampleLab rebilling for Harmeyer; applied client rules.",
        urgency="NORMAL",
        urgency_reason="Monthly billing workflow.",
    )
    parsed.usage = MagicMock(input_tokens=30, output_tokens=40)

    async def loader(sid: uuid.UUID, path: str) -> dict:
        assert sid == skill_id
        assert path == "references/client_rules.md"
        return {"content": client_rules, "bytes": len(client_rules), "is_error": False}

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[tool_resp, end_resp])
    client.messages.parse = AsyncMock(return_value=parsed)

    email = _email(
        subject="Process SampleLab invoice / rebill Harmeyer",
        body="Please process the samplelab invoice and rebill Harmeyer Transport.",
    )
    result = await draft_llm.generate_draft(
        email,
        ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        _billing_triage(),
        client=client,
        settings=_settings(anthropic_api_key="sk-ant-real-enough"),
        skills=[block],
        reference_loader=loader,
    )

    assert result.tool_calls[0]["path"] == "references/client_rules.md"
    assert result.tool_calls[0]["is_error"] is False
    # tool_result fed back into the conversation contains Elise's rules
    second_msgs = client.messages.create.await_args_list[1].kwargs["messages"]
    tool_user = next(
        m for m in second_msgs if m["role"] == "user" and isinstance(m["content"], list)
    )
    assert "Trolinder" in tool_user["content"][0]["content"]
    assert "Harmeyer" in result.draft.reply_body
    assert client.messages.parse.await_count == 1


# ---------------------------------------------------------------------------
# Live Anthropic — proves models behave as intended with Elise's skill
# ---------------------------------------------------------------------------


@pytest.fixture
def live_skill_settings() -> Settings:
    if not env_flag("RUN_LIVE_SKILL"):
        pytest.skip("Set RUN_LIVE_SKILL=1 to run live Elise skill logical tests")
    settings = Settings()
    if not settings.anthropic_api_key.strip():
        pytest.skip("ANTHROPIC_API_KEY required in .env for RUN_LIVE_SKILL")
    return settings


@pytest.mark.asyncio
async def test_live_haiku_selects_samplelab_for_rebilling_email(
    live_skill_settings: Settings,
) -> None:
    """Real Haiku: matching billing email → selects samplelab-rebilling."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    skill_id = uuid.uuid4()
    decoy_id = uuid.uuid4()
    candidates = [
        skill_selection_service._to_selector_schema(
            _selection_row(
                skill_id=skill_id,
                name=name,
                description=description,
                content=body,
                refs=[str(f["relative_path"]) for f in files],
            )
        ),
        skill_selection_service._to_selector_schema(
            _selection_row(
                skill_id=decoy_id,
                name="interview-scheduling",
                description="Use when scheduling candidate interviews or site visits.",
                content="Always propose three time slots.",
                refs=[],
                category="scheduling",
            )
        ),
    ]
    client = anthropic_client_from_settings(live_skill_settings)
    selected = await skill_selector.select_skills(
        client=client,
        settings=live_skill_settings,
        subject="Process SampleLab invoice and rebill Harmeyer",
        body_preview=(
            "Hi Elise — please process the samplelab invoice from SampleLabVendor and "
            "rebill Harmeyer Transport for last month. Build the detail files."
        ),
        triage=_billing_triage(),
        candidates=candidates,
    )
    print(f"\nLIVE Haiku selected: {selected}")
    assert skill_id in selected, (
        "Haiku should select samplelab-rebilling for an explicit SampleLab rebilling ask"
    )
    assert decoy_id not in selected


@pytest.mark.asyncio
async def test_live_haiku_rejects_samplelab_for_unrelated_scheduling_email(
    live_skill_settings: Settings,
) -> None:
    """Real Haiku: precision — unrelated scheduling email must not select skill."""
    name, description, body, _extras, files, _warnings = _parse_fixture()
    skill_id = uuid.uuid4()
    candidates = [
        skill_selection_service._to_selector_schema(
            _selection_row(
                skill_id=skill_id,
                name=name,
                description=description,
                content=body,
                refs=[str(f["relative_path"]) for f in files],
            )
        ),
    ]
    client = anthropic_client_from_settings(live_skill_settings)
    selected = await skill_selector.select_skills(
        client=client,
        settings=live_skill_settings,
        subject="Interview availability next week",
        body_preview=(
            "Can we schedule a 30-minute intro call with the new vendor contact "
            "sometime Tuesday or Wednesday afternoon?"
        ),
        triage=_scheduling_triage(),
        candidates=candidates,
    )
    print(f"\nLIVE Haiku selected (expect empty): {selected}")
    assert skill_id not in selected


@pytest.mark.asyncio
async def test_live_sonnet_reads_client_rules_for_harmeyer_draft(
    live_skill_settings: Settings,
) -> None:
    """Real Sonnet: given Elise skill + Harmeyer ask, calls read_skill_reference.

    Uses an in-memory loader backed by the real zip (same bytes the DB would hold)
    so we prove the tool-loop contract without requiring a migrated DB.

    Full SKILL.md activation (~10 KB) plus tool rounds can take 1–3 minutes.
    """
    import asyncio

    name, description, body, _extras, files, _warnings = _parse_fixture()
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

    async def loader(sid: uuid.UUID, path: str) -> dict:
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

    email = _email(
        subject="Harmeyer SampleLab rebilling — department tabs",
        body=(
            "Please process the samplelab invoice and rebill Harmeyer Transport. "
            "Confirm department tabs and the Trolinder spelling before you reply "
            "with the plan for the monthly detail file."
        ),
    )
    client = anthropic_client_from_settings(live_skill_settings)
    result = await asyncio.wait_for(
        draft_llm.generate_draft(
            email,
            ThreadContextSchema(
                conversation_id=email.conversation_id,
                mailbox=email.mailbox,
                subject=email.subject,
                messages=[email],
            ),
            _billing_triage(),
            client=client,
            settings=live_skill_settings,
            skills=[block],
            reference_loader=loader,
        ),
        timeout=300,
    )

    print("\nLIVE Sonnet tool_calls:", result.tool_calls)
    print("LIVE Sonnet reply_body:\n", result.draft.reply_body)
    print("LIVE teaching_note:\n", result.draft.teaching_note)

    paths_read = {c["path"] for c in result.tool_calls if not c.get("is_error")}
    assert paths_read & EXPECTED_REFS, (
        "Sonnet should load at least one Elise reference via read_skill_reference "
        f"(got {result.tool_calls})"
    )
    # Prefer client_rules for Harmeyer-specific ask; allow output_format as alternate.
    assert (
        "references/client_rules.md" in paths_read
        or "references/harmeyer_departments.csv" in paths_read
        or "references/output_format.md" in paths_read
    )
    lowered = result.draft.reply_body.lower()
    assert "harmeyer" in lowered
    assert any(
        token in lowered
        for token in ("department", "tab", "rebill", "samplelab", "detail", "trolinder")
    )
