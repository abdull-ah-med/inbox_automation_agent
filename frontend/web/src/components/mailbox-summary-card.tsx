"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"

import { PresentationBadges } from "@/components/presentation-badges"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api-client"
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

const countButtonClassName = (active: boolean) =>
  cn(
    "pointer-events-auto cursor-pointer rounded-sm underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring",
    active ? "text-foreground font-medium" : null,
  )

const previewState = (filter: CardPreviewFilter): string | undefined => {
  if (filter === "stale") return "STALE"
  if (filter === "filtered") return "FILTERED"
  if (filter === "awaiting") return "AWAITING_ACTION"
  return undefined
}

const inboxHrefFor = (mailboxKey: string, filter: CardPreviewFilter): string => {
  const path = `/mailboxes/${encodeURIComponent(mailboxKey)}`
  if (filter === "stale") return `${path}?state=STALE`
  if (filter === "filtered") return `${path}?state=FILTERED`
  if (filter === "awaiting") return `${path}?state=AWAITING_ACTION`
  return path
}

const inboxLinkLabelFor = (label: string, filter: CardPreviewFilter): string => {
  if (filter === "stale") return `Open ${label} inbox, stale threads`
  if (filter === "filtered") return `Open ${label} inbox, spam`
  if (filter === "total") return `Open ${label} inbox, all threads`
  return `Open ${label} inbox`
}

const previewCount = (mailbox: MailboxOverview, filter: CardPreviewFilter): number => {
  if (filter === "stale") return mailbox.stale_count
  if (filter === "filtered") return mailbox.filtered_count
  if (filter === "total") return mailbox.thread_count
  return mailbox.awaiting_action_count
}

const emptyCopy = (filter: CardPreviewFilter): string => {
  if (filter === "stale") return "No stale threads right now."
  if (filter === "filtered") return "No threads filtered as spam."
  if (filter === "total") return "No mail in this inbox."
  return "No threads awaiting action right now."
}

const previewMeta = (item: ThreadSummary): string => {
  if (item.teaching_note?.trim()) return item.teaching_note.trim()
  const sender = item.last_sender ?? "Unknown"
  return `${sender} · ${formatRelativeTime(item.last_message_at)}`
}

export const MailboxSummaryCard = ({ mailbox }: { mailbox: MailboxOverview }) => {
  const label = mailbox.label || inboxLabel(mailbox.mailbox)
  const isEmpty = mailbox.thread_count === 0
  const [previewFilter, setPreviewFilter] = useState<CardPreviewFilter>("awaiting")
  const inboxHref = inboxHrefFor(mailbox.mailbox, previewFilter)

  const handlePreviewFilter = (next: CardPreviewFilter) => {
    setPreviewFilter(next)
  }

  const { data, isFetching } = useQuery({
    queryKey: ["mailbox", mailbox.mailbox, "card-preview", previewFilter],
    queryFn: () =>
      api.mailboxes.threads(mailbox.mailbox, {
        state: previewState(previewFilter),
        limit: PREVIEW_LIMIT,
      }),
    enabled: previewFilter !== "awaiting",
  })

  const previewThreads: ThreadSummary[] =
    previewFilter === "awaiting"
      ? (mailbox.recent_threads ?? []).slice(0, PREVIEW_LIMIT)
      : (data?.items ?? [])
  const showPreviewSkeleton = previewFilter !== "awaiting" && isFetching && !data
  const showEmptyCopy = previewThreads.length === 0 && previewCount(mailbox, previewFilter) === 0

  return (
    <Card className="hover:bg-muted/40 relative h-full cursor-pointer gap-0 transition-colors">
      <Link
        href={inboxHref}
        aria-label={inboxLinkLabelFor(label, previewFilter)}
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
            <button
              type="button"
              aria-label={`View ${mailbox.awaiting_action_count} awaiting action in ${label}`}
              aria-pressed={previewFilter === "awaiting"}
              tabIndex={0}
              className="focus-visible:ring-ring pointer-events-auto cursor-pointer rounded-sm outline-none focus-visible:ring-2"
              onClick={() => handlePreviewFilter("awaiting")}
            >
              {mailbox.awaiting_action_count}
              <span
                className={cn(
                  "ml-1.5 text-sm",
                  previewFilter === "awaiting"
                    ? "text-foreground font-medium"
                    : "text-muted-foreground font-normal",
                )}
              >
                awaiting action
              </span>
            </button>
          </p>
          <p className="text-muted-foreground text-xs">
            <button
              type="button"
              aria-label={`View all ${mailbox.thread_count} threads in ${label}`}
              aria-pressed={previewFilter === "total"}
              tabIndex={0}
              className={countButtonClassName(previewFilter === "total")}
              onClick={() => handlePreviewFilter("total")}
            >
              {mailbox.thread_count} total
            </button>
            {mailbox.stale_count > 0 ? (
              <>
                {" · "}
                <button
                  type="button"
                  aria-label={`View ${mailbox.stale_count} stale threads in ${label}`}
                  aria-pressed={previewFilter === "stale"}
                  tabIndex={0}
                  className={countButtonClassName(previewFilter === "stale")}
                  onClick={() => handlePreviewFilter("stale")}
                >
                  {mailbox.stale_count} stale
                </button>
              </>
            ) : null}
            {mailbox.filtered_count > 0 ? (
              <>
                {" · "}
                <button
                  type="button"
                  aria-label={`View ${mailbox.filtered_count} filtered as spam in ${label}`}
                  aria-pressed={previewFilter === "filtered"}
                  tabIndex={0}
                  className={countButtonClassName(previewFilter === "filtered")}
                  onClick={() => handlePreviewFilter("filtered")}
                >
                  {mailbox.filtered_count} filtered as spam
                </button>
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
          ) : showPreviewSkeleton ? (
            <div className="space-y-3" aria-busy="true" aria-live="polite">
              {Array.from({ length: PREVIEW_LIMIT }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-lg" />
              ))}
            </div>
          ) : previewThreads.length ? (
            <ul className="divide-y divide-border/60">
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
                      <p className="text-card-foreground truncate text-sm font-medium leading-snug">
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
            <p className="text-muted-foreground text-sm">{emptyCopy(previewFilter)}</p>
          ) : null}
        </CardContent>
      </div>
    </Card>
  )
}
