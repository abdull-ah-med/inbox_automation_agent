"""Post-ingest pipeline — compatibility barrel.

Callers keep importing ``app.services.pipeline_service``. Implementation lives
in ``app.services.pipeline``.
"""

from __future__ import annotations

from app.services.pipeline.service import (  # noqa: F401
    _allowlisted_senders,
    _invalidate_chat_cache_mailbox,
    _phased_finalize_draft_and_slack,
    _post_slack_card_after_commit,
    _resolve_graph_client,
    _run_phased_post_ingest,
    _select_original_email,
    _summarize_non_spam,
    pipeline_ready_for_dedup,
    run_after_ingest,
    run_phased_after_ingest,
    run_post_ingest_triage,
    slack_review_card_required,
)
