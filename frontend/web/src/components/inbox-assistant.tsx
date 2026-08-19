"use client"

import { useEffect, useRef, useState } from "react"
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowUpIcon,
  Maximize2Icon,
  MessageCircle,
  MessageCircleDashedIcon,
  Minimize2Icon,
  RotateCwIcon,
  ShieldAlert,
  X,
} from "lucide-react"

import { AskCitationCard } from "@/components/ask-citation-card"
import { EmailBody } from "@/components/email-body"
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Kbd } from "@/components/ui/kbd"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { StarBorder } from "@/components/ui/star-border"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { api } from "@/lib/api-client"
import { getErrorMessage } from "@/lib/error-messages"
import { sanitizeUserText } from "@/lib/sanitize"
import type { ChatAskRequest, ChatCitation, ChatHistoryTurn, MailboxOverview } from "@/lib/types"
import { cn } from "@/lib/utils"

const ALL_MAILBOXES = ""
const COMPOSER_MAX_ROWS = 8
const PANEL_SIZES = ["compact", "large"] as const

type PanelSize = (typeof PANEL_SIZES)[number]

const PANEL_SIZE_CLASS: Record<PanelSize, string> = {
  compact:
    "right-3 bottom-3 h-[min(32rem,70dvh)] w-[min(24rem,calc(100vw-1.5rem))] max-h-[70dvh] sm:right-6 sm:bottom-6 sm:h-[min(40rem,calc(100dvh-5rem))] sm:w-[26rem] sm:max-h-none",
  large:
    "right-3 bottom-3 h-[min(52rem,92dvh)] w-[min(42rem,calc(100vw-1.5rem))] max-h-[92dvh] sm:right-4 sm:bottom-4 sm:h-[calc(100dvh-2rem)] sm:w-[min(48rem,calc(100vw-2rem))] sm:max-h-none",
}

const composerRowCount = (value: string): number => {
  const lines = value.split("\n").length
  if (lines < 1) return 1
  if (lines > COMPOSER_MAX_ROWS) return COMPOSER_MAX_ROWS
  return lines
}

type ChatTurn = {
  id: string
  role: "user" | "assistant"
  text: string
  citations?: ChatCitation[]
  refusedWrite?: boolean
  streaming?: boolean
  error?: boolean
}

type PendingAskMeta = {
  citations: ChatCitation[]
  refusedWrite: boolean
}

const EXAMPLE_ASKS = [
  "What should I focus on today?",
  "Billing disputes waiting on review",
  "Threads about SampleLab",
] as const

