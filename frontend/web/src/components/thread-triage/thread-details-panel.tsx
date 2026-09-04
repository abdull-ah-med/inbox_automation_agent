import { NotSpamButton } from "@/components/not-spam-button"
import { StatusBadge } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import type { ThreadSummary, TriageFlags } from "@/lib/types"
import { cn, textLinkClass } from "@/lib/utils"

type ThreadDetailsPanelProps = {
  thread: ThreadSummary
  threadId?: string
  triage?: TriageFlags | null
}

export const ThreadDetailsPanel = ({ thread, threadId, triage }: ThreadDetailsPanelProps) => (
  <Panel title="Thread details">
    <div className="space-y-3">
      {triage?.is_internal || triage?.is_spam != null ? (
        <div className="flex flex-wrap gap-1.5">
          {triage.is_internal ? <StatusBadge label="Internal" tone="blue" /> : null}
          {triage.is_spam != null ? (
            <StatusBadge
              label={triage.is_spam ? "Spam" : "Not spam"}
              tone={triage.is_spam ? "orange" : "green"}
            />
          ) : null}
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Category" value={thread.category ?? "—"} />
        <Field label="Messages" value={thread.message_count} />
        <Field label="Staleness" value={`${thread.staleness_hours.toFixed(1)}h`} />
        <Field label="Draft" value={thread.has_draft ? "Available" : "None"} />
      </div>
      {thread.outlook_url ? (
        <a
          href={thread.outlook_url}
          target="_blank"
          rel="noopener noreferrer"
          tabIndex={0}
          aria-label="Open thread in Outlook"
          className={cn(textLinkClass, "text-sm")}
        >
          Open in Outlook
        </a>
      ) : null}
      {threadId && (thread.state === "SPAM" || triage?.is_spam) ? (
        <NotSpamButton threadId={threadId} sender={thread.last_sender} size="sm" />
      ) : null}
    </div>
  </Panel>
)
