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
  CircleAlert,
  ShieldAlert,
  SquareIcon,
  X,
} from "lucide-react"

import { AskCitationCard } from "@/components/ask-citation-card"
import { StreamingEmailBody } from "@/components/streaming-email-body"
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
import type { ChatGroundedVerifier } from "@/lib/chat-stream"
import { toChatHistoryPayload } from "@/lib/chat-history"
import {
  clearStoredSessionId,
  readStoredSessionId,
  writeStoredSessionId,
} from "@/lib/chat-session-storage"
import { createRafDeltaBatcher } from "@/lib/stream-delta-batcher"
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
  cached?: boolean
  groundedVerifier?: ChatGroundedVerifier
  lastQuestion?: string
}

type PendingAskMeta = {
  citations: ChatCitation[]
  refusedWrite: boolean
  cached: boolean
  groundedVerifier?: ChatGroundedVerifier
}

const EXAMPLE_ASKS = [
  "What should I focus on today?",
  "Billing disputes waiting on review",
  "Threads about SampleLab",
] as const

const CitationList = ({
  citations,
  mailboxes,
}: {
  citations: ChatCitation[]
  mailboxes: MailboxOverview[]
}) => {
  return (
    <div className="space-y-2">
      {citations.map((citation, index) => (
        <div key={citation.thread_id} id={`inboxassistant-cite-${index + 1}`}>
          <AskCitationCard
            citation={citation}
            index={index + 1}
            mailboxes={mailboxes}
          />
        </div>
      ))}
    </div>
  )
}

