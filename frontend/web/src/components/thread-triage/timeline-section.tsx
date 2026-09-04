"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { Panel } from "@/components/thread-triage/panel"
import { api } from "@/lib/api-client"
import { formatReviewerDateTime } from "@/lib/dates"
import { buildThreadStory, type ThreadStoryEvent } from "@/lib/thread-story"
import type { MessageDetail } from "@/lib/types"
import { cn } from "@/lib/utils"

export const TIMELINE_PREVIEW_COUNT = 7

const TimelineEventItem = ({ event }: { event: ThreadStoryEvent }) => (
  <li className="relative pb-4 pl-4 last:pb-0">
    <span
      aria-hidden="true"
      className={cn(
        "absolute top-1.5 left-0 size-2.5 -translate-x-1/2 rounded-full border-2",
        event.direction === "outbound"
          ? "border-emerald-600 bg-emerald-400 dark:border-emerald-400 dark:bg-emerald-700"
          : "border-sky-600 bg-sky-400 dark:border-sky-400 dark:bg-sky-800",
      )}
    />
    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{event.headline}</p>
    {event.whatHappened ? (
      <p className="mt-0.5 text-sm text-gray-700 dark:text-gray-300">{event.whatHappened}</p>
    ) : null}
    <time dateTime={event.occurredAt} className="text-muted-foreground mt-0.5 block text-[11px]">
      {formatReviewerDateTime(event.occurredAt)}
    </time>
  </li>
)

export const TimelineSection = ({
  threadId,
  messages,
  mailbox,
  subject,
}: {
  threadId: string
  messages: MessageDetail[]
  mailbox: string
  subject: string | null
}) => {
  const [earlierOpen, setEarlierOpen] = useState(false)
  const contextQuery = useQuery({
    queryKey: ["thread", threadId, "context"],
    queryFn: () => api.threads.getContext(threadId),
    retry: false,
  })
  const eventsNewestFirst = buildThreadStory({
    messages,
    mailbox,
    subject,
    facts: contextQuery.data?.facts ?? [],
  }).toReversed()
  const hiddenCount = Math.max(0, eventsNewestFirst.length - TIMELINE_PREVIEW_COUNT)
  const recent = eventsNewestFirst.slice(0, TIMELINE_PREVIEW_COUNT)
  const earlier = hiddenCount > 0 ? eventsNewestFirst.slice(TIMELINE_PREVIEW_COUNT) : []

  const handleToggleEarlier = () => {
    setEarlierOpen((open) => !open)
  }

  return (
    <Panel title="Timeline">
      {eventsNewestFirst.length === 0 ? (
        <p className="text-muted-foreground text-sm">No emails on this thread yet.</p>
      ) : (
        <ol
          className="border-border/70 relative ml-1.5 border-l"
          aria-label="Thread email timeline"
        >
          {recent.map((event) => (
            <TimelineEventItem key={event.id} event={event} />
          ))}
          {hiddenCount > 0 ? (
            <li className="relative py-3 pl-4">
              <span
                aria-hidden="true"
                className="bg-border absolute top-1/2 left-0 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
              />
              <button
                type="button"
                tabIndex={0}
                aria-expanded={earlierOpen}
                aria-label={
                  earlierOpen
                    ? "Hide earlier emails"
                    : `Show ${hiddenCount} earlier ${hiddenCount === 1 ? "email" : "emails"}`
                }
                className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -ml-1 rounded-md px-1.5 py-0.5 text-xs tracking-wide transition-colors focus-visible:ring-2 focus-visible:outline-none"
                onClick={handleToggleEarlier}
              >
                {earlierOpen
                  ? "Hide earlier"
                  : `${hiddenCount} earlier ${hiddenCount === 1 ? "email" : "emails"}`}
              </button>
            </li>
          ) : null}
          {earlierOpen
            ? earlier.map((event) => <TimelineEventItem key={event.id} event={event} />)
            : null}
        </ol>
      )}
    </Panel>
  )
}
