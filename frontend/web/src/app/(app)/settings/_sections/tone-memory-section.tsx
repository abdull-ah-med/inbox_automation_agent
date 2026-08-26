"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ErrorPage } from "@/components/error-page"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import { formatReviewerDateTime } from "@/lib/dates"
import type { ReplyMemoryResponse } from "@/lib/types"

export const ToneMemorySection = () => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()

  const {
    data: replyMemory,
    isLoading: replyMemoryLoading,
    isError: replyMemoryError,
    error: replyMemoryErr,
    refetch: refetchReplyMemory,
  } = useQuery({
    queryKey: ["reply-memory"],
    queryFn: () => api.replyMemory.list(),
  })

  const excludeMutation = useMutation({
    mutationFn: ({
      id,
      is_excluded,
    }: {
      id: string
      is_excluded: boolean
    }) => api.replyMemory.setExcluded(id, is_excluded),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reply-memory"] })
    },
  })

  const handleToggleExclude = (item: ReplyMemoryResponse) => {
    if (!isAdmin) return
    excludeMutation.mutate({
      id: item.id,
      is_excluded: !item.is_excluded,
    })
  }

  const replies = replyMemory ?? []

  return (
    <section className="mt-10" aria-labelledby="tone-memory-heading">
      <div className="mb-5">
        <h2
          id="tone-memory-heading"
          className="text-xl font-semibold text-gray-900 dark:text-gray-100"
        >
          Tone memory
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Approved replies used as tone references when drafting. Exclude any
          that should not influence future drafts.
        </p>
        {!isAdmin ? (
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">
            View only — an admin account is required to exclude tone references.
          </p>
        ) : null}
      </div>

      <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        {replyMemoryLoading ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : replyMemoryError ? (
          <div className="p-4">
            <ErrorPage
              error={replyMemoryErr}
              onRetry={() => {
                void refetchReplyMemory()
              }}
            />
          </div>
        ) : replies.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">
            No approved replies yet. Approve a draft to start building tone
            memory.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {replies.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-start justify-between gap-3 p-4"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge
                      label={item.is_excluded ? "Excluded" : "Included"}
                      tone={item.is_excluded ? "neutral" : "green"}
                    />
                    <StatusBadge label={item.mailbox} tone="neutral" />
                    <span className="text-xs text-gray-500">
                      Approved {formatReviewerDateTime(item.created_at)}
                    </span>
                  </div>
                  {item.original_email_preview ? (
                    <p className="text-sm text-gray-500">
                      Re: {item.original_email_preview}
                    </p>
                  ) : null}
                  <p className="line-clamp-3 whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300">
                    {item.reply_text}
                  </p>
                </div>
                {isAdmin ? (
                  <div className="flex shrink-0">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      tabIndex={0}
                      aria-label={
                        item.is_excluded
                          ? "Include this reply in tone memory"
                          : "Exclude this reply from tone memory"
                      }
                      disabled={excludeMutation.isPending}
                      onClick={() => handleToggleExclude(item)}
                    >
                      {item.is_excluded ? "Include" : "Exclude"}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
