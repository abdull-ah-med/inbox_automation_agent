"use client"

import Link from "next/link"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import {
  formatRelativeTime,
  inboxColor,
  inboxLabel,
} from "@/lib/design-tokens"
import type { ThreadSummary } from "@/lib/types"

export const ThreadCard = ({
  thread,
  selected = false,
}: {
  thread: ThreadSummary
  selected?: boolean
}) => {
  const color = inboxColor(thread.mailbox_key)
  const label = inboxLabel(thread.mailbox_key)
  const triage = thread.triage

  const shellClass = selected
    ? "w-full min-w-0 rounded-lg border border-blue-500 bg-blue-50 dark:bg-blue-950/30"
    : "w-full min-w-0 rounded-lg border border-gray-200 bg-white transition-colors hover:border-gray-300 dark:border-gray-700 dark:bg-gray-900 dark:hover:border-gray-600"

  return (
    <div className={shellClass}>
      <Link
        href={`/threads/${thread.id}`}
        aria-label={`Open thread ${thread.subject || "untitled"}`}
        className="focus-visible:ring-ring block cursor-pointer p-4 pb-2 text-left outline-none focus-visible:ring-2"
      >
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span
            className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
            style={{ backgroundColor: color }}
          >
            {label}
          </span>
          <StatusBadge label={stateLabel(thread.state)} tone={stateTone(thread.state)} />
          {thread.urgency ? (
            <StatusBadge label={thread.urgency} tone={urgencyTone(thread.urgency)} />
          ) : null}
          {thread.category ? <StatusBadge label={thread.category} tone="purple" /> : null}
          {triage?.is_spam ? <StatusBadge label="Spam" tone="red" /> : null}
          {triage?.needs_context ? (
            <StatusBadge label="Needs context" tone="amber" />
          ) : null}
        </div>

        <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
          {thread.subject || "(no subject)"}
        </p>
        <p className="mt-1 truncate text-xs text-gray-500 dark:text-gray-400">
          {thread.last_sender ?? "Unknown sender"}
          {thread.preview ? ` — ${thread.preview}` : ""}
        </p>
      </Link>

      <div className="flex items-center justify-between gap-2 px-4 pb-3 text-xs text-gray-400">
        <span>
          {thread.message_count} msg
          {triage?.has_action_items ? " · action needed" : ""}
        </span>
        <div className="flex items-center gap-3">
          {thread.outlook_url ? (
            <a
              href={thread.outlook_url}
              target="_blank"
              rel="noopener noreferrer"
              tabIndex={0}
              aria-label={`Open thread in Outlook: ${thread.subject || "untitled"}`}
              className="cursor-pointer text-blue-600 hover:underline dark:text-blue-400"
            >
              Outlook
            </a>
          ) : null}
          <span>{formatRelativeTime(thread.last_message_at)}</span>
        </div>
      </div>
    </div>
  )
}
