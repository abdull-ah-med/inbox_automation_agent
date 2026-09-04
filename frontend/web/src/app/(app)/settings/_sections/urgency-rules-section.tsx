"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { MailboxFilter, useMailboxFilter } from "@/app/(app)/settings/_sections/mailbox-filter"
import { ErrorPage } from "@/components/error-page"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import type { UrgencyRule } from "@/lib/types"

export const UrgencyRulesSection = () => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()
  const { mailbox, addresses, setMailbox, isLoading: mailboxesLoading } = useMailboxFilter()

  const {
    data: rules,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["urgency-rules", mailbox],
    queryFn: () => api.urgencyRules.list({ mailbox }),
    enabled: Boolean(mailbox),
  })

  const pauseMutation = useMutation({
    mutationFn: (id: string) => api.urgencyRules.pause(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["urgency-rules", mailbox] })
    },
  })
  const resumeMutation = useMutation({
    mutationFn: (id: string) => api.urgencyRules.resume(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["urgency-rules", mailbox] })
    },
  })
  const archiveMutation = useMutation({
    mutationFn: (id: string) => api.urgencyRules.archive(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["urgency-rules", mailbox] })
    },
  })

  const handlePause = (rule: UrgencyRule) => {
    if (!isAdmin) return
    pauseMutation.mutate(rule.id)
  }
  const handleResume = (rule: UrgencyRule) => {
    if (!isAdmin) return
    resumeMutation.mutate(rule.id)
  }
  const handleArchive = (rule: UrgencyRule) => {
    if (!isAdmin) return
    archiveMutation.mutate(rule.id)
  }

  const items = rules ?? []
  const busy = pauseMutation.isPending || resumeMutation.isPending || archiveMutation.isPending

  return (
    <Card aria-labelledby="urgency-rules-heading">
      <CardHeader>
        <CardTitle id="urgency-rules-heading" className="text-lg">
          Urgency rules
        </CardTitle>
        <CardDescription>
          Labelling functions applied after the model. Pause a canary that is overriding too often.
        </CardDescription>
        {!isAdmin ? (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            View only — an admin account is required to pause or archive rules.
          </p>
        ) : null}
        <MailboxFilter mailbox={mailbox} addresses={addresses} onMailboxChange={setMailbox} />
      </CardHeader>
      <CardContent>
        <div className="ring-foreground/10 overflow-hidden rounded-lg ring-1">
          {mailboxesLoading || isLoading ? (
            <div className="space-y-3 p-4">
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
            <p className="p-6 text-sm text-gray-500">No urgency rules for this mailbox.</p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {items.map((rule) => (
                <li key={rule.id} className="flex items-start justify-between gap-3 p-4">
                  <div>
                    <p className="font-medium text-gray-900 dark:text-gray-100">{rule.scope_key}</p>
                    <p className="text-muted-foreground mt-1 text-sm">
                      {rule.status} · hits {rule.hit_count} · overrides {rule.override_count}
                    </p>
                  </div>
                  {isAdmin ? (
                    <div className="flex gap-2">
                      {rule.status === "paused" ? (
                        <Button
                          type="button"
                          size="sm"
                          tabIndex={0}
                          aria-label={`Resume urgency rule ${rule.scope_key}`}
                          disabled={busy}
                          onClick={() => handleResume(rule)}
                        >
                          Resume
                        </Button>
                      ) : rule.status !== "archived" ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          tabIndex={0}
                          aria-label={`Pause urgency rule ${rule.scope_key}`}
                          disabled={busy}
                          onClick={() => handlePause(rule)}
                        >
                          Pause
                        </Button>
                      ) : null}
                      {rule.status !== "archived" ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          tabIndex={0}
                          aria-label={`Archive urgency rule ${rule.scope_key}`}
                          disabled={busy}
                          onClick={() => handleArchive(rule)}
                        >
                          Archive
                        </Button>
                      ) : null}
                    </div>
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
