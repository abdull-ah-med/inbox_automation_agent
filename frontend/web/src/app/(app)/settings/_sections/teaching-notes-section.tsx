"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ErrorPage } from "@/components/error-page"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import type { TeachingNote } from "@/lib/types"

export const TeachingNotesSection = ({
  mailbox,
  mailboxesLoading,
}: {
  mailbox: string
  mailboxesLoading: boolean
}) => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()

  const {
    data: notes,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["teaching-notes", mailbox],
    queryFn: () => api.teachingNotes.list({ mailbox }),
    enabled: Boolean(mailbox),
  })

  const archiveMutation = useMutation({
    mutationFn: (id: string) => api.teachingNotes.remove(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["teaching-notes", mailbox] })
    },
  })

  const handleArchive = (note: TeachingNote) => {
    if (!isAdmin) return
    archiveMutation.mutate(note.id)
  }

  const items = notes ?? []

  return (
    <Card aria-labelledby="teaching-notes-heading">
      <CardHeader>
        <CardTitle id="teaching-notes-heading" className="text-lg">
          Teaching notes
        </CardTitle>
        <CardDescription>
          Standing guidance retrieved into drafts. Wider scope changes go through a promotion
          proposal instead of applying immediately.
        </CardDescription>
        {!isAdmin ? (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            View only — an admin account is required to archive teaching notes.
          </p>
        ) : null}
      </CardHeader>
      <CardContent>
        <div className="ring-foreground/10 overflow-hidden rounded-lg ring-1">
          {mailboxesLoading || isLoading ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : isError ? (
            <div className="p-4">
              <ErrorPage
                error={error}
                onRetry={() => {
                  void refetch()
                }}
              />
            </div>
          ) : items.length === 0 ? (
            <p className="p-6 text-sm text-gray-500">No teaching notes yet for this mailbox.</p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {items.map((note) => (
                <li key={note.id} className="flex items-start justify-between gap-3 p-4">
                  <div>
                    <p className="font-medium text-gray-900 dark:text-gray-100">{note.title}</p>
                    <p className="text-muted-foreground mt-1 text-sm">{note.body}</p>
                    <p className="text-muted-foreground mt-1 text-xs">
                      {note.scope} · {note.status}
                    </p>
                  </div>
                  {isAdmin && note.status !== "archived" ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      tabIndex={0}
                      aria-label={`Archive teaching note ${note.title}`}
                      disabled={archiveMutation.isPending}
                      onClick={() => handleArchive(note)}
                    >
                      Archive
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
