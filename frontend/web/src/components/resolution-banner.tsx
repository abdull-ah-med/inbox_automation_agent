"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CircleCheck } from "lucide-react"

import { Button } from "@/components/ui/button"
import { api } from "@/lib/api-client"
import { getMutationErrorMessage } from "@/lib/error-messages"
import type { ActivityEntry, ThreadPresentation } from "@/lib/types"
import { cn } from "@/lib/utils"

type FeedbackOutcome = "reopened" | "wrong_reason"

const OUTCOME_COPY: Record<FeedbackOutcome, { title: string; body: string }> = {
  reopened: {
    title: "Reopened",
    body: "Back in Needs Attention when a draft awaits review.",
  },
  wrong_reason: {
    title: "Reason noted",
    body: "We recorded that the auto-resolve reason was wrong.",
  },
}

const bannerShellClass = cn(
  "mb-5 flex gap-3 rounded-xl border border-emerald-200/80 bg-emerald-50/90 px-4 py-3.5 text-sm text-emerald-950",
  "shadow-sm shadow-emerald-900/5",
  "dark:border-emerald-800/50 dark:bg-emerald-950/35 dark:text-emerald-50",
)

const outcomeFromActivity = (activity: ActivityEntry[] | undefined): FeedbackOutcome | null => {
  if (!activity?.length) return null
  if (activity.some((entry) => entry.event_type === "thread.resolved.wrong_reason")) {
    return "wrong_reason"
  }
  if (activity.some((entry) => entry.event_type === "thread.reopened.resolution_feedback")) {
    return "reopened"
  }
  return null
}

export const ResolutionBanner = ({
  threadId,
  presentation,
  urgencyAssessed,
  activity,
}: {
  threadId: string
  presentation: ThreadPresentation | null | undefined
  urgencyAssessed: string | null
  activity?: ActivityEntry[]
}) => {
  const queryClient = useQueryClient()
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<FeedbackOutcome | null>(null)

  const feedbackMutation = useMutation({
    mutationFn: (action: "reopen" | "wrong_reason") =>
      api.threads.resolutionFeedback(threadId, { action }),
    onSuccess: async (_data, action) => {
      setError(null)
      setOutcome(action === "reopen" ? "reopened" : "wrong_reason")
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
    },
    onError: (err: unknown) => {
      setError(getMutationErrorMessage(err, "Could not update resolution."))
    },
  })

  const effectiveOutcome = outcome ?? outcomeFromActivity(activity)

  if (dismissed) return null
  if (!effectiveOutcome && !presentation?.show_resolution_banner) return null

  const handleReopen = () => {
    feedbackMutation.mutate("reopen")
  }

  const handleWrongReason = () => {
    feedbackMutation.mutate("wrong_reason")
  }

  const handleDismiss = () => {
    setDismissed(true)
  }

  if (effectiveOutcome) {
    const copy = OUTCOME_COPY[effectiveOutcome]
    return (
      <div role="status" aria-live="polite" className={bannerShellClass}>
        <CircleCheck
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400"
        />
        <div className="min-w-0 flex-1">
          <p className="font-medium tracking-tight text-emerald-950 dark:text-emerald-50">
            {copy.title}
          </p>
          <p className="mt-1 text-emerald-800/85 dark:text-emerald-100/75">{copy.body}</p>
          <div className="mt-3">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="cursor-pointer text-emerald-800 hover:bg-emerald-100/80 dark:text-emerald-200 dark:hover:bg-emerald-900/40"
              aria-label="Dismiss resolution banner"
              onClick={handleDismiss}
            >
              Dismiss
            </Button>
          </div>
        </div>
      </div>
    )
  }

  const urgencyNote = urgencyAssessed
    ? ` Assessed urgency was ${urgencyAssessed}; it no longer drives priority.`
    : ""

  return (
    <div role="status" className={bannerShellClass}>
      <CircleCheck
        aria-hidden="true"
        className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400"
      />
      <div className="min-w-0 flex-1">
        <p className="font-medium tracking-tight text-emerald-950 dark:text-emerald-50">
          This thread was resolved automatically
        </p>
        <p className="mt-1 max-w-2xl leading-relaxed text-emerald-800/85 dark:text-emerald-100/75">
          Removed from Needs Attention.
          {urgencyNote} You can reopen if work is still open.
        </p>
        {error ? (
          <p className="mt-2 text-xs text-red-700 dark:text-red-300" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            className="cursor-pointer bg-emerald-700 text-white hover:bg-emerald-800 dark:bg-emerald-500 dark:text-emerald-950 dark:hover:bg-emerald-400"
            aria-label="Mark thread still open"
            disabled={feedbackMutation.isPending}
            onClick={handleReopen}
          >
            Still open?
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="cursor-pointer border-emerald-300/80 bg-white/90 text-emerald-950 hover:bg-emerald-100 dark:border-emerald-700/80 dark:bg-emerald-950/80 dark:text-emerald-50 dark:hover:bg-emerald-900/70"
            aria-label="Wrong auto-resolve reason"
            disabled={feedbackMutation.isPending}
            onClick={handleWrongReason}
          >
            Wrong reason
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="cursor-pointer text-emerald-800 hover:bg-emerald-100/80 dark:text-emerald-200 dark:hover:bg-emerald-900/40"
            aria-label="Dismiss resolution banner"
            onClick={handleDismiss}
          >
            Dismiss
          </Button>
        </div>
      </div>
    </div>
  )
}
