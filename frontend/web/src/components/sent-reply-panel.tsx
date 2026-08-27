"use client"

import { type KeyboardEvent, type ReactNode, useState } from "react"

import { EmailBody, splitQuotedHistory } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import type { DraftVsSentDiff, DraftView, SentReplyView } from "@/lib/types"
import { cn, textActionClass } from "@/lib/utils"

const MATCHED_BY_LABEL: Record<SentReplyView["matched_by"], string> = {
  approved_draft: "Approved draft",
  time_window: "Recent draft (48h)",
  manual: "Manual",
}

/** Highlight only when a minority of reply lines changed — full rewrites look broken as red strike-through. */
const SUBSTANTIAL_REWRITE_RATIO = 0.5

type SentReplyPanelProps = {
  sentReply: SentReplyView
  draft: DraftView | null
  diff: DraftVsSentDiff | null | undefined
}

const nonEmptyLines = (text: string): string[] =>
  text.split("\n").filter((line) => line.trim().length > 0)

const isSubstantialRewrite = (
  proposed: string,
  sentMain: string,
  removed: Set<string>,
  added: Set<string>,
): boolean => {
  const proposedLines = nonEmptyLines(proposed)
  const sentLines = nonEmptyLines(sentMain)
  if (proposedLines.length === 0 && sentLines.length === 0) {
    return false
  }

  const removedHits =
    proposedLines.length === 0
      ? 0
      : proposedLines.filter((line) => removed.has(line)).length / proposedLines.length
  const addedHits =
    sentLines.length === 0
      ? 0
      : sentLines.filter((line) => added.has(line)).length / sentLines.length

  return removedHits > SUBSTANTIAL_REWRITE_RATIO || addedHits > SUBSTANTIAL_REWRITE_RATIO
}

const highlightLines = (text: string, highlight: Set<string>, kind: "added" | "removed") => {
  const lines = text.split("\n")
  return lines.map((line, index) => {
    const isHit = highlight.has(line)
    const className = isHit
      ? kind === "added"
        ? "break-words bg-green-50 underline decoration-green-600 dark:bg-green-950/30"
        : "break-words bg-red-50 line-through decoration-red-600 dark:bg-red-950/30"
      : "break-words"
    return (
      <span key={`${index}-${line.slice(0, 24)}`} className={className}>
        {line}
        {index < lines.length - 1 ? "\n" : ""}
      </span>
    )
  })
}

const BodyBox = ({ children }: { children: ReactNode }) => (
  <Card size="sm" className="bg-muted/50 ring-foreground/5 min-w-0 gap-0 py-3.5">
    <CardContent className="text-sm">{children}</CardContent>
  </Card>
)

const ProposedDraftColumn = ({
  proposed,
  showLineHighlights,
  removed,
}: {
  proposed: string
  showLineHighlights: boolean
  removed: Set<string>
}) => (
  <div className="min-w-0">
    <h3 className="text-muted-foreground mb-2.5 text-[11px] font-medium tracking-wider uppercase">
      Proposed draft
    </h3>
    {proposed ? (
      <BodyBox>
        {showLineHighlights && removed.size > 0 ? (
          <div className="min-w-0 overflow-hidden leading-relaxed break-words whitespace-pre-wrap text-gray-700 dark:text-gray-300">
            {highlightLines(proposed, removed, "removed")}
          </div>
        ) : (
          <EmailBody text={proposed} />
        )}
      </BodyBox>
    ) : (
      <BodyBox>
        <p className="text-muted-foreground text-sm">No proposed draft available.</p>
      </BodyBox>
    )}
  </div>
)

const SentBodyColumn = ({
  sentBody,
  showLineHighlights,
  added,
}: {
  sentBody: string
  showLineHighlights: boolean
  added: Set<string>
}) => (
  <div className="min-w-0">
    <h3 className="text-muted-foreground mb-2.5 text-[11px] font-medium tracking-wider uppercase">
      Sent reply
    </h3>
    <BodyBox>
      {showLineHighlights && added.size > 0 ? (
        <div className="min-w-0 overflow-hidden leading-relaxed break-words whitespace-pre-wrap text-gray-700 dark:text-gray-300">
          {highlightLines(sentBody, added, "added")}
        </div>
      ) : (
        <EmailBody text={sentBody} collapseQuotes />
      )}
    </BodyBox>
  </div>
)

export const SentReplyPanel = ({ sentReply, draft, diff }: SentReplyPanelProps) => {
  const [open, setOpen] = useState(true)
  const proposed = draft?.edited_body || draft?.body || ""
  const sentBody = sentReply.sent_body_snapshot
  const { main: sentMain } = splitQuotedHistory(sentBody)
  const added = new Set(diff?.added ?? [])
  const removed = new Set(diff?.removed ?? [])
  const hasDiff = added.size > 0 || removed.size > 0
  const substantial = hasDiff && isSubstantialRewrite(proposed, sentMain, removed, added)
  const showLineHighlights = hasDiff && !substantial

  const handleToggle = () => {
    setOpen((prev) => !prev)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggle()
    }
  }

  return (
    <Card className="mb-5 min-w-0 gap-0 py-0" aria-label="Sent reply comparison">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-x-3 gap-y-2 px-5 py-3.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
          <CardTitle className="text-sm leading-none font-semibold">Sent reply</CardTitle>
          <StatusBadge label="Resolved" tone="green" />
          <span className="text-muted-foreground text-xs leading-none">
            Matched by: {MATCHED_BY_LABEL[sentReply.matched_by]}
          </span>
        </div>
        <button
          type="button"
          tabIndex={0}
          aria-label={open ? "Collapse sent reply panel" : "Expand sent reply panel"}
          aria-expanded={open}
          onClick={handleToggle}
          onKeyDown={handleKeyDown}
          className={cn(
            textActionClass,
            "cursor-pointer text-xs leading-none font-medium text-muted-foreground hover:text-foreground",
          )}
        >
          {open ? "Hide" : "Show"}
        </button>
      </CardHeader>
      {open ? <Separator /> : null}

      {open ? (
        <CardContent className="grid min-w-0 grid-cols-1 gap-5 px-5 pt-4 pb-5 lg:grid-cols-2">
          {substantial ? (
            <p className="text-muted-foreground text-xs lg:col-span-2">
              The sent reply was substantially edited from the proposed draft. Showing both without
              line-by-line strike-through.
            </p>
          ) : null}
          <ProposedDraftColumn
            proposed={proposed}
            showLineHighlights={showLineHighlights}
            removed={removed}
          />
          <SentBodyColumn
            sentBody={sentBody}
            showLineHighlights={showLineHighlights}
            added={added}
          />
        </CardContent>
      ) : null}
    </Card>
  )
}
