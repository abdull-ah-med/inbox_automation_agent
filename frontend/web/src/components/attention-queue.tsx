"use client"

import Link from "next/link"

import { EmptyState } from "@/components/empty-state"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { formatRelativeTime, inboxLabel } from "@/lib/design-tokens"
import type { ThreadSummary } from "@/lib/types"

export const AttentionQueue = ({ threads }: { threads: ThreadSummary[] }) => {
  if (threads.length === 0) {
    return (
      <EmptyState
        title="Inbox clear"
        description="Nothing is awaiting action. New threads will show up here with urgency and context flags."
        className="border-0 bg-transparent px-0 py-6 ring-0"
      />
    )
  }

  return (
    <ul className="divide-y divide-gray-100 dark:divide-gray-800">
      {threads.map((thread) => (
        <li key={thread.id}>
          <Link
            href={`/threads/${thread.id}`}
            className="focus-visible:ring-ring block min-w-0 py-3 outline-none transition-colors hover:bg-gray-50 focus-visible:ring-2 dark:hover:bg-gray-800/50"
            aria-label={`Review ${thread.subject || "thread"}`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs font-medium text-gray-500">
                {inboxLabel(thread.mailbox_key)}
              </span>
              <StatusBadge label={stateLabel(thread.state)} tone={stateTone(thread.state)} />
              {thread.urgency ? (
                <StatusBadge
                  label={thread.urgency}
                  tone={urgencyTone(thread.urgency)}
                />
              ) : null}
              {thread.triage?.is_internal ? (
                <StatusBadge label="Internal" tone="blue" />
              ) : null}
              {thread.triage?.is_automated ? (
                <StatusBadge label="Automated" tone="neutral" />
              ) : null}
              {thread.triage?.needs_context ? (
                <StatusBadge label="Needs context" tone="amber" />
              ) : null}
            </div>
            <p className="mt-1 truncate text-sm font-medium text-gray-900 dark:text-gray-100">
              {thread.subject || "(no subject)"}
            </p>
            <p className="mt-0.5 truncate text-xs text-gray-500">
              {thread.last_sender ?? "Unknown"} ·{" "}
              {formatRelativeTime(thread.last_message_at)}
            </p>
            {thread.teaching_note ? (
              <p className="mt-1.5 line-clamp-2 min-w-0 text-xs text-gray-500 dark:text-gray-400">
                {thread.teaching_note}
              </p>
            ) : null}
          </Link>
        </li>
      ))}
    </ul>
  )
}
