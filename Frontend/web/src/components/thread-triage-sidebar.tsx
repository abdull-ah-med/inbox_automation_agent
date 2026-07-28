"use client"

import { useState } from "react"

import { EmailBody } from "@/components/email-body"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { formatEventName, formatRelativeTime } from "@/lib/design-tokens"
import type {
  AuditEntry,
  ClassificationView,
  DraftView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

const Panel = ({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) => {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
      <h3 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
        {title}
      </h3>
      {children}
    </section>
  )
}

const Field = ({
  label,
  value,
}: {
  label: string
  value: React.ReactNode
}) => {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <div className="mt-0.5 text-sm text-gray-900 dark:text-gray-100">{value}</div>
    </div>
  )
}

export const ThreadTriageSidebar = ({
  thread,
  classification,
  draft,
  triage,
  auditLog,
}: {
  thread: ThreadSummary
  classification: ClassificationView | null
  draft: DraftView | null
  triage: TriageFlags | null
  auditLog: AuditEntry[]
}) => {
  const [showAudit, setShowAudit] = useState(false)

  const handleToggleAudit = () => {
    setShowAudit((value) => !value)
  }

  const handleAuditKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggleAudit()
    }
  }

  const teachingNote = thread.teaching_note ?? draft?.teaching_note ?? null
  const urgency = thread.urgency ?? draft?.urgency ?? classification?.urgency ?? null
  const urgencyReason = draft?.urgency_reason ?? null

  return (
    <div className="space-y-4">
      <Panel title="Insights">
        <div className="space-y-4">
          <div>
            <p className="text-xs text-gray-400">Teaching note</p>
            {teachingNote ? (
              <EmailBody
                text={teachingNote}
                className="mt-1 rounded-md bg-blue-50 px-3 py-2 text-gray-800 dark:bg-blue-950/30 dark:text-gray-100"
              />
            ) : (
              <p className="mt-1 text-sm text-gray-400 italic">
                No teaching note yet — this thread hasn&apos;t produced a draft.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="State"
              value={
                <StatusBadge label={stateLabel(thread.state)} tone={stateTone(thread.state)} />
              }
            />
            <Field
              label="Urgency"
              value={
                urgency ? (
                  <div className="space-y-1">
                    <StatusBadge label={urgency} tone={urgencyTone(urgency)} />
                    {urgencyReason ? (
                      <p className="text-xs text-gray-500">{urgencyReason}</p>
                    ) : null}
                  </div>
                ) : (
                  "—"
                )
              }
            />
          </div>
        </div>
      </Panel>

      <Panel title="Triage">
        {triage ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {triage.is_spam != null ? (
                <StatusBadge
                  label={triage.is_spam ? "Spam" : "Not spam"}
                  tone={triage.is_spam ? "red" : "green"}
                />
              ) : null}
              {triage.has_action_items != null ? (
                <StatusBadge
                  label={triage.has_action_items ? "Action needed" : "No action"}
                  tone={triage.has_action_items ? "amber" : "neutral"}
                />
              ) : null}
              {triage.needs_context != null ? (
                <StatusBadge
                  label={triage.needs_context ? "Needs context" : "Context OK"}
                  tone={triage.needs_context ? "amber" : "green"}
                />
              ) : null}
            </div>
            {triage.action_items_summary ? (
              <Field label="Action items" value={triage.action_items_summary} />
            ) : null}
            {triage.spam_reason ? (
              <Field label="Spam reason" value={triage.spam_reason} />
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
              className="inline-block cursor-pointer text-sm text-blue-600 hover:underline dark:text-blue-400"
            >
              Open in Outlook
            </a>
          ) : null}
        </div>
      </Panel>

      <Panel title="Draft reply">
        {draft ? (
          <div className="space-y-3">
            <Field label="Subject" value={draft.subject} />
            <div>
              <p className="text-xs text-gray-400">Body</p>
              <div className="mt-1 overflow-auto">
                <EmailBody text={draft.body} />
              </div>
            </div>
            {draft.forward_to ? (
              <Field label="Forward to" value={draft.forward_to} />
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-gray-500">No draft generated for this thread.</p>
        )}
      </Panel>

      <Panel title="Audit">
        <button
          type="button"
          tabIndex={0}
          aria-expanded={showAudit}
          aria-label="Toggle audit log"
          onClick={handleToggleAudit}
          onKeyDown={handleAuditKeyDown}
          className="cursor-pointer text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          {showAudit ? "Hide audit log" : `Show audit log (${auditLog.length})`}
        </button>
        {showAudit ? (
          <ul className="mt-3 space-y-2">
            {auditLog.length === 0 ? (
              <li className="text-sm text-gray-500">No audit events.</li>
            ) : (
              auditLog.map((entry, index) => (
                <li
                  key={`${entry.timestamp}-${index}`}
                  className="rounded border border-gray-100 p-2 dark:border-gray-800"
                >
                  <div className="flex justify-between gap-2">
                    <span className="text-xs font-medium">
                      {formatEventName(entry.event)}
                    </span>
                    <span className="text-xs text-gray-400">
                      {formatRelativeTime(entry.timestamp)}
                    </span>
                  </div>
                  {entry.detail ? (
                    <p className="mt-1 line-clamp-3 text-xs text-gray-500">
                      {entry.detail}
                    </p>
                  ) : null}
                </li>
              ))
            )}
          </ul>
        ) : null}
      </Panel>
    </div>
  )
}
