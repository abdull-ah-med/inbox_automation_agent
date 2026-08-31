import { NotSpamButton } from "@/components/not-spam-button"
import { StatusBadge } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import type { ThreadSummary, TriageFlags } from "@/lib/types"

type TriageHistory =
  | NonNullable<NonNullable<ThreadSummary["presentation"]>["triage_history"]>
  | undefined

type TriageHistoryPanelProps = {
  threadId: string
  thread: ThreadSummary
  triage: TriageFlags | null
}

const TriageFlagBadges = ({ triage, history }: { triage: TriageFlags; history: TriageHistory }) => (
  <div className="flex flex-wrap gap-1.5">
    {triage.is_internal ? <StatusBadge label="Internal" tone="blue" /> : null}
    {triage.is_automated ? <StatusBadge label="Automated" tone="neutral" /> : null}
    {triage.is_spam != null ? (
      <StatusBadge
        label={triage.is_spam ? "Spam" : "Not spam"}
        tone={triage.is_spam ? "orange" : "green"}
      />
    ) : null}
    {(history?.has_action_items ?? triage.has_action_items) != null ? (
      <StatusBadge
        label={
          (history?.has_action_items ?? triage.has_action_items)
            ? "Action needed (at triage)"
            : "No action (at triage)"
        }
        tone="neutral"
      />
    ) : null}
    {(history?.needs_context ?? triage.needs_context) != null ? (
      <StatusBadge
        label={
          (history?.needs_context ?? triage.needs_context)
            ? "Needed context (at triage)"
            : "Context OK (at triage)"
        }
        tone="neutral"
      />
    ) : null}
  </div>
)

const TriageHistoryFields = ({
  threadId,
  thread,
  triage,
}: {
  threadId: string
  thread: ThreadSummary
  triage: TriageFlags
}) => (
  <div className="space-y-3">
    <TriageFlagBadges triage={triage} history={thread.presentation?.triage_history} />
    {triage.action_items_summary ? (
      <Field label="Action items" value={triage.action_items_summary} />
    ) : null}
    {triage.spam_reason ? <Field label="Spam reason" value={triage.spam_reason} /> : null}
    {thread.state === "SPAM" || triage.is_spam ? (
      <NotSpamButton threadId={threadId} sender={thread.last_sender} size="sm" />
    ) : null}
    {triage.context_reason ? <Field label="Context reason" value={triage.context_reason} /> : null}
  </div>
)

export const TriageHistoryPanel = ({ threadId, thread, triage }: TriageHistoryPanelProps) => (
  <Panel title="When we triaged">
    <p className="text-muted-foreground mb-3 text-xs">
      Historical classification from triage — not current open-work status.
    </p>
    {triage ? (
      <TriageHistoryFields threadId={threadId} thread={thread} triage={triage} />
    ) : (
      <p className="text-sm text-gray-500">
        No triage result yet. Classification may still be pending.
      </p>
    )}
  </Panel>
)
