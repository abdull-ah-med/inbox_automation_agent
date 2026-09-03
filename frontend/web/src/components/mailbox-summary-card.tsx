"use client"

import Link from "next/link"

import { PresentationBadges } from "@/components/presentation-badges"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  formatRelativeTime,
  inboxAccentStyle,
  inboxChipClassName,
  inboxLabel,
} from "@/lib/design-tokens"
import { mailboxPreviewSignals } from "@/lib/mailbox-preview-signals"
import { cn } from "@/lib/utils"
import type { MailboxOverview, ThreadSummary } from "@/lib/types"

type CardPreviewFilter = "awaiting" | "total" | "stale" | "filtered"

const PREVIEW_LIMIT = 3

const countLinkClassName =
  "pointer-events-auto cursor-pointer rounded-sm underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"

const inboxHrefFor = (mailboxKey: string, filter: CardPreviewFilter): string => {
  const path = `/mailboxes/${encodeURIComponent(mailboxKey)}`
  if (filter === "stale") return `${path}?state=STALE`
  if (filter === "filtered") return `${path}?state=FILTERED`
  if (filter === "awaiting") return `${path}?state=AWAITING_ACTION`
  return path
}

const previewMeta = (item: ThreadSummary): string => {
  if (item.teaching_note?.trim()) return item.teaching_note.trim()
  const sender = item.last_sender ?? "Unknown"
  return `${sender} · ${formatRelativeTime(item.last_message_at)}`
}

export const MailboxSummaryCard = ({ mailbox }: { mailbox: MailboxOverview }) => {
  const label = mailbox.label || inboxLabel(mailbox.mailbox)
  const isEmpty = mailbox.thread_count === 0
  const inboxHref = inboxHrefFor(mailbox.mailbox, "awaiting")
  const previewThreads = (mailbox.recent_threads ?? []).slice(0, PREVIEW_LIMIT)
  const showEmptyCopy = previewThreads.length === 0 && mailbox.awaiting_action_count === 0

  return (
    <Card className="hover:bg-muted/40 relative h-full cursor-pointer gap-0 transition-colors">
      <Link
        href={inboxHref}
        aria-label={`Open ${label} inbox`}
        tabIndex={0}
        className="focus-visible:ring-ring absolute inset-0 z-0 rounded-xl outline-none focus-visible:ring-2"
      />
      <div className="pointer-events-none relative z-10 flex h-full flex-col">
        <CardHeader className="gap-2 pb-4">
          <span
            className={cn("inline-block w-fit no-underline", inboxChipClassName)}
            style={inboxAccentStyle(mailbox.mailbox)}
          >
            {label}
          </span>
          {mailbox.email_address ? (
            <p className="text-muted-foreground truncate text-xs">{mailbox.email_address}</p>
          ) : null}
          <p className="text-card-foreground text-2xl font-semibold tracking-tight">
            <Link
              href={inboxHrefFor(mailbox.mailbox, "awaiting")}
              aria-label={`View ${mailbox.awaiting_action_count} awaiting action in ${label}`}
              tabIndex={0}
              className="focus-visible:ring-ring pointer-events-auto cursor-pointer rounded-sm outline-none focus-visible:ring-2"
            >
              {mailbox.awaiting_action_count}
              <span className="text-foreground ml-1.5 text-sm font-medium">awaiting action</span>
            </Link>
          </p>
          <p className="text-muted-foreground text-xs">
            <Link
              href={inboxHrefFor(mailbox.mailbox, "total")}
              aria-label={`View all ${mailbox.thread_count} threads in ${label}`}
              tabIndex={0}
              className={countLinkClassName}
            >
              {mailbox.thread_count} total
            </Link>
            {mailbox.stale_count > 0 ? (
              <>
                {" · "}
                <Link
                  href={inboxHrefFor(mailbox.mailbox, "stale")}
                  aria-label={`View ${mailbox.stale_count} stale threads in ${label}`}
                  tabIndex={0}
                  className={countLinkClassName}
                >
                  {mailbox.stale_count} stale
                </Link>
              </>
            ) : null}
            {mailbox.filtered_count > 0 ? (
              <>
                {" · "}
                <Link
                  href={inboxHrefFor(mailbox.mailbox, "filtered")}
                  aria-label={`View ${mailbox.filtered_count} filtered as spam in ${label}`}
                  tabIndex={0}
                  className={countLinkClassName}
                >
                  {mailbox.filtered_count} filtered as spam
                </Link>
              </>
            ) : null}
          </p>
        </CardHeader>
        <Separator />
        <CardContent className="pt-4">
          {isEmpty ? (
            <p className="text-muted-foreground text-sm">
              No mail ingested for this inbox yet. New threads appear after the next poll.
            </p>
          ) : previewThreads.length ? (
            <ul className="divide-border/60 divide-y">
              {previewThreads.map((item) => {
                const signals = mailboxPreviewSignals(item)
                return (
                  <li key={item.id} className="min-w-0 py-3 first:pt-0 last:pb-0">
                    <Link
                      href={`/threads/${item.id}`}
                      aria-label={`Review ${item.subject || "thread"}`}
                      tabIndex={0}
                      className="focus-visible:ring-ring pointer-events-auto block min-w-0 cursor-pointer rounded-sm no-underline outline-none focus-visible:ring-2"
                    >
                      <p className="text-card-foreground truncate text-sm leading-snug font-medium">
                        {item.subject || "(no subject)"}
                      </p>
                      <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
                        <PresentationBadges badges={signals} />
                        <p className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
                          {previewMeta(item)}
                        </p>
                      </div>
                    </Link>
                  </li>
                )
              })}
            </ul>
          ) : showEmptyCopy ? (
            <p className="text-muted-foreground text-sm">No threads awaiting action right now.</p>
          ) : null}
        </CardContent>
      </div>
    </Card>
  )
}
