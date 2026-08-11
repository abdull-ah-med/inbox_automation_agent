"use client"

import { useState, type KeyboardEvent, type ReactNode } from "react"

import { EmailBody, splitQuotedHistory } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import type { DraftVsSentDiff, DraftView, SentReplyView } from "@/lib/types"

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

const highlightLines = (
  text: string,
  highlight: Set<string>,
  kind: "added" | "removed",
) => {
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
  <div className="min-w-0 overflow-hidden rounded border border-gray-100 bg-gray-50 p-3 text-sm dark:border-gray-800 dark:bg-gray-950">
    {children}
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
    <section
      className="mb-5 min-w-0 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
      aria-label="Sent reply comparison"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            Sent reply
          </h2>
          <StatusBadge label="Resolved" tone="green" />
          <span className="text-xs text-gray-500">
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
          className="cursor-pointer text-xs font-medium text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
        >
          {open ? "Hide" : "Show"}
        </button>
      </div>

      {open ? (
        <div className="grid min-w-0 grid-cols-1 gap-4 p-4 lg:grid-cols-2">
          {substantial ? (
            <p className="text-xs text-gray-500 lg:col-span-2">
              The sent reply was substantially edited from the proposed draft.
              Showing both without line-by-line strike-through.
            </p>
          ) : null}
          <div className="min-w-0">
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">
              Proposed draft
            </h3>
            {proposed ? (
              <BodyBox>
                {showLineHighlights && removed.size > 0 ? (
                  <div className="min-w-0 overflow-hidden whitespace-pre-wrap break-words leading-relaxed text-gray-700 dark:text-gray-300">
                    {highlightLines(proposed, removed, "removed")}
                  </div>
                ) : (
                  <EmailBody text={proposed} />
                )}
              </BodyBox>
            ) : (
              <p className="text-sm text-gray-500">No proposed draft available.</p>
            )}
          </div>
          <div className="min-w-0">
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">
              Sent reply
            </h3>
            <BodyBox>
              {showLineHighlights && added.size > 0 ? (
                <div className="min-w-0 overflow-hidden whitespace-pre-wrap break-words leading-relaxed text-gray-700 dark:text-gray-300">
                  {highlightLines(sentBody, added, "added")}
                </div>
              ) : (
                <EmailBody text={sentBody} collapseQuotes />
              )}
            </BodyBox>
          </div>
        </div>
      ) : null}
    </section>
  )
}
