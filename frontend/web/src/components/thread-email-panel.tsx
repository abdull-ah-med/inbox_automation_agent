"use client"

import { useEffect, useRef, useState } from "react"
import { Loader2Icon } from "lucide-react"

import { EmailBody, splitQuotedHistory } from "@/components/email-body"
import { HtmlEmailFrame, isHtmlEmailSupported } from "@/components/html-email-frame"
import { StatusBadge } from "@/components/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api } from "@/lib/api-client"
import { formatRelativeTime, formatReviewerDateTime } from "@/lib/dates"
import type { MessageDetail } from "@/lib/types"
import { cn, textActionClass, textLinkClass } from "@/lib/utils"

const LOADING_SPINNER_MS = 400
const LOADING_TIMEOUT_MS = 7000

const directionLabel = (direction: string): "Sent" | "Received" => {
  return direction === "outbound" ? "Sent" : "Received"
}

const MEETING_LABEL: Record<string, string> = {
  meetingAccepted: "Meeting accepted",
  meetingTenativelyAccepted: "Meeting tentatively accepted",
  meetingDeclined: "Meeting declined",
  meetingCancelled: "Meeting cancelled",
  meetingRequest: "Meeting request",
}

const meetingLabel = (message: MessageDetail): string | null => {
  const type = message.meeting_message_type
  if (!type) return null
  return MEETING_LABEL[type] ?? null
}

const peekText = (message: MessageDetail): string => {
  const preview = message.body_preview?.trim()
  if (preview) return preview
  const reply = message.reply_text?.trim()
  if (reply) return reply.split("\n")[0] ?? reply
  const meeting = meetingLabel(message)
  if (meeting) return meeting
  return ""
}

const sortNewestFirst = (messages: MessageDetail[]): MessageDetail[] => {
  return messages.toSorted(
    (a, b) => new Date(b.received_at).getTime() - new Date(a.received_at).getTime(),
  )
}

type RichViewState =
  | { status: "plain" }
  | { status: "loading"; showSpinner: boolean }
  | { status: "html"; html: string }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "unsupported" }

const useRichHtml = (threadId: string, messageId: string) => {
  const [richView, setRichView] = useState<RichViewState>({ status: "plain" })
  const htmlCacheRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const loadRichHtml = async () => {
    if (!isHtmlEmailSupported()) {
      setRichView({ status: "unsupported" })
      return
    }
    if (htmlCacheRef.current != null) {
      const cached = htmlCacheRef.current.trim()
      if (!cached) {
        setRichView({ status: "empty" })
        return
      }
      setRichView({ status: "html", html: htmlCacheRef.current })
      return
    }

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setRichView({ status: "loading", showSpinner: false })

    const spinnerTimer = window.setTimeout(() => {
      setRichView((current) =>
        current.status === "loading" ? { status: "loading", showSpinner: true } : current,
      )
    }, LOADING_SPINNER_MS)
    const timeoutTimer = window.setTimeout(() => {
      controller.abort()
    }, LOADING_TIMEOUT_MS)

    try {
      const result = await api.threads.getMessageHtml(threadId, messageId, controller.signal)
      if (controller.signal.aborted) return
      const html = result.html ?? ""
      htmlCacheRef.current = html
      if (!html.trim()) {
        setRichView({ status: "empty" })
        return
      }
      setRichView({ status: "html", html })
    } catch {
      if (controller.signal.aborted) {
        setRichView({
          status: "error",
          message:
            "We couldn't load the rich view in time. The mailbox may be temporarily unreachable.",
        })
        return
      }
      setRichView({
        status: "error",
        message: "We couldn't load the rich view. The mailbox may be temporarily unreachable.",
      })
    } finally {
      window.clearTimeout(spinnerTimer)
      window.clearTimeout(timeoutTimer)
    }
  }

  const showPlain = () => {
    setRichView({ status: "plain" })
  }

  const retry = () => {
    htmlCacheRef.current = null
    void loadRichHtml()
  }

  return { richView, loadRichHtml, showPlain, retry }
}

