"""Live Claude Haiku triage test — Graph fetch → PII scrub → Anthropic parse.

Opt-in. Prints the exact redacted user payload sent toward Claude, then the
structured triage flags returned by Haiku.

Run from backend/:

    set -a && source .env && set +a
    RUN_LIVE_CLAUDE=1 .venv/bin/pytest tests/test_live_claude_triage.py -vv -s

Requires ANTHROPIC_API_KEY plus Graph credentials / TARGET_MAILBOXES.
"""

from __future__ import annotations

import pytest

from app.core.dependencies import anthropic_client_from_settings
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from app.llm import triage as triage_llm
from app.llm.pii_redact import scrub_email_for_llm, scrub_thread_for_llm
from tests.live_helpers import (
    build_thread_context,
    env_flag,
    graph_to_email,
    lookback_filter,
    print_divider,
    print_scrubbed_pair,
    require_anthropic_settings,
    truncate,
)

pytestmark = pytest.mark.live_claude


@pytest.fixture
def live_settings():
    if not env_flag("RUN_LIVE_CLAUDE"):
        pytest.skip("Set RUN_LIVE_CLAUDE=1 to run live Claude triage tests")
    try:
        return require_anthropic_settings()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def live_mailbox(live_settings) -> str:
    return live_settings.mailbox_list[0]


@pytest.mark.asyncio
async def test_live_claude_triage_on_real_mailbox_message(
    live_settings,
    live_mailbox: str,
) -> None:
    """Pull one real inbox message + thread, scrub, call Haiku, print triage JSON."""
    auth = GraphAuth(live_settings, redis=None)
    graph = GraphClient(auth)
    anthropic = anthropic_client_from_settings(live_settings)
    try:
        inbox = await graph.list_messages(
            live_mailbox,
            folder="inbox",
            filter_query=lookback_filter(days=14),
            top=1,
            orderby="receivedDateTime desc",
        )
        if not inbox:
            pytest.skip("No recent inbox messages to triage")

        summary = inbox[0]
        conversation_id = summary.conversation_id
        assert conversation_id

        full = await graph.get_message(live_mailbox, summary.id)
        thread_msgs = await graph.list_thread_messages(live_mailbox, conversation_id)
        if not thread_msgs:
            thread_msgs = [full]

        email = graph_to_email(live_mailbox, conversation_id, full)
        thread = build_thread_context(
            mailbox=live_mailbox,
            conversation_id=conversation_id,
            subject=full.subject or summary.subject or "(no subject)",
            thread_messages=thread_msgs,
        )

        print_scrubbed_pair(email, thread)

        scrubbed_email = scrub_email_for_llm(email)
        scrubbed_thread = scrub_thread_for_llm(thread)
        user_content = triage_llm._build_user_content(scrubbed_email, scrubbed_thread)

        print_divider("USER CONTENT SENT TO CLAUDE (redacted)")
        print(truncate(user_content, limit=6000))
        print(
            f"\nmodel={live_settings.classification_model} "
            f"max_tokens={live_settings.triage_max_tokens}"
        )

        result = await triage_llm.triage_email(
            client=anthropic,
            settings=live_settings,
            email=email,
            thread_context=thread,
        )

        triage = result.triage
        print_divider("CLAUDE HAIKU TRIAGE RESULT")
        print(f"  model:               {result.model}")
        print(f"  prompt_version:      {result.prompt_version}")
        print(f"  latency_ms:          {result.latency_ms}")
        print(f"  input_tokens:        {result.input_tokens}")
        print(f"  output_tokens:       {result.output_tokens}")
        print(f"  is_spam:             {triage.is_spam}")
        print(f"  spam_reason:         {triage.spam_reason!r}")
        print(f"  has_action_items:    {triage.has_action_items}")
        print(f"  action_items_summary:{triage.action_items_summary!r}")
        print(f"  needs_context:       {triage.needs_context}")
        print(f"  context_reason:      {triage.context_reason!r}")
        print_divider("CLAUDE TRIAGE COMPLETE")

        assert isinstance(triage.is_spam, bool)
        assert isinstance(triage.has_action_items, bool)
        assert isinstance(triage.needs_context, bool)
        assert result.model == live_settings.classification_model
        assert result.latency_ms >= 0
    finally:
        await graph.aclose()
        await anthropic.close()
