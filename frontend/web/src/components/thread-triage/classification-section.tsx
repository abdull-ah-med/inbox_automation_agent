import { SuggestedProcessPanel } from "@/components/thread-triage/suggested-process-panel"
import { ThreadDetailsPanel } from "@/components/thread-triage/thread-details-panel"
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
    <ThreadDetailsPanel threadId={threadId} thread={thread} triage={triage} />
  </div>
)