const MessageBodySection = ({
  message,
  richView,
  onRetry,
}: {
  message: MessageDetail
  richView: RichViewState
  onRetry: () => void
}) => {
  const quoted = splitQuotedHistory(message.body_text).quoted
  const showingHtml = richView.status === "html"

  return (
    <div className="border-border border-t px-4 py-3">
      {richView.status === "unsupported" ? (
        <p className="text-muted-foreground text-sm" role="status">
          Rich view is not supported in this browser. Showing plain text instead.
        </p>
      ) : null}
      {richView.status === "loading" && richView.showSpinner ? (
        <div
          className="text-muted-foreground mb-2 flex items-center gap-2"
          aria-busy="true"
          aria-live="polite"
        >
          <Loader2Icon className="size-4 animate-spin" aria-hidden />
          <span className="text-xs">Loading rich view…</span>
        </div>
      ) : null}
      {richView.status === "error" ? (
        <div className="mb-3 space-y-2" role="alert">
          <p className="text-sm text-red-700 dark:text-red-400">{richView.message}</p>
          <button
            type="button"
            tabIndex={0}
            className={cn(textActionClass, "min-h-10 cursor-pointer text-xs text-blue-600")}
            onClick={onRetry}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                onRetry()
              }
            }}
          >
            Retry
          </button>
        </div>
      ) : null}
      {richView.status === "empty" ? (
        <p className="text-muted-foreground mb-3 text-sm" role="status">
          No HTML body for this message.
        </p>
      ) : null}
      {showingHtml ? (
        <div className="overflow-hidden rounded-md border border-neutral-200 bg-white shadow-sm dark:border-neutral-700">
          <HtmlEmailFrame
            html={richView.html}
            title={`Rich view of email from ${message.sender}`}
          />
        </div>
      ) : meetingLabel(message) && !message.reply_text.trim() ? (
        <p className="text-muted-foreground text-sm italic">{meetingLabel(message)}</p>
      ) : (
        <EmailBody
          text={message.reply_text}
          quotedText={quoted ?? undefined}
          emptyLabel="(no message body)"
          collapseQuotes
        />
      )}
    </div>
  )
}

const MessageBlock = ({
  threadId,
  message,
  defaultOpen,
}: {
  threadId: string
  message: MessageDetail
  defaultOpen: boolean
}) => {
  const [open, setOpen] = useState(defaultOpen)
  const { richView, loadRichHtml, showPlain, retry } = useRichHtml(threadId, message.id)
  const isOutbound = message.direction === "outbound"
  const label = directionLabel(message.direction)
  const when = formatReviewerDateTime(message.received_at)
  const peek = peekText(message)
  const showingHtml = richView.status === "html"
  const richToggleActive = showingHtml || richView.status === "empty" || richView.status === "error"
  const richButtonLabel = richToggleActive ? "Plain text" : "Rich view"

  const handleToggle = () => {
    setOpen((value) => !value)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggle()
    }
  }

  const handleRichViewClick = () => {
    if (richToggleActive) {
      showPlain()
      return
    }
    if (richView.status === "loading") return
    void loadRichHtml()
  }

  const handleRichViewKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleRichViewClick()
    }
  }

  return (
    <article aria-label={`${message.sender}, ${label}, ${when}`}>
      <Card size="sm" className="gap-0 py-0">
        <div className="flex items-start gap-2 px-4 py-3">
          <div className="min-w-0 flex-1 text-left select-text">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                {message.sender}
              </p>
              <StatusBadge label={label} tone={isOutbound ? "green" : "blue"} />
              {message.has_attachments ? <StatusBadge label="Attachments" tone="blue" /> : null}
            </div>
            <p className="text-muted-foreground text-xs">
              {formatRelativeTime(message.received_at)} · {when}
            </p>
            {message.to.length > 0 ? (
              <p className="text-muted-foreground truncate text-xs">To: {message.to.join(", ")}</p>
            ) : null}
            {message.cc.length > 0 ? (
              <p className="text-muted-foreground truncate text-xs">Cc: {message.cc.join(", ")}</p>
            ) : null}
            {message.bcc.length > 0 ? (
              <p className="text-muted-foreground truncate text-xs">
                Bcc: {message.bcc.join(", ")}
              </p>
            ) : null}
            {!open && peek ? (
              <p className="dark:text-muted-foreground mt-1 truncate text-xs text-gray-500">
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
            {open ? (
              <button
                type="button"
                tabIndex={0}
                aria-pressed={showingHtml}
                aria-label={richButtonLabel}
                className={cn(
                  textActionClass,
                  "min-h-10 min-w-10 cursor-pointer justify-center text-xs text-muted-foreground hover:text-gray-800 dark:hover:text-gray-300",
                )}
                onClick={handleRichViewClick}
                onKeyDown={handleRichViewKeyDown}
              >
                {richButtonLabel}
              </button>
            ) : null}
            <button
              type="button"
              tabIndex={0}
              aria-expanded={open}
              aria-label={open ? "Hide" : "Show"}
              className={cn(
                textActionClass,
                "min-h-10 min-w-10 cursor-pointer justify-center text-xs text-muted-foreground hover:text-gray-800 dark:hover:text-gray-300",
              )}
              onClick={handleToggle}
              onKeyDown={handleKeyDown}
            >
              {open ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {open ? <MessageBodySection message={message} richView={richView} onRetry={retry} /> : null}
      </Card>
    </article>
  )
}

export const ThreadEmailPanel = ({
  threadId,
  subject,
  messages,
}: {
  threadId: string
  subject: string
  messages: MessageDetail[]
  outlookUrl?: string | null
}) => {
  const ordered = sortNewestFirst(messages)

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="text-base font-semibold">{subject || "(no subject)"}</CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">
            {messages.length} message{messages.length === 1 ? "" : "s"} in thread
          </p>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {ordered.length === 0 ? (
          <p className="text-muted-foreground text-sm">No messages in this thread.</p>
        ) : (
          ordered.map((message, index) => (
            <div key={message.id}>
              <MessageBlock threadId={threadId} message={message} defaultOpen={index === 0} />
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
