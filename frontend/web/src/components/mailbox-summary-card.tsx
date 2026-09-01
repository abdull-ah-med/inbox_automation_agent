"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
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
  if (filter === "filtered") return `Open ${label} inbox, spam and no action`
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
  if (filter === "filtered") return "No threads filtered as spam/no action."
  if (filter === "total") return "No mail in this inbox."
  return "No threads awaiting action right now."
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
          <div className="flex items-start justify-between gap-2">
            <span
              className={cn("inline-block no-underline", inboxChipClassName)}
              style={inboxAccentStyle(mailbox.mailbox)}
            >
              {label}
            </span>
            {mailbox.stale_count > 0 ? (
              <StatusBadge label={`${mailbox.stale_count} stale`} tone="amber" />
            ) : null}
          </div>
          {mailbox.email_address ? (
            <p className="text-muted-foreground truncate text-xs">{mailbox.email_address}</p>
          ) : null}
          <p className="text-card-foreground text-2xl font-semibold">
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
                  aria-label={`View ${mailbox.filtered_count} filtered as spam/no action in ${label}`}
                  aria-pressed={previewFilter === "filtered"}
                  tabIndex={0}
                  className={countButtonClassName(previewFilter === "filtered")}
                  onClick={() => handlePreviewFilter("filtered")}
                >
                  {mailbox.filtered_count} filtered as spam/no action
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
            <div className="space-y-2" aria-busy="true" aria-live="polite">
              {Array.from({ length: PREVIEW_LIMIT }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-lg" />
              ))}
            </div>
          ) : previewThreads.length ? (
            <ul className="space-y-2">
              {previewThreads.map((item) => (
                <li key={item.id} className="min-w-0">
                  <Link
                    href={`/threads/${item.id}`}
                    aria-label={`Review ${item.subject || "thread"}`}
                    tabIndex={0}
                    className="focus-visible:ring-ring pointer-events-auto block min-w-0 cursor-pointer rounded-sm no-underline outline-none focus-visible:ring-2"
                  >
                    <div className="mb-0.5 flex flex-wrap gap-1">
                      <StatusBadge label={stateLabel(item.state)} tone={stateTone(item.state)} />
                      {item.urgency ? (
                        <StatusBadge label={item.urgency} tone={urgencyTone(item.urgency)} />
                      ) : null}
                    </div>
                    <p className="text-card-foreground truncate text-sm font-medium">
                      {item.subject || "(no subject)"}
                    </p>
                    {item.teaching_note ? (
                      <p className="text-muted-foreground line-clamp-1 text-xs">
                        {item.teaching_note}
                      </p>
                    ) : (
                      <p className="text-muted-foreground truncate text-xs">
                        {item.last_sender ?? "Unknown"} · {formatRelativeTime(item.last_message_at)}
                      </p>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          ) : showEmptyCopy ? (
            <p className="text-muted-foreground text-sm">{emptyCopy(previewFilter)}</p>
          ) : null}
        </CardContent>
      </div>
    </Card>
  )
}
