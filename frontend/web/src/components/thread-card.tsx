"use client"

import Link from "next/link"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { NotSpamButton } from "@/components/not-spam-button"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  formatRelativeTime,
  inboxColor,
  inboxLabel,
} from "@/lib/design-tokens"
import { cn, textLinkClass } from "@/lib/utils"
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

  return (
    <Card
      className={cn(
        "w-full min-w-0 gap-0 py-0 transition-colors hover:bg-muted/40",
        selected && "ring-2 ring-primary",
      )}
    >
      <CardContent className="p-5">
        <Link
          href={`/threads/${thread.id}`}
          aria-label={`Open thread ${thread.subject || "untitled"}`}
          className="focus-visible:ring-ring block cursor-pointer text-left outline-none focus-visible:ring-2"
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
            {triage?.is_internal ? <StatusBadge label="Internal" tone="blue" /> : null}
            {triage?.is_automated ? <StatusBadge label="Automated" tone="neutral" /> : null}
            {triage?.is_spam ? <StatusBadge label="Spam" tone="red" /> : null}
            {triage?.needs_context ? (
              <StatusBadge label="Needs context" tone="amber" />
            ) : null}
          </div>

          <p className="truncate text-sm font-medium text-card-foreground">
            {thread.subject || "(no subject)"}
          </p>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {thread.last_sender ?? "Unknown sender"}
            {thread.preview ? ` — ${thread.preview}` : ""}
          </p>
        </Link>
      </CardContent>
      <Separator />
      <div className="flex items-center justify-between gap-2 px-5 py-3 text-xs text-muted-foreground">
        <span>
          {thread.message_count} msg
          {triage?.has_action_items ? " · action needed" : ""}
        </span>
        <div className="flex items-center gap-3">
          {thread.state === "SPAM" ? (
            <NotSpamButton
              threadId={thread.id}
              sender={thread.last_sender}
              size="xs"
            />
          ) : null}
          {thread.outlook_url ? (
            <a
              href={thread.outlook_url}
              target="_blank"
              rel="noopener noreferrer"
              tabIndex={0}
              aria-label={`Open thread in Outlook: ${thread.subject || "untitled"}`}
              className={cn(textLinkClass, "text-xs")}
            >
              Outlook
            </a>
          ) : null}
          <span>{formatRelativeTime(thread.last_message_at)}</span>
        </div>
      </div>
    </Card>
  )
}
