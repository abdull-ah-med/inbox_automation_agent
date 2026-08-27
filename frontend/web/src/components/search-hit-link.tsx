"use client"

import Link from "next/link"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import {
  formatRelativeTime,
  inboxAccentStyle,
  inboxChipClassName,
  inboxLabel,
} from "@/lib/design-tokens"
import { cn } from "@/lib/utils"
import type { SearchHit } from "@/lib/types"

const mailboxKeyFor = (mailbox: string) => {
  const local = mailbox.split("@")[0]
  return local || mailbox
}

export const SearchHitLink = ({ hit, onNavigate }: { hit: SearchHit; onNavigate?: () => void }) => {
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
        <span className={inboxChipClassName} style={inboxAccentStyle(key)}>
          {inboxLabel(key)}
        </span>
        <StatusBadge label={stateLabel(hit.state)} tone={stateTone(hit.state)} />
        {hit.urgency ? <StatusBadge label={hit.urgency} tone={urgencyTone(hit.urgency)} /> : null}
        {hit.last_message_at ? <span className="text-muted-foreground text-xs">{when}</span> : null}
      </div>
      <p className="text-foreground truncate text-sm font-medium">{subject}</p>
      {hit.snippet ? (
        <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">{hit.snippet}</p>
      ) : null}
    </Link>
  )
}
