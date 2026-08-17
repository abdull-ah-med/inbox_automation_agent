"use client"

import Link from "next/link"
import { ArrowUpRight } from "lucide-react"

import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { inboxColor, inboxLabel } from "@/lib/design-tokens"
import { cn } from "@/lib/utils"
import type { ChatCitation, MailboxOverview } from "@/lib/types"

const mailboxKeyFor = (
  mailbox: string,
  mailboxes: MailboxOverview[],
): string => {
  const match = mailboxes.find(
    (item) => item.email_address === mailbox || item.mailbox === mailbox,
  )
  if (match) return match.mailbox
  const local = mailbox.split("@")[0]
  return local || mailbox
}

export const AskCitationCard = ({
  citation,
  index,
  mailboxes,
  onNavigate,
}: {
  citation: ChatCitation
  index: number
  mailboxes: MailboxOverview[]
  onNavigate?: () => void
}) => {
  const key = mailboxKeyFor(citation.mailbox, mailboxes)
  const color = inboxColor(key)
  const label = inboxLabel(key)
  const subject = citation.subject?.trim() || "Untitled thread"
  const href = citation.url_path || `/threads/${citation.thread_id}`

  const handleClick = () => {
    onNavigate?.()
  }

  return (
    <Link
      href={href}
      tabIndex={0}
      aria-label={`Open thread ${subject}`}
      onClick={handleClick}
      className={cn(
        "group block rounded-xl bg-card p-4 ring-1 ring-foreground/10 outline-none transition-colors",
        "hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[11px] font-semibold text-white"
        >
          {index}
        </span>
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <span
              className="rounded-full px-2 py-0.5 text-xs font-medium text-white"
              style={{ backgroundColor: color }}
            >
              {label}
            </span>
            <StatusBadge label={stateLabel(citation.state)} tone={stateTone(citation.state)} />
            {citation.urgency ? (
              <StatusBadge label={citation.urgency} tone={urgencyTone(citation.urgency)} />
            ) : null}
          </div>
          <p className="truncate text-sm font-medium text-card-foreground">{subject}</p>
          {citation.snippet ? (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              {citation.snippet}
            </p>
          ) : null}
        </div>
        <ArrowUpRight
          className="size-4 shrink-0 text-muted-foreground group-hover:text-blue-600 dark:group-hover:text-blue-400"
          aria-hidden="true"
        />
      </div>
    </Link>
  )
}
