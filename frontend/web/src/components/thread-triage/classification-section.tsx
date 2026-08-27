import { SuggestedProcessPanel } from "@/components/thread-triage/suggested-process-panel"
import { ThreadDetailsPanel } from "@/components/thread-triage/thread-details-panel"
import { TriageHistoryPanel } from "@/components/thread-triage/triage-history-panel"
import type { DraftView, ThreadSummary, TriageFlags } from "@/lib/types"

type ClassificationSectionProps = {
  threadId: string
  thread: ThreadSummary
  triage: TriageFlags | null
  suggestedActions: NonNullable<DraftView["suggested_actions"]>
}

export const ClassificationSection = ({
  threadId,
  thread,
  triage,
  suggestedActions,
}: ClassificationSectionProps) => (
  <div className="space-y-4">
    <SuggestedProcessPanel suggestedActions={suggestedActions} />
    <TriageHistoryPanel threadId={threadId} thread={thread} triage={triage} />
    <ThreadDetailsPanel thread={thread} />
  </div>
)
