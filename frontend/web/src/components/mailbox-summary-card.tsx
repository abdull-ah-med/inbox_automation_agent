"use client"

import Link from "next/link"

import { CardMount } from "@/components/motion"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import {
  formatRelativeTime,
  inboxColor,
  inboxLabel,
} from "@/lib/design-tokens"
import type { MailboxOverview } from "@/lib/types"

export const MailboxSummaryCard = ({
  mailbox,
  index = 0,
}: {
  mailbox: MailboxOverview
  index?: number
}) => {
  const color = inboxColor(mailbox.mailbox)
  const label = mailbox.label || inboxLabel(mailbox.mailbox)
  const isEmpty = mailbox.thread_count === 0

  return (
    <CardMount index={index}>
      <Link
        href={`/mailboxes/${encodeURIComponent(mailbox.mailbox)}`}
        aria-label={`Open ${label} inbox`}
        className="flex h-full flex-col rounded-lg border border-gray-200 bg-white p-4 transition-colors hover:border-gray-300 dark:border-gray-700 dark:bg-gray-900 dark:hover:border-gray-600"
      >
        <div className="flex items-start justify-between gap-2">
          <span
            className="inline-block rounded-full px-2 py-0.5 text-xs font-medium text-white"
            style={{ backgroundColor: color }}
          >
            {label}
          </span>
          {mailbox.stale_count > 0 ? (
            <StatusBadge label={`${mailbox.stale_count} stale`} tone="amber" />
          ) : null}
        </div>

        {mailbox.email_address ? (
          <p className="mt-2 truncate text-xs text-gray-500 dark:text-gray-400">
            {mailbox.email_address}
          </p>
        ) : null}

        <p className="mt-3 text-2xl font-semibold text-gray-900 dark:text-gray-100">
          {mailbox.awaiting_action_count}
          <span className="ml-1.5 text-sm font-normal text-gray-500">
            awaiting action
          </span>
        </p>
        <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
          {mailbox.thread_count} total
          {mailbox.stale_count > 0 ? ` · ${mailbox.stale_count} stale` : ""}
          {mailbox.filtered_count > 0
            ? ` · ${mailbox.filtered_count} filtered as spam/no action`
            : ""}
        </p>

        <div className="mt-4 flex-1 border-t border-gray-100 pt-3 dark:border-gray-800">
          {isEmpty ? (
            <p className="text-sm text-gray-400">
              No mail ingested for this inbox yet.
            </p>
          ) : mailbox.recent_threads?.length ? (
            <ul className="space-y-2">
              {mailbox.recent_threads.slice(0, 3).map((thread) => (
                <li key={thread.id} className="min-w-0">
                  <div className="mb-0.5 flex flex-wrap gap-1">
                    <StatusBadge
                      label={stateLabel(thread.state)}
                      tone={stateTone(thread.state)}
                    />
                    {thread.urgency ? (
                      <StatusBadge
                        label={thread.urgency}
                        tone={urgencyTone(thread.urgency)}
                      />
                    ) : null}
                  </div>
                  <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                    {thread.subject || "(no subject)"}
                  </p>
                  {thread.teaching_note ? (
                    <p className="line-clamp-1 text-xs text-gray-500 dark:text-gray-400">
                      {thread.teaching_note}
                    </p>
                  ) : (
                    <p className="truncate text-xs text-gray-400">
                      {thread.last_sender ?? "Unknown"} ·{" "}
                      {formatRelativeTime(thread.last_message_at)}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-400">
              No threads awaiting action right now.
            </p>
          )}
        </div>
      </Link>
    </CardMount>
  )
}
