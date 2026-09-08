"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ErrorPage } from "@/components/error-page"
import { MemoryPreviewRow } from "@/components/memory-preview-row"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import type { RejectionMemoryResponse } from "@/lib/types"
import { cn } from "@/lib/utils"

export const RejectionMemorySection = () => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()

  const {
    data: rejectionMemory,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["rejection-memory"],
    queryFn: () => api.rejectionMemory.list(),
  })

  const excludeMutation = useMutation({
    mutationFn: ({ id, is_excluded }: { id: string; is_excluded: boolean }) =>
      api.rejectionMemory.setExcluded(id, is_excluded),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["rejection-memory"] })
    },
  })

  const handleToggleExclude = (item: RejectionMemoryResponse) => {
    if (!isAdmin) return
    excludeMutation.mutate({
      id: item.id,
      is_excluded: !item.is_excluded,
    })
  }

  const items = rejectionMemory ?? []

  return (
    <Card aria-labelledby="rejection-memory-heading">
      <CardHeader>
        <CardTitle id="rejection-memory-heading" className="text-lg">
          Rejected drafts
        </CardTitle>
        <CardDescription>
          Rejection notes used as “do not do this” constraints when drafting. Exclude any that
          should not influence future drafts. Click a preview to read the full draft.
        </CardDescription>
        {!isAdmin ? (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            View only — an admin account is required to exclude rejection constraints.
          </p>
        ) : null}
      </CardHeader>
      <CardContent>
        <div className={cn("overflow-hidden rounded-lg", !isError && "ring-foreground/10 ring-1")}>
          {isLoading ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : isError ? (
            <ErrorPage
              embedded
              error={error}
              onRetry={() => {
                void refetch()
              }}
            />
          ) : items.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">
              No rejected drafts yet. Reject a draft with a note to start building constraints.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {items.map((item) => (
                <MemoryPreviewRow
                  key={item.id}
                  isExcluded={item.is_excluded}
                  canExclude={isAdmin}
                  excludePending={excludeMutation.isPending}
                  excludeLabelInclude="Include this rejection in draft constraints"
                  excludeLabelExclude="Exclude this rejection from draft constraints"
                  onToggleExclude={() => handleToggleExclude(item)}
                  item={{
                    id: item.id,
                    title: item.draft_subject,
                    senderEmail: item.sender_email ?? null,
                    receiverEmail: item.receiver_email ?? item.mailbox,
                    reasonCode: item.reason_code,
                    reasonText: item.reason_text ?? item.note,
                    dateIso: item.created_at,
                    previewLine: item.preview_line ?? item.draft_body_preview,
                    fullBody: item.draft_body ?? item.draft_body_preview ?? item.note,
                    threadId: item.thread_id ?? null,
                    datePrefix: "Rejected",
                    badges: [
                      {
                        label: item.is_excluded ? "Excluded" : "Included",
                        tone: item.is_excluded ? "neutral" : "red",
                      },
                      { label: item.mailbox, tone: "neutral" },
                      { label: item.routing_category, tone: "blue" },
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
