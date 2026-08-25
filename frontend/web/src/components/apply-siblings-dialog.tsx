"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api-client"
import { formatReviewerDate } from "@/lib/dates"
import { getMutationErrorMessage } from "@/lib/error-messages"
import type { RelatedThreadItem } from "@/lib/types"

export const ApplySiblingsDialog = ({
  open,
  sourceThreadId,
  items,
  treatment,
  reason,
  urgency,
  onOpenChange,
}: {
  open: boolean
  sourceThreadId: string
  items: RelatedThreadItem[]
  treatment: "no_reply" | "urgency"
  reason: string
  urgency?: "CRITICAL" | "HIGH" | "NORMAL" | "LOW"
  onOpenChange: (open: boolean) => void
}) => {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState(() => items.map((item) => item.thread_id))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [success, setSuccess] = useState<string | null>(null)

  if (!open || items.length === 0) {
    return null
  }

  const title =
    treatment === "no_reply"
      ? "Apply no reply to related threads"
      : "Apply urgency to related threads"
  const actionLabel =
    treatment === "no_reply" ? "no reply needed" : `urgency ${urgency ?? ""}`

  const handleToggle = (threadId: string) => {
    setSelected((current) =>
      current.includes(threadId)
        ? current.filter((id) => id !== threadId)
        : [...current, threadId],
    )
  }

  const handleSkip = async () => {
    setBusy(true)
    setError(null)
    try {
      await Promise.all(
        items.map((item) =>
          api.threads.reviewRelated(sourceThreadId, item.thread_id, {
            status: "dismissed",
          }),
        ),
      )
      setSuccess("Skipped. Related threads were left unchanged.")
      await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
      await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
      onOpenChange(false)
    } catch (err) {
      setError(getMutationErrorMessage(err, "Could not skip related threads."))
    } finally {
      setBusy(false)
    }
  }

  const handleApply = async () => {
    setBusy(true)
    setError(null)
    try {
      const skipped = items.filter((item) => !selected.includes(item.thread_id))
      if (selected.length > 0) {
        await api.threads.applyTreatment(sourceThreadId, {
          treatment,
          thread_ids: selected,
          reason,
          ...(treatment === "urgency" && urgency ? { urgency } : {}),
        })
      }
      await Promise.all(
        skipped.map((item) =>
          api.threads.reviewRelated(sourceThreadId, item.thread_id, {
            status: "dismissed",
          }),
        ),
      )
      setSuccess(`Applied ${actionLabel.trim()} to ${selected.length} thread${selected.length === 1 ? "" : "s"}.`)
      await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
      await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
      await queryClient.invalidateQueries({ queryKey: ["thread"] })
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply to related threads.")
    } finally {
      setBusy(false)
    }
  }

  const handleSkipKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      void handleSkip()
    }
  }

  const handleApplyKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      void handleApply()
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg" className="overscroll-contain">
        <DialogHeader>
          <DialogTitle className="text-pretty">{title}</DialogTitle>
          <DialogDescription>
            Apply {actionLabel.trim()} to the threads checked below. Uncheck any
            thread that should stay as-is. Nothing is sent in Outlook.
          </DialogDescription>
        </DialogHeader>
        <ul className="max-h-64 space-y-2 overflow-y-auto overscroll-contain">
          {items.map((item) => {
            const checked = selected.includes(item.thread_id)
            const label = `${item.subject} ${item.mailbox}`
            return (
              <li key={item.thread_id}>
                <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
                  <input
                    type="checkbox"
                    name="sibling"
                    checked={checked}
                    disabled={busy}
                    aria-label={label}
                    className="mt-1"
                    onChange={() => handleToggle(item.thread_id)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                      {item.subject}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-gray-500">
                      {item.mailbox} · {formatReviewerDate(item.last_message_at)} ·{" "}
                      {item.urgency ?? "—"}
                    </span>
                  </span>
                </label>
              </li>
            )
          })}
        </ul>
        {error ? (
          <p className="text-sm text-red-600" role="alert">
            {error}
          </p>
        ) : null}
        {success ? (
          <p className="sr-only" aria-live="polite">
            {success}
          </p>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            tabIndex={0}
            aria-label="Skip"
            disabled={busy}
            onClick={() => {
              void handleSkip()
            }}
            onKeyDown={handleSkipKeyDown}
          >
            Skip
          </Button>
          <Button
            type="button"
            tabIndex={0}
            aria-label="Apply to selected"
            disabled={busy}
            onClick={() => {
              void handleApply()
            }}
            onKeyDown={handleApplyKeyDown}
          >
            {busy ? "Applying…" : "Apply to selected"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
