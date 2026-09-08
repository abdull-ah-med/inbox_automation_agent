"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api-client"
import { getMutationErrorMessage } from "@/lib/error-messages"
import type { ActivityEntry } from "@/lib/types"

const shouldShowBanner = (activity: ActivityEntry[] | undefined): ActivityEntry | null => {
  if (!activity?.length) return null
  let latestEscalated: ActivityEntry | null = null
  let latestWrong: ActivityEntry | null = null
  for (const entry of activity) {
    if (entry.event_type === "thread.urgency.recurrence_escalated") {
      if (
        !latestEscalated ||
        new Date(entry.timestamp).getTime() > new Date(latestEscalated.timestamp).getTime()
      ) {
        latestEscalated = entry
      }
    }
    if (entry.event_type === "thread.urgency.recurrence_wrong") {
      if (
        !latestWrong ||
        new Date(entry.timestamp).getTime() > new Date(latestWrong.timestamp).getTime()
      ) {
        latestWrong = entry
      }
    }
  }
  if (!latestEscalated) return null
  if (
    latestWrong &&
    new Date(latestWrong.timestamp).getTime() >= new Date(latestEscalated.timestamp).getTime()
  ) {
    return null
  }
  return latestEscalated
}

type RecurrenceBannerProps = {
  threadId: string
  activity?: ActivityEntry[]
}

export const RecurrenceBanner = (props: RecurrenceBannerProps) => (
  <RecurrenceBannerState key={props.threadId} {...props} />
)

const RecurrenceBannerState = ({ threadId, activity }: RecurrenceBannerProps) => {
  const queryClient = useQueryClient()
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [markedWrong, setMarkedWrong] = useState(false)

  const feedbackMutation = useMutation({
    mutationFn: () => api.threads.urgencyFeedback(threadId, { action: "wrong_escalation" }),
    onSuccess: async () => {
      setError(null)
      setMarkedWrong(true)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
    },
    onError: (err: unknown) => {
      setError(getMutationErrorMessage(err, "Could not update urgency feedback."))
    },
  })

  const escalated = shouldShowBanner(activity)

  if (dismissed) return null
  if (!markedWrong && !escalated) return null

  const handleMarkWrong = () => {
    feedbackMutation.mutate()
  }

  const handleDismiss = () => {
    setDismissed(true)
  }

  if (markedWrong) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="mb-5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
      >
        <p className="font-medium">Automatic urgency bump marked wrong</p>
        <p className="mt-1 text-amber-900/90 dark:text-amber-100/90">
          We reverted this thread and will not auto-bump this alert fingerprint again.
        </p>
        <div className="mt-3">
          <button
            type="button"
            className="cursor-pointer rounded-md px-2.5 py-1.5 text-xs font-medium text-amber-800 underline-offset-2 hover:underline dark:text-amber-200"
            aria-label="Dismiss automatic urgency banner"
            onClick={handleDismiss}
          >
            Dismiss
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      role="status"
      className="mb-5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100"
    >
      <p className="font-medium">Urgency bumped automatically</p>
      <p className="mt-1 text-amber-900/90 dark:text-amber-100/90">
        {escalated?.body ||
          "Similar alerts in 48h raised urgency automatically (same sender and subject)."}
      </p>
      {error ? (
        <p className="mt-2 text-xs text-red-700 dark:text-red-300" role="alert">
          {error}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="cursor-pointer rounded-md bg-amber-800 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-amber-900 disabled:opacity-50"
          aria-label="Mark automatic urgency bump as wrong"
          disabled={feedbackMutation.isPending}
          onClick={handleMarkWrong}
        >
          Mark as wrong
        </button>
        <button
          type="button"
          className="cursor-pointer rounded-md px-2.5 py-1.5 text-xs font-medium text-amber-800 underline-offset-2 hover:underline dark:text-amber-200"
          aria-label="Dismiss automatic urgency banner"
          onClick={handleDismiss}
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
