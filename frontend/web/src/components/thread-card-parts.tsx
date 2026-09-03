"use client"

import { PresentationBadges } from "@/components/presentation-badges"
import { StatusBadge, urgencyTone } from "@/components/status-badge"
import { threadPrimaryBadge } from "@/lib/thread-primary-badge"
import type { ThreadSummary } from "@/lib/types"

export const ThreadCardBadges = ({ thread }: { thread: ThreadSummary }) => {
  const presentation = thread.presentation
  const triage = thread.triage

  if (presentation?.badges_now?.length) {
    return <PresentationBadges badges={presentation.badges_now} />
  }

  return (
    <>
      <PresentationBadges badges={[threadPrimaryBadge(thread)]} />
      {thread.urgency && presentation?.urgency_active !== false ? (
        <StatusBadge label={thread.urgency} tone={urgencyTone(thread.urgency)} />
      ) : null}
      {thread.category ? <StatusBadge label={thread.category} tone="purple" /> : null}
      {triage?.is_internal ? <StatusBadge label="Internal" tone="blue" /> : null}
      {triage?.is_automated ? <StatusBadge label="Automated" tone="neutral" /> : null}
      {triage?.needs_context ? <StatusBadge label="Needs context" tone="amber" /> : null}
      {triage?.is_spam ? <StatusBadge label="Spam" tone="orange" /> : null}
    </>
  )
}

export const ThreadCardFooterMeta = ({ thread }: { thread: ThreadSummary }) => (
  <span>
    {thread.message_count} msg
    {thread.presentation?.open_work ? " · open work" : ""}
  </span>
)
