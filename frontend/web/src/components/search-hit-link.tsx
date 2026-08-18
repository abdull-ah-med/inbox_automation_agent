"use client"

import Link from "next/link"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { formatRelativeTime, inboxColor, inboxLabel } from "@/lib/design-tokens"
import { cn } from "@/lib/utils"
import type { SearchHit } from "@/lib/types"

const mailboxKeyFor = (mailbox: string) => {
  const local = mailbox.split("@")[0]
  return local || mailbox
}

export const SearchHitLink = ({
  hit,
  onNavigate,
}: {
  hit: SearchHit
  onNavigate?: () => void
}) => {
  const key = mailboxKeyFor(hit.mailbox)
  const subject = hit.subject?.trim() || "Untitled thread"
  const href = `/threads/${hit.thread_id}`
  const when = formatRelativeTime(hit.last_message_at)

  const handleClick = () => {
    onNavigate?.()
  }

  return (
    <Link
      href={href}
      tabIndex={0}
      aria-label={subject}
      onClick={handleClick}
      className={cn(
        "block rounded-lg px-3 py-2.5 outline-none transition-colors",
        "hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <span
          className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
          style={{ backgroundColor: inboxColor(key) }}
        >
          {inboxLabel(key)}
        </span>
        <StatusBadge label={stateLabel(hit.state)} tone={stateTone(hit.state)} />
        {hit.urgency ? (
          <StatusBadge label={hit.urgency} tone={urgencyTone(hit.urgency)} />
        ) : null}
        {hit.last_message_at ? (
          <span className="text-xs text-muted-foreground">{when}</span>
        ) : null}
      </div>
      <p className="truncate text-sm font-medium text-foreground">{subject}</p>
      {hit.snippet ? (
        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{hit.snippet}</p>
      ) : null}
    </Link>
  )
}
