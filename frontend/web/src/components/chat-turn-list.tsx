"use client"

import { CircleAlert, ShieldAlert } from "lucide-react"

import { AskCitationCard } from "@/components/ask-citation-card"
import { StreamingEmailBody } from "@/components/streaming-email-body"
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  MessageScrollerItem,
} from "@/components/ui/message-scroller"
import type { ChatGroundedVerifier } from "@/lib/chat-stream"
import type { ChatCitation, MailboxOverview } from "@/lib/types"
import { cn } from "@/lib/utils"

export type ChatTurn = {
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

type ChatTurnListProps = {
  turns: ChatTurn[]
  mailboxes: MailboxOverview[]
  isAsking: boolean
  toolStatus: string | null
  onReask: (turn: ChatTurn) => void
}

export const ChatTurnList = ({
  turns,
  mailboxes,
  isAsking,
  toolStatus,
  onReask,
}: ChatTurnListProps) => {
  const handleReask = (turn: ChatTurn) => {
    onReask(turn)
  }

  return (
    <>
      {turns.map((turn) => (
        <MessageScrollerItem
          key={turn.id}
          className={cn(
            "flex",
            turn.role === "user" ? "justify-end" : "justify-start",
          )}
        >
          <div
            className={cn(
              "max-w-[88%] min-w-0 space-y-2",
              turn.role === "user" ? "items-end" : "flex-1",
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
                className="wrap-anywhere rounded-2xl rounded-bl-md bg-muted px-3.5 py-2.5 ring-1 ring-foreground/10"
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
                    Some claims in this answer could not be confirmed from the
                    cited threads. Review the source threads to verify.
                  </p>
                ) : turn.groundedVerifier === "UNKNOWN" ? (
                  <p
                    id={`${turn.id}-groundedness`}
                    className="mb-2 text-xs text-muted-foreground"
                    tabIndex={0}
                    aria-label="This answer could not be verified in time. Review the source threads to confirm the details."
                  >
                    This answer could not be verified in time. Review the
                    source threads to confirm the details.
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
    </>
  )
}
