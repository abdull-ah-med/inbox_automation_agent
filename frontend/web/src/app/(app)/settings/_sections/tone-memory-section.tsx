"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ErrorPage } from "@/components/error-page"
import { MemoryPreviewRow } from "@/components/memory-preview-row"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import type { ReplyMemoryResponse } from "@/lib/types"
import { cn } from "@/lib/utils"

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
    mutationFn: ({ id, is_excluded }: { id: string; is_excluded: boolean }) =>
      api.replyMemory.setExcluded(id, is_excluded),
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
    <Card aria-labelledby="tone-memory-heading">
      <CardHeader>
        <CardTitle id="tone-memory-heading" className="text-lg">
          Tone memory
        </CardTitle>
        <CardDescription>
          Approved replies used as tone references when drafting. Exclude any that should not
          influence future drafts. Click a preview to read the full draft.
        </CardDescription>
        {!isAdmin ? (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            View only — an admin account is required to exclude tone references.
          </p>
        ) : null}
      </CardHeader>
      <CardContent>
        <div
          className={cn(
            "overflow-hidden rounded-lg",
            !replyMemoryError && "ring-foreground/10 ring-1",
          )}
        >
          {replyMemoryLoading ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : replyMemoryError ? (
            <ErrorPage
              embedded
              error={replyMemoryErr}
              onRetry={() => {
                void refetchReplyMemory()
              }}
            />
          ) : replies.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">
              No approved replies yet. Approve a draft to start building tone memory.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {replies.map((item) => (
                <MemoryPreviewRow
                  key={item.id}
                  isExcluded={item.is_excluded}
                  canExclude={isAdmin}
                  excludePending={excludeMutation.isPending}
                  excludeLabelInclude="Include this reply in tone memory"
                  excludeLabelExclude="Exclude this reply from tone memory"
                  onToggleExclude={() => handleToggleExclude(item)}
                  item={{
                    id: item.id,
                    title: item.draft_subject ?? item.original_email_preview,
                    senderEmail: item.sender_email ?? null,
                    receiverEmail: item.receiver_email ?? item.mailbox,
                    reasonCode: item.reason_code ?? null,
                    reasonText: item.reason_text ?? item.learning_note ?? null,
                    dateIso: item.created_at,
                    previewLine: item.preview_line ?? null,
                    fullBody: item.reply_text,
                    threadId: item.thread_id ?? null,
                    datePrefix: "Approved",
                    badges: [
                      {
                        label: item.is_excluded ? "Excluded" : "Included",
                        tone: item.is_excluded ? "neutral" : "green",
                      },
                      { label: item.mailbox, tone: "neutral" },
                    ],
                  }}
                />
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
