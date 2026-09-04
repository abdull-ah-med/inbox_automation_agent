"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { MailboxFilter, useMailboxFilter } from "@/app/(app)/settings/_sections/mailbox-filter"
import { ErrorPage } from "@/components/error-page"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import type { PromotionProposal } from "@/lib/types"

export const PromotionProposalsSection = () => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()
  const { mailbox, addresses, setMailbox, isLoading: mailboxesLoading } = useMailboxFilter()

  const {
    data: proposals,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["promotion-proposals", mailbox],
    queryFn: () => api.promotionProposals.list({ mailbox, status: "pending" }),
    enabled: Boolean(mailbox),
  })

  const acceptMutation = useMutation({
    mutationFn: (id: string) => api.promotionProposals.accept(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["promotion-proposals", mailbox] })
    },
  })
  const dismissMutation = useMutation({
    mutationFn: (id: string) => api.promotionProposals.dismiss(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["promotion-proposals", mailbox] })
    },
  })

  const handleAccept = (proposal: PromotionProposal) => {
    if (!isAdmin) return
    acceptMutation.mutate(proposal.id)
  }
  const handleDismiss = (proposal: PromotionProposal) => {
    if (!isAdmin) return
    dismissMutation.mutate(proposal.id)
  }

  const items = proposals ?? []
  const busy = acceptMutation.isPending || dismissMutation.isPending

  return (
    <Card aria-labelledby="promotion-proposals-heading">
      <CardHeader>
        <CardTitle id="promotion-proposals-heading" className="text-lg">
          Promotion proposals
        </CardTitle>
        <CardDescription>
          Suggested widenings and urgency rules. Accept runs the golden-set gate before anything
          goes live.
        </CardDescription>
        {!isAdmin ? (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            View only — an admin account is required to accept or dismiss proposals.
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
            <p className="p-6 text-sm text-gray-500">No pending promotion proposals.</p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {items.map((proposal) => {
                const description =
                  typeof proposal.payload.description === "string"
                    ? proposal.payload.description
                    : `${proposal.kind} · ${proposal.mailbox}`
                return (
                  <li key={proposal.id} className="flex items-start justify-between gap-3 p-4">
                    <div>
                      <p className="font-medium text-gray-900 dark:text-gray-100">
                        {proposal.kind}
                      </p>
                      <p className="text-muted-foreground mt-1 text-sm">{description}</p>
                      <p className="text-muted-foreground mt-1 text-xs">
                        Impact {proposal.impact_num}/{proposal.impact_den}
                      </p>
                    </div>
                    {isAdmin ? (
                      <div className="flex gap-2">
                        <Button
                          type="button"
                          size="sm"
                          tabIndex={0}
                          aria-label={`Accept proposal ${proposal.kind}`}
                          disabled={busy}
                          onClick={() => handleAccept(proposal)}
                        >
                          Accept
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          tabIndex={0}
                          aria-label={`Dismiss proposal ${proposal.kind}`}
                          disabled={busy}
                          onClick={() => handleDismiss(proposal)}
                        >
                          Dismiss
                        </Button>
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
