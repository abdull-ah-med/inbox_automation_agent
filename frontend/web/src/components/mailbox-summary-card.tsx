"use client"

import Link from "next/link"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  formatRelativeTime,
  inboxColor,
  inboxLabel,
} from "@/lib/design-tokens"
import type { MailboxOverview } from "@/lib/types"

export const MailboxSummaryCard = ({
  mailbox,
}: {
  mailbox: MailboxOverview
}) => {
  const color = inboxColor(mailbox.mailbox)
  const label = mailbox.label || inboxLabel(mailbox.mailbox)
  const isEmpty = mailbox.thread_count === 0

  return (
    <Link
      href={`/mailboxes/${encodeURIComponent(mailbox.mailbox)}`}
      aria-label={`Open ${label} inbox`}
      className="block h-full rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Card className="h-full gap-0 transition-colors hover:bg-muted/40">
        <CardHeader className="gap-2 pb-4">
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
            <p className="truncate text-xs text-muted-foreground">
              {mailbox.email_address}
            </p>
          ) : null}
          <p className="text-2xl font-semibold text-card-foreground">
            {mailbox.awaiting_action_count}
            <span className="ml-1.5 text-sm font-normal text-muted-foreground">
              awaiting action
            </span>
          </p>
          <p className="text-xs text-muted-foreground">
            {mailbox.thread_count} total
            {mailbox.stale_count > 0 ? ` · ${mailbox.stale_count} stale` : ""}
            {mailbox.filtered_count > 0
              ? ` · ${mailbox.filtered_count} filtered as spam/no action`
              : ""}
          </p>
        </CardHeader>
        <Separator />
        <CardContent className="pt-4">
          {isEmpty ? (
            <p className="text-sm text-muted-foreground">
              No mail ingested for this inbox yet. New threads appear after the
              next poll.
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
                  <p className="truncate text-sm font-medium text-card-foreground">
                    {thread.subject || "(no subject)"}
                  </p>
                  {thread.teaching_note ? (
                    <p className="line-clamp-1 text-xs text-muted-foreground">
                      {thread.teaching_note}
                    </p>
                  ) : (
                    <p className="truncate text-xs text-muted-foreground">
                      {thread.last_sender ?? "Unknown"} ·{" "}
                      {formatRelativeTime(thread.last_message_at)}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No threads awaiting action right now.
            </p>
          )}
        </CardContent>
      </Card>
    </Link>
  )
}