export const InboxAssistant = () => {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState("")
  const [mailbox, setMailbox] = useState(ALL_MAILBOXES)
  const [validation, setValidation] = useState<string | null>(null)
  const [isAsking, setIsAsking] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [panelSize, setPanelSize] = useState<PanelSize>("compact")
  const [toolStatus, setToolStatus] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const pendingMetaRef = useRef<PendingAskMeta | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const deltaBatcherRef = useRef<ReturnType<typeof createRafDeltaBatcher> | null>(
    null,
  )
  const sizeIndex = PANEL_SIZES.indexOf(panelSize)
  const canShrink = sizeIndex > 0
  const canGrow = sizeIndex < PANEL_SIZES.length - 1

  const mailboxesQuery = useQuery({
    queryKey: ["mailboxes", "list"],
    queryFn: () => api.mailboxes.list(),
    enabled: open,
  })
  const mailboxes: MailboxOverview[] = mailboxesQuery.data ?? []

  const resumeStoredSessionIfEmpty = async () => {
    if (turns.length > 0) return
    const stored = readStoredSessionId(mailbox)
    if (!stored) return
    try {
      const session = await api.chat.getSession(stored)
      setSessionId((current) => current ?? session.session_id)
      setTurns((current) => {
        if (current.length > 0) return current
        return session.messages.map((item, index) => ({
          id: `restored-${session.session_id}-${index}`,
          role: item.role,
          text: item.content,
        }))
      })
    } catch {
      clearStoredSessionId(mailbox)
    }
  }

  const handleOpen = () => {
    setOpen(true)
    window.requestAnimationFrame(() => {
      inputRef.current?.focus()
    })
    void resumeStoredSessionIfEmpty()
  }

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const ensureSessionId = async (): Promise<string> => {
    if (sessionId) return sessionId
    const created = await api.chat.createSession({
      mailbox: mailbox || null,
    })
    writeStoredSessionId(mailbox, created.session_id)
    setSessionId(created.session_id)
    return created.session_id
  }

  const handleClose = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setOpen(false)
  }

  const handleReset = () => {
    abortRef.current?.abort()
    abortRef.current = null
    pendingMetaRef.current = null
    clearStoredSessionId(mailbox)
    setSessionId(null)
    setToolStatus(null)
    setTurns([])
    setMessage("")
    setValidation(null)
    setIsAsking(false)
  }

  const handleShrink = () => {
    if (!canShrink) return
    setPanelSize(PANEL_SIZES[sizeIndex - 1])
  }

  const handleGrow = () => {
    if (!canGrow) return
    setPanelSize(PANEL_SIZES[sizeIndex + 1])
  }

  const handleReask = (turn: ChatTurn) => {
    if (!turn.lastQuestion) return
    void handleAsk(turn.lastQuestion, { bypassCache: true })
  }

  const handleStop = () => {
    abortRef.current?.abort()
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

  const handleAsk = async (
    nextMessage = message,
    options?: { bypassCache?: boolean },
  ) => {
    const trimmed = sanitizeUserText(nextMessage)
    if (!trimmed) {
      setValidation("Enter a question")
      return
    }
    setValidation(null)
    setMessage("")
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
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
    let receivedAnswer = false
    deltaBatcherRef.current?.flushNow()
    const deltaBatcher = createRafDeltaBatcher((chunk) => {
      setTurns((current) => {
        const existing = current.find((turn) => turn.id === assistantId)
        const pendingCitations = pendingMetaRef.current?.citations
        if (existing) {
          return current.map((turn) =>
            turn.id === assistantId
              ? {
                  ...turn,
                  text: `${turn.text}${chunk}`,
                  streaming: true,
                  citations: turn.citations ?? pendingCitations,
                }
              : turn,
          )
        }
        return [
          ...current,
          {
            id: assistantId,
            role: "assistant",
            text: chunk,
            streaming: true,
            citations: pendingCitations,
          },
        ]
      })
    })
    deltaBatcherRef.current = deltaBatcher
    try {
      const activeSessionId = await ensureSessionId()
      const payload: ChatAskRequest = {
        message: trimmed,
        session_id: activeSessionId,
      }
      if (mailbox) payload.mailbox = mailbox
      if (history.length > 0) payload.history = toChatHistoryPayload(history)
      if (options?.bypassCache) payload.bypass_cache = true
      await api.chat.askStream(payload, {
        onStatus: (text) => {
          setToolStatus(text)
        },
        onMeta: (meta) => {
          pendingMetaRef.current = {
            citations: meta.citations,
            refusedWrite: meta.refused_write,
            cached: Boolean(meta.cached),
            groundedVerifier: meta.grounded_verifier,
          }
          setTurns((current) =>
            current.map((turn) =>
              turn.id === assistantId
                ? {
                    ...turn,
                    citations: meta.citations,
                    refusedWrite: meta.refused_write,
                    cached: Boolean(meta.cached),
                    groundedVerifier: meta.grounded_verifier,
                  }
                : turn,
            ),
          )
        },
        onDelta: (text) => {
          if (text) receivedAnswer = true
          deltaBatcher.push(text)
        },
        onDone: (verdict) => {
          const trailing = deltaBatcher.drain()
          const pending = pendingMetaRef.current
          pendingMetaRef.current = null
          setToolStatus(null)
          const citations = pending?.citations ?? []
          const refusedWrite = pending?.refusedWrite ?? false
          const cached = pending?.cached ?? false
          // H1: the backend now sends the real, post-verification verdict on
          // "done" (the eager "meta" only ever carries the placeholder
          // "SKIPPED" before the answer finishes). Fall back to whatever meta
          // carried only for older/alternate code paths that might omit it.
          const groundedVerifier = verdict ?? pending?.groundedVerifier
          setTurns((current) => {
            const existing = current.find((turn) => turn.id === assistantId)
            if (existing) {
              return current.map((turn) =>
                turn.id === assistantId
                  ? {
                      ...turn,
                      text: trailing ? `${turn.text}${trailing}` : turn.text,
                      citations,
                      refusedWrite,
                      cached,
                      groundedVerifier,
                      lastQuestion: trimmed,
                      streaming: false,
                    }
                  : turn,
              )
            }
            if (!trailing && citations.length === 0) {
              return current
            }
            return [
              ...current,
              {
                id: assistantId,
                role: "assistant",
                text: trailing,
                citations,
                refusedWrite,
                cached,
                groundedVerifier,
                lastQuestion: trimmed,
                streaming: false,
              },
            ]
          })
        },
      }, controller.signal)
      deltaBatcher.flushNow()
      if (!receivedAnswer) {
        throw new Error("InboxAssistant did not return an answer. Please try again.")
      }
    } catch (error) {
      deltaBatcher.flushNow()
      if (
        controller.signal.aborted ||
        (error instanceof DOMException && error.name === "AbortError") ||
        (error instanceof Error && error.name === "AbortError")
      ) {
        pendingMetaRef.current = null
        setToolStatus(null)
        setTurns((current) =>
          current.map((turn) =>
            turn.id === assistantId ? { ...turn, streaming: false } : turn,
          ),
        )
        return
      }
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
      if (deltaBatcherRef.current === deltaBatcher) {
        deltaBatcherRef.current = null
      }
      if (abortRef.current === controller) {
        pendingMetaRef.current = null
        setIsAsking(false)
      }
    }
  }

  const handleExampleAsk = (prompt: string) => {
    setMessage(prompt)
    void handleAsk(prompt)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isAsking && !sanitizeUserText(message)) return
    void handleAsk()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return
    event.preventDefault()
    if (isAsking && !sanitizeUserText(message)) return
    void handleAsk()
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

      <dialog
        role="dialog"
        aria-modal={open}
        aria-label="InboxAssistant"
        aria-hidden={!open}
        open
        inert={!open}
        data-state={open ? "open" : "closed"}
        className={cn(
          "fixed top-auto left-auto start-auto z-50 m-0 flex flex-col overflow-hidden rounded-2xl bg-card p-0 shadow-2xl ring-1 ring-foreground/10",
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
                          className={cn(
                            "flex",
                            turn.role === "user"
                              ? "justify-end"
                              : "justify-start",
                          )}
                        >
                          <div
                            className={cn(
                              "max-w-[88%] min-w-0 space-y-2",
                              turn.role === "user"
                                ? "items-end"
                                : "flex-1",
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
                                <CircleAlert aria-hidden="true" />
                                <AlertTitle>Could not ask InboxAssistant</AlertTitle>
                                <AlertDescription>{turn.text}</AlertDescription>
                              </Alert>
                            ) : turn.role === "user" ? (
                              <p className="wrap-anywhere whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-primary-foreground">
                                {turn.text}
                              </p>
                            ) : turn.text ? (
                              <div
                                className={cn(
                                  "wrap-anywhere rounded-2xl rounded-bl-md bg-muted px-3.5 py-2.5 ring-1 ring-foreground/10",
                                  (turn.groundedVerifier === "UNSUPPORTED" ||
                                    turn.groundedVerifier === "UNKNOWN") &&
                                    "opacity-70",
                                )}
                                aria-describedby={
                                  turn.groundedVerifier === "UNSUPPORTED" ||
                                  turn.groundedVerifier === "UNKNOWN"
                                    ? `${turn.id}-groundedness`
                                    : undefined
                                }
                              >
                                {turn.cached ? (
                                  <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                                    <span>Cached · Re-ask to refresh</span>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      className="h-7 px-2"
                                      aria-label="Re-ask now"
                                      onClick={() => handleReask(turn)}
                                    >
                                      Re-ask
                                    </Button>
                                  </div>
                                ) : null}
                                {turn.groundedVerifier === "UNSUPPORTED" ? (
                                  <p
                                    id={`${turn.id}-groundedness`}
                                    className="mb-2 text-xs text-muted-foreground"
                                    tabIndex={0}
                                    aria-label="Some claims in this answer could not be confirmed from the cited threads. Review the source threads to verify."
                                  >
                                    Some claims in this answer could not be
                                    confirmed from the cited threads. Review the
                                    source threads to verify.
                                  </p>
                                ) : turn.groundedVerifier === "UNKNOWN" ? (
                                  <p
                                    id={`${turn.id}-groundedness`}
                                    className="mb-2 text-xs text-muted-foreground"
                                    tabIndex={0}
                                    aria-label="This answer could not be verified in time. Review the source threads to confirm the details."
                                  >
                                    This answer could not be verified in time.
                                    Review the source threads to confirm the
                                    details.
                                  </p>
                                ) : null}
                                <StreamingEmailBody
                                  text={turn.text}
                                  streaming={Boolean(turn.streaming)}
                                  citations={turn.citations}
                                />
                              </div>
                            ) : null}
                            {turn.citations &&
                            turn.citations.length > 0 &&
                            !turn.streaming ? (
                              <CitationList
                                citations={turn.citations}
                                mailboxes={mailboxes}
                              />
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
                    "flex min-w-0 items-end gap-2 rounded-2xl border border-border bg-muted/60 px-3 py-2",
                    "transition-[box-shadow,border-color] focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/20",
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
                    aria-describedby={validation ? "inboxassistant-validation" : undefined}
                    rows={composerRows}
                    className="field-sizing-content max-h-40 min-h-8 flex-1 resize-none overflow-y-auto wrap-anywhere border-0 bg-transparent px-0 py-1.5 leading-6 shadow-none focus-visible:ring-0 dark:bg-transparent"
                  />
                  {isAsking ? (
                    <Button
                      type="button"
                      size="icon-sm"
                      aria-label="Stop generating"
                      onClick={handleStop}
                      className="mb-0.5 size-8 shrink-0 rounded-full"
                    >
                      <SquareIcon className="size-3.5 fill-current" aria-hidden="true" />
                    </Button>
                  ) : (
                    <Button
                      type="submit"
                      size="icon-sm"
                      aria-label="Send"
                      className="mb-0.5 size-8 shrink-0 rounded-full"
                    >
                      <ArrowUpIcon className="size-4" aria-hidden="true" />
                    </Button>
                  )}
                </div>
              </form>

              {validation ? (
                <p
                  id="inboxassistant-validation"
                  className="text-sm text-red-600 dark:text-red-400"
                  role="alert"
                >
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
      </dialog>
    </TooltipProvider>
  )
}
