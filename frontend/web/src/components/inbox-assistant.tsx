"use client"

import { useRef, useState } from "react"
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { MessageCircle } from "lucide-react"

import { ChatComposer } from "@/components/chat-composer"
import { ChatTurnList, type ChatTurn } from "@/components/chat-turn-list"
import { InboxAssistantEmptyState } from "@/components/inbox-assistant-empty-state"
import { InboxAssistantPanelHeader } from "@/components/inbox-assistant-panel-header"
import { Button } from "@/components/ui/button"
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller"
import { StarBorder } from "@/components/ui/star-border"
import { TooltipProvider } from "@/components/ui/tooltip"
import { api } from "@/lib/api-client"
import { MAILBOXES_LIST_QUERY_KEY } from "@/lib/query-keys"
import { useInboxAssistantChat } from "@/hooks/use-inbox-assistant-chat"
import type { MailboxOverview } from "@/lib/types"
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
  const sizeIndex = PANEL_SIZES.indexOf(panelSize)
  const canShrink = sizeIndex > 0
  const canGrow = sizeIndex < PANEL_SIZES.length - 1

  const mailboxesQuery = useQuery({
    queryKey: MAILBOXES_LIST_QUERY_KEY,
    queryFn: () => api.mailboxes.list(),
    enabled: open,
  })
  const mailboxes: MailboxOverview[] = mailboxesQuery.data ?? []

  const { resumeStoredSessionIfEmpty, handleReset, handleStop, handleAsk } = useInboxAssistantChat({
    mailbox,
    turns,
    setTurns,
    sessionId,
    setSessionId,
    setMessage,
    setValidation,
    setIsAsking,
    setToolStatus,
  })

  const handleOpen = () => {
    setOpen(true)
    window.requestAnimationFrame(() => {
      inputRef.current?.focus()
    })
    void resumeStoredSessionIfEmpty()
  }

  const handleClose = () => {
    handleStop()
    setOpen(false)
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

  const handleMailboxChange = (value: string | null) => {
    handleReset()
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

  const handleExampleAsk = (prompt: string) => {
    setMessage(prompt)
    void handleAsk(prompt)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void handleAsk(message)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return
    event.preventDefault()
    void handleAsk(message)
  }

  const composerRows = composerRowCount(message)
  const showEmpty = turns.length === 0 && !isAsking

  return (
    <TooltipProvider>
      <div
        className={cn(
          "fixed right-4 bottom-4 z-40 transition duration-200 ease-out sm:right-6 sm:bottom-6",
          open ? "pointer-events-none scale-90 opacity-0" : "scale-100 opacity-100",
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
            className="bg-primary text-primary-foreground hover:bg-primary/80 h-12 gap-2.5 rounded-full px-3.5"
          >
            <MessageCircle className="size-4" aria-hidden="true" />
            <span className="hidden sm:inline">InboxAssistant</span>
          </Button>
        </StarBorder>
      </div>

      <dialog
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
          <div data-slot="card" className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <InboxAssistantPanelHeader
              canShrink={canShrink}
              canGrow={canGrow}
              isAsking={isAsking}
              onShrink={handleShrink}
              onGrow={handleGrow}
              onReset={handleReset}
              onClose={handleClose}
            />

            <div className="min-h-0 flex-1 overflow-hidden">
              {showEmpty ? (
                <InboxAssistantEmptyState onExampleAsk={handleExampleAsk} />
              ) : (
                <MessageScroller>
                  <MessageScrollerViewport>
                    <MessageScrollerContent aria-busy={isAsking} className="gap-4 p-4">
                      <ChatTurnList
                        turns={turns}
                        mailboxes={mailboxes}
                        isAsking={isAsking}
                        toolStatus={toolStatus}
                        onReask={handleReask}
                      />
                    </MessageScrollerContent>
                  </MessageScrollerViewport>
                  <MessageScrollerButton />
                </MessageScroller>
              )}
            </div>

            <ChatComposer
              message={message}
              onMessageChange={handleMessageChange}
              onSubmit={handleSubmit}
              onKeyDown={handleKeyDown}
              isAsking={isAsking}
              validation={validation}
              onStop={handleStop}
              mailbox={mailbox}
              mailboxItems={mailboxItems}
              onMailboxChange={handleMailboxChange}
              mailboxesLoading={mailboxesQuery.isLoading}
              inputRef={inputRef}
              composerRows={composerRows}
            />
          </div>
        </MessageScrollerProvider>
      </dialog>
    </TooltipProvider>
  )
}
