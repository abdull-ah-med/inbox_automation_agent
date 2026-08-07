"use client"

import { useState, type KeyboardEvent } from "react"

import { EmailBody } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import type { DraftVsSentDiff, DraftView, SentReplyView } from "@/lib/types"

const MATCHED_BY_LABEL: Record<SentReplyView["matched_by"], string> = {
  approved_draft: "Approved draft",
  time_window: "Recent draft (48h)",
  manual: "Manual",
}

type SentReplyPanelProps = {
  sentReply: SentReplyView
  draft: DraftView | null
  diff: DraftVsSentDiff | null | undefined
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
        ? "bg-green-50 underline decoration-green-600 dark:bg-green-950/30"
        : "bg-red-50 line-through decoration-red-600 dark:bg-red-950/30"
      : undefined
    return (
      <span key={`${index}-${line.slice(0, 24)}`} className={className}>
        {line}
        {index < lines.length - 1 ? "\n" : ""}
      </span>
    )
  })
}

export const SentReplyPanel = ({ sentReply, draft, diff }: SentReplyPanelProps) => {
  const [open, setOpen] = useState(true)
  const proposed = draft?.edited_body || draft?.body || ""
  const added = new Set(diff?.added ?? [])
  const removed = new Set(diff?.removed ?? [])

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
      className="mb-5 rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
      aria-label="Sent reply comparison"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
        <div className="flex flex-wrap items-center gap-2">
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
        <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-2">
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">
              Proposed draft
            </h3>
            {proposed ? (
              <div className="rounded border border-gray-100 bg-gray-50 p-3 text-sm whitespace-pre-wrap dark:border-gray-800 dark:bg-gray-950">
                {diff && removed.size > 0
                  ? highlightLines(proposed, removed, "removed")
                  : <EmailBody text={proposed} />}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No proposed draft available.</p>
            )}
          </div>
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">
              Sent reply
            </h3>
            <div className="rounded border border-gray-100 bg-gray-50 p-3 text-sm whitespace-pre-wrap dark:border-gray-800 dark:bg-gray-950">
              {diff && added.size > 0
                ? highlightLines(sentReply.sent_body_snapshot, added, "added")
                : <EmailBody text={sentReply.sent_body_snapshot} />}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
