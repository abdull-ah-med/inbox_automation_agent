"use client"

import { useState } from "react"

import { EmailBody, splitQuotedHistory } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatRelativeTime, formatReviewerDateTime } from "@/lib/dates"
import type { MessageDetail } from "@/lib/types"
import { cn, textActionClass, textLinkClass } from "@/lib/utils"

const directionLabel = (direction: string): "Sent" | "Received" => {
  return direction === "outbound" ? "Sent" : "Received"
}

const peekText = (message: MessageDetail): string => {
  const preview = message.body_preview?.trim()
  if (preview) return preview
  const reply = message.reply_text?.trim()
  if (reply) return reply.split("\n")[0] ?? reply
  return ""
}

const sortNewestFirst = (messages: MessageDetail[]): MessageDetail[] => {
  return [...messages].sort(
    (a, b) =>
      new Date(b.received_at).getTime() - new Date(a.received_at).getTime(),
  )
}

const MessageBlock = ({
  message,
  defaultOpen,
}: {
  message: MessageDetail
  defaultOpen: boolean
}) => {
  const [open, setOpen] = useState(defaultOpen)
  const isOutbound = message.direction === "outbound"
  const label = directionLabel(message.direction)
  const when = formatReviewerDateTime(message.received_at)
  const quoted = splitQuotedHistory(message.body_text).quoted
  const peek = peekText(message)

  const handleToggle = () => {
    setOpen((value) => !value)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggle()
    }
  }

  return (
    <article aria-label={`${message.sender}, ${label}, ${when}`}>
      <Card size="sm" className="gap-0 py-0">
        <div className="flex items-start gap-2 px-4 py-3">
          <div className="min-w-0 flex-1 select-text text-left">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                {message.sender}
              </p>
              <StatusBadge
                label={label}
                tone={isOutbound ? "green" : "blue"}
              />
              {message.has_attachments ? (
                <StatusBadge label="Attachments" tone="blue" />
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground">
              {formatRelativeTime(message.received_at)} · {when}
            </p>
            {message.to.length > 0 ? (
              <p className="truncate text-xs text-muted-foreground">
                To: {message.to.join(", ")}
              </p>
            ) : null}
            {message.cc.length > 0 ? (
              <p className="truncate text-xs text-muted-foreground">
                Cc: {message.cc.join(", ")}
              </p>
            ) : null}
            {message.bcc.length > 0 ? (
              <p className="truncate text-xs text-muted-foreground">
                Bcc: {message.bcc.join(", ")}
              </p>
            ) : null}
            {!open && peek ? (
              <p className="mt-1 truncate text-xs text-gray-500 dark:text-muted-foreground">
                {peek}
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-3 pt-0.5">
            {message.outlook_url ? (
              <a
                href={message.outlook_url}
                target="_blank"
                rel="noopener noreferrer"
                tabIndex={0}
                aria-label={`Open email from ${message.sender} in Outlook`}
                className={cn(textLinkClass, "text-xs")}
              >
                Outlook
              </a>
            ) : null}
            <button
              type="button"
              tabIndex={0}
              aria-expanded={open}
              aria-label={open ? "Hide" : "Show"}
              className={cn(
                textActionClass,
                "cursor-pointer text-xs text-muted-foreground hover:text-gray-800 dark:hover:text-gray-300",
              )}
              onClick={handleToggle}
              onKeyDown={handleKeyDown}
            >
              {open ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {open ? (
          <div className="border-t border-border px-4 py-3">
            <EmailBody
              text={message.reply_text}
              quotedText={quoted ?? undefined}
              emptyLabel="(no message body)"
              collapseQuotes
            />
          </div>
        ) : null}
      </Card>
    </article>
  )
}

export const ThreadEmailPanel = ({
  subject,
  messages,
}: {
  subject: string
  messages: MessageDetail[]
  outlookUrl?: string | null
}) => {
  const ordered = sortNewestFirst(messages)

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="text-base font-semibold">
            {subject || "(no subject)"}
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {messages.length} message{messages.length === 1 ? "" : "s"} in thread
          </p>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {ordered.length === 0 ? (
          <p className="text-sm text-muted-foreground">No messages in this thread.</p>
        ) : (
          ordered.map((message, index) => (
            <div key={message.id}>
              <MessageBlock message={message} defaultOpen={index === 0} />
              <span className="sr-only">
                Message {index + 1} of {ordered.length}
              </span>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}