export const InboxAssistant = () => {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState("")
  const [mailbox, setMailbox] = useState(ALL_MAILBOXES)
  const [validation, setValidation] = useState<string | null>(null)
  const [isAsking, setIsAsking] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [panelSize, setPanelSize] = useState<PanelSize>("compact")
  const [toolStatus, setToolStatus] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const pendingMetaRef = useRef<PendingAskMeta | null>(null)
  const sizeIndex = PANEL_SIZES.indexOf(panelSize)
  const canShrink = sizeIndex > 0
  const canGrow = sizeIndex < PANEL_SIZES.length - 1

  const mailboxesQuery = useQuery({
    queryKey: ["mailboxes", "list"],
    queryFn: () => api.mailboxes.list(),
    enabled: open,
  })
  const mailboxes: MailboxOverview[] = mailboxesQuery.data ?? []

  useEffect(() => {
    if (!open) return
    const frame = window.requestAnimationFrame(() => {
      inputRef.current?.focus()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [open])

  const handleOpen = () => {
    setOpen(true)
  }

  const handleClose = () => {
    setOpen(false)
  }

  const handleReset = () => {
    pendingMetaRef.current = null
    setToolStatus(null)
    setTurns([])
    setMessage("")
    setValidation(null)
  }

  const handleShrink = () => {
    if (!canShrink) return
    setPanelSize(PANEL_SIZES[sizeIndex - 1])
  }

  const handleGrow = () => {
    if (!canGrow) return
    setPanelSize(PANEL_SIZES[sizeIndex + 1])
  }

  const handleMailboxChange = (value: string | null) => {
    setMailbox(value ?? ALL_MAILBOXES)
  }

  const mailboxItems = [
    { label: "All mailboxes", value: null },
    ...mailboxes.map((item) => ({
      label: item.label,
      value: item.email_address,
    })),
  ]

  const handleMessageChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(event.target.value)
  }

  const handleAsk = async (nextMessage = message) => {
    const trimmed = sanitizeUserText(nextMessage)
    if (!trimmed) {
      setValidation("Enter a question")
      return
    }
    setValidation(null)
    setMessage("")
    const history: ChatHistoryTurn[] = turns
      .filter((turn) => !turn.error && turn.text.trim())
      .map((turn) => {
        if (turn.role !== "assistant" || !turn.citations?.length) {
          return { role: turn.role, content: turn.text }
        }
        return {
          role: turn.role,
          content: turn.text,
          citations: turn.citations.map((citation) => ({
            thread_id: citation.thread_id,
            subject: citation.subject,
          })),
        }
      })
    const userTurn: ChatTurn = {
      id: `user-${Date.now()}`,
      role: "user",
      text: trimmed,
    }
    setTurns((current) => [...current, userTurn])
    const assistantId = `assistant-${Date.now()}`
    setToolStatus(null)
    setIsAsking(true)
    try {
      const payload: ChatAskRequest = { message: trimmed }
      if (mailbox) payload.mailbox = mailbox
      if (history.length > 0) payload.history = history
      await api.chat.askStream(payload, {
        onStatus: (text) => {
          setToolStatus(text)
        },
        onMeta: (meta) => {
          pendingMetaRef.current = {
            citations: meta.citations,
            refusedWrite: meta.refused_write,
          }
        },
        onDelta: (text) => {
          setTurns((current) => {
            const existing = current.find((turn) => turn.id === assistantId)
            if (existing) {
              return current.map((turn) =>
                turn.id === assistantId
                  ? { ...turn, text: `${turn.text}${text}`, streaming: true }
                  : turn,
              )
            }
            return [
              ...current,
              {
                id: assistantId,
                role: "assistant",
                text,
                streaming: true,
              },
            ]
          })
        },
        onDone: () => {
          const pending = pendingMetaRef.current
          pendingMetaRef.current = null
          setToolStatus(null)
          setTurns((current) => {
            const existing = current.find((turn) => turn.id === assistantId)
            const citations = pending?.citations ?? []
            const refusedWrite = pending?.refusedWrite ?? false
            if (existing) {
              return current.map((turn) =>
                turn.id === assistantId
                  ? {
                      ...turn,
                      citations,
                      refusedWrite,
                      streaming: false,
                    }
                  : turn,
              )
            }
            return [
              ...current,
              {
                id: assistantId,
                role: "assistant",
                text: "",
                citations,
                refusedWrite,
                streaming: false,
              },
            ]
          })
        },
      })
    } catch (error) {
      pendingMetaRef.current = null
      setToolStatus(null)
      setTurns((current) => [
        ...current.map((turn) =>
          turn.id === assistantId ? { ...turn, streaming: false } : turn,
        ),
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          text: getErrorMessage(error),
          error: true,
        },
      ])
    } finally {
      pendingMetaRef.current = null
      setIsAsking(false)
    }
  }

  const handleExampleAsk = (prompt: string) => {
    setMessage(prompt)
    void handleAsk(prompt)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void handleAsk()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      void handleAsk()
    }
  }

  const composerRows = composerRowCount(message)
  const showEmpty = turns.length === 0 && !isAsking

  return (
    <TooltipProvider>
      <div
        className={cn(
          "fixed right-4 bottom-4 z-40 transition duration-200 ease-out sm:right-6 sm:bottom-6",
          open
            ? "pointer-events-none scale-90 opacity-0"
            : "scale-100 opacity-100",
        )}
      >
        <StarBorder color="#2563eb" thickness={2}>
          <Button
            type="button"
            tabIndex={open ? -1 : 0}
            aria-label="InboxAssistant"
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-hidden={open}
            onClick={handleOpen}
            className="h-12 gap-2.5 rounded-full bg-primary px-3.5 text-primary-foreground hover:bg-primary/80"
          >
            <MessageCircle className="size-4" aria-hidden="true" />
            <span className="hidden sm:inline">InboxAssistant</span>
          </Button>
        </StarBorder>
      </div>

      <aside
        role="dialog"
        aria-modal="false"
        aria-label="InboxAssistant"
        aria-hidden={!open}
        inert={!open}
        data-state={open ? "open" : "closed"}
        className={cn(
          "fixed z-50 flex flex-col overflow-hidden rounded-2xl bg-card shadow-2xl ring-1 ring-foreground/10",
          PANEL_SIZE_CLASS[panelSize],
          "origin-bottom-right transition-[width,height,transform,opacity] duration-200 ease-out",
          open
            ? "pointer-events-auto translate-y-0 scale-100 opacity-100"
            : "pointer-events-none translate-y-3 scale-95 opacity-0",
        )}
      >
        <MessageScrollerProvider>
          <div
            data-slot="card"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <header className="flex shrink-0 items-start gap-3 border-b border-border px-4 py-3">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold tracking-tight text-foreground">
                  InboxAssistant
                </h2>
                <p className="text-xs text-muted-foreground">
                  Read-only · cites matching threads
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Make InboxAssistant smaller"
                        disabled={!canShrink}
                        onClick={handleShrink}
                      >
                        <Minimize2Icon />
                      </Button>
                    }
                  />
                  <TooltipContent>
                    <p>Smaller</p>
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Make InboxAssistant larger"
                        disabled={!canGrow}
                        onClick={handleGrow}
                      >
                        <Maximize2Icon />
                      </Button>
                    }
                  />
                  <TooltipContent>
                    <p>Larger</p>
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        variant="outline"
                        size="icon-sm"
                        aria-label="Reset conversation"
                        disabled={isAsking}
                        onClick={handleReset}
                      >
                        <RotateCwIcon />
                      </Button>
                    }
                  />
                  <TooltipContent>
                    <p>Reset</p>
                  </TooltipContent>
                </Tooltip>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Close InboxAssistant"
                  onClick={handleClose}
                >
                  <X className="size-4" aria-hidden="true" />
                </Button>
              </div>
            </header>

            <div className="min-h-0 flex-1 overflow-hidden">
              {showEmpty ? (
                <Empty className="h-full border-0">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <MessageCircleDashedIcon />
                    </EmptyMedia>
                    <EmptyTitle>Ask about the inbox</EmptyTitle>
                    <EmptyDescription>
                      InboxAssistant finds matching threads and cites them so you can
                      jump into review. It never sends mail.
                    </EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent>
                    <div className="flex flex-wrap justify-center gap-2">
                      {EXAMPLE_ASKS.map((prompt) => (
                        <Button
                          key={prompt}
                          type="button"
                          variant="outline"
                          size="sm"
                          className="min-h-8 rounded-full"
                          onClick={() => {
                            handleExampleAsk(prompt)
                          }}
                        >
                          {prompt}
                        </Button>
                      ))}
                    </div>
                  </EmptyContent>
                </Empty>
              ) : (
                <MessageScroller>
                  <MessageScrollerViewport>
                    <MessageScrollerContent
                      aria-busy={isAsking}
                      className="gap-4 p-4"
                    >
                      {turns.map((turn) => (
                        <MessageScrollerItem
                          key={turn.id}
                          scrollAnchor={turn.role === "user"}
                          className={cn(
                            "flex",
                            turn.role === "user"
                              ? "justify-end"
                              : "justify-start",
                          )}
                        >
                          <div
                            className={cn(
                              "max-w-[88%] space-y-2",
                              turn.role === "user"
                                ? "items-end"
                                : "min-w-0 flex-1",
                            )}
                          >
                            {turn.role === "assistant" && turn.refusedWrite ? (
                              <Alert variant="warning" className="bg-background">
                                <ShieldAlert aria-hidden="true" />
                                <AlertTitle>Read-only</AlertTitle>
                                <AlertDescription>
                                  InboxAssistant cannot send, approve, or change mail.
                                </AlertDescription>
                              </Alert>
                            ) : null}
                            {turn.error ? (
                              <Alert variant="destructive" className="mb-0">
                                <AlertTitle>Could not ask InboxAssistant</AlertTitle>
                                <AlertDescription>{turn.text}</AlertDescription>
                              </Alert>
                            ) : turn.role === "user" ? (
                              <p className="whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-primary-foreground">
                                {turn.text}
                              </p>
                            ) : turn.text ? (
                              <div className="rounded-2xl rounded-bl-md bg-muted px-3.5 py-2.5 ring-1 ring-foreground/10">
                                {turn.streaming ? (
                                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
                                    {turn.text}
                                  </p>
                                ) : (
                                  <EmailBody
                                    text={turn.text}
                                    className="text-foreground dark:text-foreground"
                                  />
                                )}
                              </div>
                            ) : null}
                            {turn.citations && turn.citations.length > 0 ? (
                              <div className="space-y-2">
                                {turn.citations.map((citation, index) => (
                                  <AskCitationCard
                                    key={citation.thread_id}
                                    citation={citation}
                                    index={index + 1}
                                    mailboxes={mailboxes}
                                  />
                                ))}
                              </div>
                            ) : null}
                          </div>
                        </MessageScrollerItem>
                      ))}
                      {isAsking &&
                      !turns.some(
                        (turn) => turn.role === "assistant" && Boolean(turn.text),
                      ) ? (
                        <MessageScrollerItem className="flex justify-start">
                          <p
                            role="status"
                            aria-live="polite"
                            aria-busy="true"
                            className="thinking-label px-1 py-1 text-sm font-medium"
                          >
                            {toolStatus ?? "Thinking"}
                          </p>
                        </MessageScrollerItem>
                      ) : null}
                    </MessageScrollerContent>
                  </MessageScrollerViewport>
                  <MessageScrollerButton />
                </MessageScroller>
              )}
            </div>

            <footer className="shrink-0 space-y-2 border-t border-border p-3">
              <Select
                items={mailboxItems}
                value={mailbox || null}
                onValueChange={handleMailboxChange}
                disabled={isAsking || mailboxesQuery.isLoading}
              >
                <SelectTrigger
                  aria-label="Mailbox"
                  size="sm"
                  className="h-8 w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent side="top" align="start">
                  <SelectGroup>
                    {mailboxItems.map((item) => (
                      <SelectItem
                        key={item.value ?? "all"}
                        value={item.value}
                      >
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>

              <form onSubmit={handleSubmit}>
                <div
                  className={cn(
                    "flex items-end gap-1.5 border border-border bg-muted/60",
                    composerRows === 1
                      ? "rounded-full py-1 pl-3.5 pr-1"
                      : "rounded-[1.5rem] py-1.5 pl-3.5 pr-1.5",
                  )}
                >
                  <Textarea
                    ref={inputRef}
                    id="inboxassistant-input"
                    name="message"
                    value={message}
                    onChange={handleMessageChange}
                    onKeyDown={handleKeyDown}
                    placeholder="Ask about a thread…"
                    aria-label="Message InboxAssistant"
                    aria-invalid={validation ? true : undefined}
                    disabled={isAsking}
                    rows={composerRows}
                    className="field-sizing-content max-h-40 min-h-8 flex-1 resize-none overflow-y-auto border-0 bg-transparent px-0 py-1.5 leading-5 shadow-none focus-visible:ring-0 dark:bg-transparent"
                  />
                  <Button
                    type="submit"
                    size="icon-sm"
                    aria-label="Send"
                    disabled={isAsking}
                    className="size-8 shrink-0 rounded-full"
                  >
                    <ArrowUpIcon className="size-4" aria-hidden="true" />
                  </Button>
                </div>
              </form>

              {validation ? (
                <p className="text-sm text-red-600 dark:text-red-400">
                  {validation}
                </p>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  <span className="mb-0.5 hidden items-center gap-1.5 sm:inline-flex">
                    <Kbd>⌘</Kbd>
                    <Kbd>↵</Kbd>
                    <span>to send.</span>
                  </span>
                  <span className="sm:ml-1">InboxAssistant never sends mail.</span>
                </p>
              )}
            </footer>
          </div>
        </MessageScrollerProvider>
      </aside>
    </TooltipProvider>
  )
}
