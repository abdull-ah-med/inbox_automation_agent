"use client"

import Link from "next/link"
import { useState } from "react"

import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { formatReviewerDateTime } from "@/lib/dates"
import { REJECT_REASON_LABELS, type RejectReasonCode } from "@/lib/routing"

const reasonCodeLabel = (code: string | null | undefined): string | null => {
  if (!code) return null
  if (code in REJECT_REASON_LABELS) {
    return REJECT_REASON_LABELS[code as RejectReasonCode]
  }
  if (code === "similar") return "Applies to similar emails"
  if (code === "once") return "This thread only"
  return code.replaceAll("_", " ")
}

export type MemoryPreviewItem = {
  id: string
  title: string | null
  senderEmail: string | null
  receiverEmail: string | null
  reasonCode: string | null
  reasonText: string | null
  dateIso: string | null
  previewLine: string | null
  fullBody: string
  threadId: string | null
  badges?: { label: string; tone: "neutral" | "blue" | "green" | "amber" | "red" }[]
  datePrefix: string
}

type MemoryPreviewRowProps = {
  item: MemoryPreviewItem
  excludeLabelInclude: string
  excludeLabelExclude: string
  canExclude: boolean
  excludePending: boolean
  isExcluded: boolean
  onToggleExclude: () => void
}

const formatReasonLine = (reasonLabel: string | null, reasonText: string | null): string | null => {
  if (!reasonLabel && !reasonText) return null
  if (reasonLabel && reasonText) return `Reason: ${reasonLabel} — ${reasonText}`
  if (reasonLabel) return `Reason: ${reasonLabel}`
  return `Reason: ${reasonText}`
}

const ReasonBlock = ({
  reasonLabel,
  reasonText,
  compact,
}: {
  reasonLabel: string | null
  reasonText: string | null
  compact?: boolean
}) => {
  const line = formatReasonLine(reasonLabel, reasonText)
  if (!line) return null
  if (compact) {
    return <p className="truncate text-xs font-medium text-gray-700 dark:text-gray-300">{line}</p>
  }
  return (
    <div className="bg-muted/40 rounded-md px-3 py-2 text-sm">
      <p className="font-medium whitespace-pre-wrap">{line}</p>
    </div>
  )
}

const PreviewTrigger = ({
  item,
  reasonLabel,
  onOpen,
}: {
  item: MemoryPreviewItem
  reasonLabel: string | null
  onOpen: () => void
}) => {
  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return
    event.preventDefault()
    onOpen()
  }

  return (
    <button
      type="button"
      tabIndex={0}
      aria-label={`Open draft: ${item.title ?? "Untitled"}`}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
      className="hover:bg-muted/40 w-full rounded-md px-1 py-1 text-left transition-colors"
    >
      <ReasonBlock reasonLabel={reasonLabel} reasonText={item.reasonText} compact />
      <p className="mt-0.5 truncate text-sm font-medium text-gray-900 dark:text-gray-100">
        {item.title || "(no subject)"}
      </p>
      <p className="text-muted-foreground mt-0.5 truncate text-xs">
        From {item.senderEmail || "—"} · To {item.receiverEmail || "—"}
        {item.dateIso ? ` · ${item.datePrefix} ${formatReviewerDateTime(item.dateIso)}` : ""}
      </p>
      {item.previewLine ? (
        <p className="text-muted-foreground mt-1 truncate text-sm">{item.previewLine}</p>
      ) : null}
    </button>
  )
}

const DraftDetailDialog = ({
  item,
  reasonLabel,
  open,
  onOpenChange,
}: {
  item: MemoryPreviewItem
  reasonLabel: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent size="lg">
      <DialogHeader>
        <DialogTitle>{item.title || "(no subject)"}</DialogTitle>
        <DialogDescription>
          From {item.senderEmail || "—"} · To {item.receiverEmail || "—"}
          {item.dateIso ? ` · ${formatReviewerDateTime(item.dateIso)}` : ""}
        </DialogDescription>
      </DialogHeader>
      <ReasonBlock reasonLabel={reasonLabel} reasonText={item.reasonText} />
      <pre className="border-border/60 max-h-[50vh] overflow-auto rounded-md border p-3 text-sm whitespace-pre-wrap text-gray-800 dark:text-gray-200">
        {item.fullBody}
      </pre>
    </DialogContent>
  </Dialog>
)

export const MemoryPreviewRow = ({
  item,
  excludeLabelInclude,
  excludeLabelExclude,
  canExclude,
  excludePending,
  isExcluded,
  onToggleExclude,
}: MemoryPreviewRowProps) => {
  const [open, setOpen] = useState(false)
  const reasonLabel = reasonCodeLabel(item.reasonCode)

  return (
    <li className="flex flex-wrap items-start justify-between gap-3 p-4">
      <div className="min-w-0 flex-1 space-y-1.5">
        {item.badges && item.badges.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            {item.badges.map((badge) => (
              <StatusBadge
                key={`${badge.label}-${badge.tone}`}
                label={badge.label}
                tone={badge.tone}
              />
            ))}
          </div>
        ) : null}
        <PreviewTrigger item={item} reasonLabel={reasonLabel} onOpen={() => setOpen(true)} />
      </div>

      <div className="flex shrink-0 flex-col items-end gap-2">
        {canExclude ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            tabIndex={0}
            aria-label={isExcluded ? excludeLabelInclude : excludeLabelExclude}
            disabled={excludePending}
            onClick={onToggleExclude}
          >
            {isExcluded ? "Include" : "Exclude"}
          </Button>
        ) : null}
        {item.threadId ? (
          <Link
            href={`/threads/${item.threadId}`}
            className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
            aria-label="Open thread"
            tabIndex={0}
          >
            Open thread
          </Link>
        ) : null}
      </div>

      <DraftDetailDialog item={item} reasonLabel={reasonLabel} open={open} onOpenChange={setOpen} />
    </li>
  )
}
