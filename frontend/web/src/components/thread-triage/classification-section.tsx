import { NotSpamButton } from "@/components/not-spam-button"
import { StatusBadge } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import type { DraftView, ThreadSummary, TriageFlags } from "@/lib/types"
import { cn, textLinkClass } from "@/lib/utils"

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
}: ClassificationSectionProps) => {
  const history = thread.presentation?.triage_history

  return (
    <div className="space-y-4">
      <Panel title="Suggested Process">
        {suggestedActions.length > 0 ? (
          <ol className="space-y-3">
            {suggestedActions
              .slice()
              .sort((a, b) => a.step - b.step)
              .map((item) => (
                <li key={`${item.step}-${item.action}`} className="flex gap-3">
                  <span
                    className="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                    aria-hidden="true"
                  >
                    {item.step}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {item.action}
                    </p>
                    {item.stakeholder ? (
                      <span className="mt-1 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                        {item.stakeholder}
                      </span>
                    ) : null}
                    <p className="mt-1 text-xs text-gray-500">{item.rationale}</p>
                  </div>
                </li>
              ))}
          </ol>
        ) : (
          <p className="text-sm text-gray-500">
            No suggested process. This thread hasn&apos;t produced a draft.
          </p>
        )}
      </Panel>

      <Panel title="When we triaged">
        <p className="mb-3 text-xs text-muted-foreground">
          Historical classification from triage — not current open-work status.
        </p>
        {triage ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {triage.is_internal ? (
                <StatusBadge label="Internal" tone="blue" />
              ) : null}
              {triage.is_automated ? (
                <StatusBadge label="Automated" tone="neutral" />
              ) : null}
              {triage.is_spam != null ? (
                <StatusBadge
                  label={triage.is_spam ? "Spam" : "Not spam"}
                  tone={triage.is_spam ? "red" : "green"}
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
            {triage.action_items_summary ? (
              <Field label="Action items" value={triage.action_items_summary} />
            ) : null}
            {triage.spam_reason ? (
              <Field label="Spam reason" value={triage.spam_reason} />
            ) : null}
            {thread.state === "SPAM" || triage.is_spam ? (
              <NotSpamButton
                threadId={threadId}
                sender={thread.last_sender}
                size="sm"
              />
            ) : null}
            {triage.context_reason ? (
              <Field label="Context reason" value={triage.context_reason} />
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            No triage result yet. Classification may still be pending.
          </p>
        )}
      </Panel>

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
    </div>
  )
}
