import { Field, Panel } from "@/components/thread-triage/panel"
import type { ThreadSummary } from "@/lib/types"
import { cn, textLinkClass } from "@/lib/utils"

type ThreadDetailsPanelProps = {
  thread: ThreadSummary
}

export const ThreadDetailsPanel = ({ thread }: ThreadDetailsPanelProps) => (
  <Panel title="Thread details">
    <div className="space-y-3">
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
    </div>
  </Panel>
)
