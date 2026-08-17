"use client"

import { useEffect, useRef, useState } from "react"
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { MessageCircle, Send, ShieldAlert, X } from "lucide-react"
import { ThinkingOrb } from "thinking-orbs"

import { AskCitationCard } from "@/components/ask-citation-card"
import { StarBorder } from "@/components/ui/star-border"
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Select } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api-client"
import { getErrorMessage } from "@/lib/error-messages"
import type { ChatCitation, ChatAskResponse, MailboxOverview } from "@/lib/types"
import { cn } from "@/lib/utils"

const ALL_MAILBOXES = ""

type ChatTurn = {
  id: string
  role: "user" | "assistant"
  text: string
  citations?: ChatCitation[]
  refusedWrite?: boolean
  error?: boolean
}

const EXAMPLE_ASKS = [
  "Billing disputes waiting on review",
  "Spam we filtered about invoices",
  "Threads about SampleLab",
] as const

export const InboxAssistant = () => {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState("")
  const [mailbox, setMailbox] = useState(ALL_MAILBOXES)
  const [validation, setValidation] = useState<string | null>(null)
  const [isAsking, setIsAsking] = useState(false)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const logRef = useRef<HTMLDivElement | null>(null)

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

  useEffect(() => {
    const node = logRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [turns, isAsking])

  const handleOpen = () => {
    setOpen(true)
  }

  const handleClose = () => {
    setOpen(false)
  }

  const handleMailboxChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setMailbox(event.target.value)
  }

  const handleMessageChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(event.target.value)
  }

  const handleAsk = async (nextMessage = message) => {
    const trimmed = nextMessage.trim()
    if (!trimmed) {
      setValidation("Enter a question")
      return
    }
    setValidation(null)
    setMessage("")
    const userTurn: ChatTurn = {
      id: `user-${Date.now()}`,
      role: "user",
      text: trimmed,
    }
    setTurns((current) => [...current, userTurn])
    setIsAsking(true)
    try {
      const payload: { message: string; mailbox?: string } = { message: trimmed }
      if (mailbox) payload.mailbox = mailbox
      const response: ChatAskResponse = await api.chat.ask(payload)
      setTurns((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: "assistant",
          text: response.answer,
          citations: response.citations,
          refusedWrite: response.refused_write,
        },
      ])
    } catch (error) {
      setTurns((current) => [
        ...current,
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          text: getErrorMessage(error),
          error: true,
        },
      ])
    } finally {
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

  return (
    <>
      {!open ? (
        <div className="fixed right-4 bottom-4 z-40 sm:right-6 sm:bottom-6">
          <StarBorder color="#95d5b2" thickness={2}>
            <Button
              type="button"
              tabIndex={0}
              aria-label="InboxAssistant"
              aria-haspopup="dialog"
              aria-expanded={false}
              onClick={handleOpen}
              className="h-12 gap-2.5 rounded-full bg-[#1b4332] px-3.5 text-white hover:bg-[#2d6a4f]"
            >
              <MessageCircle className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">InboxAssistant</span>
            </Button>
          </StarBorder>
        </div>
      ) : null}

      {open ? (
        <aside
          role="dialog"
          aria-modal="false"
          aria-label="InboxAssistant"
          className={cn(
            "fixed z-50 flex flex-col overflow-hidden rounded-[1.75rem] shadow-2xl",
            "bg-[#f6faf7] ring-1 ring-[#1b4332]/12 dark:bg-[#071410] dark:ring-white/10",
            "inset-x-3 top-20 bottom-3",
            "sm:inset-auto sm:right-6 sm:bottom-6 sm:h-[min(40rem,calc(100dvh-5rem))] sm:w-[26rem]",
          )}
        >
          <header className="relative flex items-center gap-3 border-b border-[#1b4332]/10 bg-white/80 px-3.5 py-2.5 backdrop-blur dark:border-white/10 dark:bg-[#0c1f18]/80">
            <span
              aria-hidden="true"
              className="absolute inset-x-0 top-0 h-0.5 bg-[#1b4332]"
            />
            <span aria-hidden="true" className="flex size-8 items-center justify-center">
              <ThinkingOrb state="breathing" size={20} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold tracking-tight text-[#081c15] dark:text-[#d8f3dc]">
                InboxAssistant
              </p>
              <p className="text-[11px] text-muted-foreground">
                Read-only · cites matching threads
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Close InboxAssistant"
              onClick={handleClose}
              className="text-[#1b4332] hover:bg-[#d8f3dc] hover:text-[#1b4332] dark:text-[#d8f3dc] dark:hover:bg-white/10"
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          </header>

          <div
            ref={logRef}
            className="min-h-0 flex-1 overflow-y-auto px-3.5 py-4"
          >
            {turns.length === 0 && !isAsking ? (
              <div className="flex h-full flex-col items-center justify-center px-2 text-center">
                <div aria-hidden="true" className="mb-4">
                  <ThinkingOrb state="breathing" size={64} />
                </div>
                <p className="text-[15px] font-medium tracking-tight text-[#081c15] dark:text-[#d8f3dc]">
                  Ask about the inbox
                </p>
                <p className="mt-1.5 max-w-[17rem] text-xs leading-relaxed text-muted-foreground">
                  InboxAssistant finds matching threads and cites them so you can jump
                  into review. It never sends mail.
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-2">
                  {EXAMPLE_ASKS.map((prompt) => (
                    <Button
                      key={prompt}
                      type="button"
                      variant="outline"
                      size="sm"
                      className="min-h-8 rounded-full border-[#2d6a4f]/25 bg-white text-[#1b4332] hover:bg-[#d8f3dc] dark:border-white/15 dark:bg-transparent dark:text-[#d8f3dc] dark:hover:bg-white/10"
                      onClick={() => {
                        handleExampleAsk(prompt)
                      }}
                    >
                      {prompt}
                    </Button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="space-y-4">
              {turns.map((turn) => (
                <div
                  key={turn.id}
                  className={cn(
                    "flex",
                    turn.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  <div
                    className={cn(
                      "max-w-[88%] space-y-2",
                      turn.role === "user" ? "items-end" : "min-w-0 flex-1",
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
                    ) : (
                      <p
                        className={cn(
                          "whitespace-pre-wrap px-3.5 py-2.5 text-sm leading-relaxed",
                          turn.role === "user"
                            ? "rounded-[1.25rem] rounded-br-md bg-[#1b4332] text-white"
                            : "rounded-[1.25rem] rounded-bl-md bg-white text-[#081c15] ring-1 ring-[#1b4332]/8 dark:bg-[#12241c] dark:text-[#d8f3dc] dark:ring-white/8",
                        )}
                      >
                        {turn.text}
                      </p>
                    )}
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
                </div>
              ))}
            </div>

            {isAsking ? (
              <div className="mt-3 flex justify-start" aria-live="polite" aria-busy="true">
                <ThinkingOrb state="composing" size={64} aria-label="Composing" />
              </div>
            ) : null}
          </div>

          <form
            onSubmit={handleSubmit}
            className="border-t border-[#1b4332]/10 bg-white/90 p-3 dark:border-white/10 dark:bg-[#0c1f18]/90"
          >
            <Select
              id="inboxassistant-mailbox"
              name="mailbox"
              aria-label="Mailbox"
              value={mailbox}
              onChange={handleMailboxChange}
              disabled={isAsking || mailboxesQuery.isLoading}
              className="mb-2 h-8 border-[#1b4332]/15 bg-transparent px-2.5 text-xs dark:border-white/15"
            >
              <option value={ALL_MAILBOXES}>All mailboxes</option>
              {mailboxes.map((item) => (
                <option key={item.mailbox} value={item.email_address}>
                  {item.label}
                </option>
              ))}
            </Select>
            <div className="flex items-end gap-2 rounded-2xl border border-[#1b4332]/15 bg-[#f6faf7] p-2 shadow-inner dark:border-white/10 dark:bg-[#071410]">
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
                rows={2}
                className="min-h-12 resize-none border-0 bg-transparent px-2 py-1 shadow-none focus-visible:ring-0 dark:bg-transparent"
              />
              <Button
                type="submit"
                size="icon"
                aria-label="Send"
                disabled={isAsking}
                className="size-10 shrink-0 rounded-full bg-[#1b4332] text-white hover:bg-[#2d6a4f]"
              >
                <Send className="size-4" aria-hidden="true" />
              </Button>
            </div>
            {validation ? (
              <p className="mt-2 text-sm text-red-600 dark:text-red-400">{validation}</p>
            ) : (
              <p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Kbd>⌘</Kbd>
                <Kbd>↵</Kbd>
                <span>to send. InboxAssistant never sends mail.</span>
              </p>
            )}
          </form>
        </aside>
      ) : null}
    </>
  )
}
