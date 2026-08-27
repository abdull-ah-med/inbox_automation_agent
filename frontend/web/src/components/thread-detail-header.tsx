"use client"

import { PresentationBadges } from "@/components/presentation-badges"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { inboxAccentStyle, inboxChipClassName, inboxLabel } from "@/lib/design-tokens"
import { formatThreadHeaderMeta } from "@/lib/thread-header-meta"
import { cn, textLinkClass } from "@/lib/utils"
import type { MessageDetail, ThreadSummary } from "@/lib/types"

type ThreadDetailHeaderProps = {
  thread: ThreadSummary
  messages: MessageDetail[]
}

export const ThreadDetailHeader = ({ thread, messages }: ThreadDetailHeaderProps) => {
  const label = inboxLabel(thread.mailbox_key)
  const presentation = thread.presentation
  const subject = thread.subject || "(no subject)"

  return (
    <Card className="mb-5">
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={inboxChipClassName} style={inboxAccentStyle(thread.mailbox_key)}>
            {label}
          </span>
          {presentation?.badges_now?.length ? (
            <PresentationBadges badges={presentation.badges_now} />
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-card-foreground text-lg font-semibold">{subject}</h1>
            <p className="text-muted-foreground mt-1 text-sm">
              {formatThreadHeaderMeta({
                mailbox: thread.mailbox,
                lastSender: thread.last_sender,
                messageCount: thread.message_count,
                messages,
              })}
            </p>
            {presentation && !presentation.urgency_active && presentation.urgency_assessed ? (
              <p className="text-muted-foreground mt-1 text-xs">
                Assessed urgency {presentation.urgency_assessed} (inactive — finished thread)
              </p>
            ) : null}
          </div>
          {thread.outlook_url ? (
            <a
              href={thread.outlook_url}
              target="_blank"
              rel="noopener noreferrer"
              tabIndex={0}
              aria-label="Open thread in Outlook"
              className={cn(textLinkClass, "text-sm font-medium")}
            >
              Open in Outlook
            </a>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
