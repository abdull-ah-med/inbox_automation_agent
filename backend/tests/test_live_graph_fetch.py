"""Live Graph fetch test — inbox + full message body + conversation thread.

Opt-in (never runs in default CI). Prints tangible mail content so you can see
what the app actually pulls from Microsoft Graph (not just IDs in logs).

Bodies are printed twice where useful: raw Graph text and PII-scrubbed copies.

Run from backend/:

    set -a && source .env && set +a
    RUN_LIVE_GRAPH=1 .venv/bin/pytest tests/test_live_graph_fetch.py -vv -s

Optional:

    LIVE_MAX_MESSAGES=2   # default 3, max 20
"""

from __future__ import annotations

import pytest

from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from tests.live_helpers import (
    build_thread_context,
    env_flag,
    graph_to_email,
    lookback_filter,
    max_messages,
    print_divider,
    print_scrubbed_pair,
    require_graph_settings,
)

pytestmark = pytest.mark.live_graph


@pytest.fixture
def live_settings():
    if not env_flag("RUN_LIVE_GRAPH"):
        pytest.skip("Set RUN_LIVE_GRAPH=1 to run live Graph fetch tests")
    try:
        return require_graph_settings()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def live_mailbox(live_settings) -> str:
    return live_settings.mailbox_list[0]


@pytest.mark.asyncio
async def test_live_graph_fetch_inbox_bodies_and_threads(
    live_settings,
    live_mailbox: str,
) -> None:
    """Fetch recent inbox mail, print full bodies, then load each conversation thread."""
    limit = max_messages(3)
    auth = GraphAuth(live_settings, redis=None)
    client = GraphClient(auth)
    try:
        print_divider(f"GRAPH FETCH — mailbox={live_mailbox}")
        inbox = await client.list_messages(
            live_mailbox,
            folder="inbox",
            filter_query=lookback_filter(days=14),
            top=limit,
            orderby="receivedDateTime desc",
        )
        print(f"Fetched {len(inbox)} inbox message(s) (cap={limit})")
        if not inbox:
            pytest.skip("No recent inbox messages to inspect")

        for index, summary in enumerate(inbox, start=1):
            assert summary.id
            conversation_id = summary.conversation_id
            assert conversation_id, f"message {summary.id} missing conversationId"

            # Same path as ingest: get full message, then list thread by conversationId.
            full = await client.get_message(live_mailbox, summary.id)
            thread_msgs = await client.list_thread_messages(live_mailbox, conversation_id)
            if not thread_msgs:
                thread_msgs = [full]

            trigger = graph_to_email(live_mailbox, conversation_id, full)
            thread = build_thread_context(
                mailbox=live_mailbox,
                conversation_id=conversation_id,
                subject=full.subject or summary.subject or "(no subject)",
                thread_messages=thread_msgs,
            )

            print_divider(
                f"MESSAGE {index}/{len(inbox)} — thread has {len(thread.messages)} message(s)"
            )
            print_scrubbed_pair(trigger, thread)

            assert trigger.body_text is not None
            assert thread.messages
            assert any(m.message_id == trigger.message_id for m in thread.messages)

        print_divider("GRAPH FETCH COMPLETE")
        print(
            "OK — listed inbox, loaded full bodies via get_message, "
            "and expanded each conversation via list_thread_messages "
            "(client-side sort; no $orderby InefficientFilter)."
        )
    finally:
        await client.aclose()
