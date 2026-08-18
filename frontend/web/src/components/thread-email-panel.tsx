"use client"

import { useState } from "react"

import { EmailBody } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { MessageDetail } from "@/lib/types"
import { cn, textActionClass, textLinkClass } from "@/lib/utils"

const MessageBlock = ({
  message,
  defaultOpen,
}: {
  message: MessageDetail
  defaultOpen: boolean
}) => {
  const [open, setOpen] = useState(defaultOpen)

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
    <Card size="sm" className="gap-0 py-0">
      <div className="flex items-start gap-2 px-4 py-3">
        <div className="min-w-0 flex-1 select-text text-left">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
              {message.sender}
            </p>
            {message.has_attachments ? (
              <StatusBadge label="Attachments" tone="blue" />
            ) : null}
          </div>
          <p className="text-xs text-gray-400">
            {new Date(message.received_at).toLocaleString()} · {message.direction}
          </p>
          {message.to.length > 0 ? (
            <p className="truncate text-xs text-gray-400">
              To: {message.to.join(", ")}
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
              "cursor-pointer text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300",
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
            text={message.body_text}
            emptyLabel="(no message body)"
            collapseQuotes
          />
        </div>
      ) : null}
    </Card>
  )
}

export const ThreadEmailPanel = ({
  subject,
  messages,
  outlookUrl,
}: {
  subject: string
  messages: MessageDetail[]
  outlookUrl?: string | null
}) => {
  const [showEarlier, setShowEarlier] = useState(false)
  const latest = messages[messages.length - 1]
  const earlier = messages.slice(0, -1)

  const handleToggleEarlier = () => {
    setShowEarlier((value) => !value)
  }

  const handleEarlierKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggleEarlier()
    }
  }

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
        {earlier.length > 0 ? (
          <button
            type="button"
            tabIndex={0}
            aria-expanded={showEarlier}
            aria-label={
              showEarlier
                ? `Hide ${earlier.length} earlier messages`
                : `Show ${earlier.length} earlier messages`
            }
            className={cn(textLinkClass, "text-sm")}
            onClick={handleToggleEarlier}
            onKeyDown={handleEarlierKeyDown}
          >
            {showEarlier
              ? `Hide earlier (${earlier.length})`
              : `Show earlier (${earlier.length})`}
          </button>
        ) : null}
        {(showEarlier ? earlier : []).map((message) => (
          <MessageBlock key={message.id} message={message} defaultOpen={false} />
        ))}
        {latest ? <MessageBlock message={latest} defaultOpen /> : null}
        {!latest ? (
          <p className="text-sm text-muted-foreground">No messages in this thread.</p>
        ) : null}
      </CardContent>
    </Card>
  )
}
