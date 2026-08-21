"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api-client"
import type { ThreadPresentation } from "@/lib/types"

export const ResolutionBanner = ({
  threadId,
  presentation,
  urgencyAssessed,
}: {
  threadId: string
  presentation: ThreadPresentation | null | undefined
  urgencyAssessed: string | null
}) => {
  const queryClient = useQueryClient()
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const feedbackMutation = useMutation({
    mutationFn: (action: "reopen" | "wrong_reason") =>
      api.threads.resolutionFeedback(threadId, { action }),
    onSuccess: async () => {
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : "Could not update resolution.")
    },
  })

  if (!presentation?.show_resolution_banner || dismissed) return null

  const urgencyNote = urgencyAssessed
    ? ` Assessed urgency was ${urgencyAssessed}; it no longer drives priority.`
    : ""

  const handleReopen = () => {
    feedbackMutation.mutate("reopen")
  }

  const handleWrongReason = () => {
    feedbackMutation.mutate("wrong_reason")
  }

  const handleDismiss = () => {
    setDismissed(true)
  }

  return (
    <div
      role="status"
      className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-100"
    >
      <p className="font-medium">This thread was resolved automatically</p>
      <p className="mt-1 text-emerald-900/90 dark:text-emerald-100/90">
        Removed from Needs Attention.
        {urgencyNote} You can reopen if work is still open.
      </p>
      {error ? (
        <p className="mt-2 text-xs text-red-700 dark:text-red-300" role="alert">
          {error}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="cursor-pointer rounded-md bg-emerald-800 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-emerald-900 disabled:opacity-50"
          aria-label="Mark thread still open"
          disabled={feedbackMutation.isPending}
          onClick={handleReopen}
        >
          Still open?
        </button>
        <button
          type="button"
          className="cursor-pointer rounded-md border border-emerald-300 bg-white px-2.5 py-1.5 text-xs font-medium text-emerald-900 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-900 dark:text-emerald-50"
          aria-label="Wrong auto-resolve reason"
          disabled={feedbackMutation.isPending}
          onClick={handleWrongReason}
        >
          Wrong reason
        </button>
        <button
          type="button"
          className="cursor-pointer rounded-md px-2.5 py-1.5 text-xs font-medium text-emerald-800 underline-offset-2 hover:underline dark:text-emerald-200"
          aria-label="Dismiss resolution banner"
          onClick={handleDismiss}
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
