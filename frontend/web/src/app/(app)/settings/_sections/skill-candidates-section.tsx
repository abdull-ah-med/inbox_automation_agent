"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import { useAuthState } from "@/features/auth/use-auth"
import { api } from "@/lib/api-client"
import type { SkillCandidateResponse } from "@/lib/types"

export const SkillCandidatesSection = () => {
  const auth = useAuthState()
  const isAdmin = auth.user?.role === "admin"
  const queryClient = useQueryClient()

  const { data: skillCandidates, refetch: refetchCandidates } = useQuery({
    queryKey: ["skill-candidates"],
    queryFn: () => api.skillCandidates.list(),
  })

  const acceptCandidateMutation = useMutation({
    mutationFn: (id: string) => api.skillCandidates.accept(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skills"] })
      await refetchCandidates()
    },
  })

  const dismissCandidateMutation = useMutation({
    mutationFn: (id: string) => api.skillCandidates.dismiss(id),
    onSuccess: async () => {
      await refetchCandidates()
    },
  })

  const handleAcceptCandidate = (candidate: SkillCandidateResponse) => {
    if (!isAdmin) return
    acceptCandidateMutation.mutate(candidate.id)
  }

  const handleDismissCandidate = (candidate: SkillCandidateResponse) => {
    if (!isAdmin) return
    dismissCandidateMutation.mutate(candidate.id)
  }

  const candidates = skillCandidates ?? []

  return (
    <section className="mt-10" aria-labelledby="skill-candidates-heading">
      <div className="mb-5">
        <h2
          id="skill-candidates-heading"
          className="text-xl font-semibold text-gray-900 dark:text-gray-100"
        >
          Proposed skills
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Recurring rejection themes promoted into standing skill drafts for
          review. Accept creates an active skill; dismiss archives the proposal.
        </p>
      </div>
      <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        {candidates.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">
            No pending proposals. Reject drafts with the same reason a few times
            to surface candidates.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {candidates.map((candidate) => (
              <li
                key={candidate.id}
                className="flex flex-wrap items-start justify-between gap-3 p-4"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium text-gray-900 dark:text-gray-100">
                      {candidate.proposed_name}
                    </p>
                    <StatusBadge label={candidate.routing_category} tone="neutral" />
                    <StatusBadge label={candidate.reason_code} tone="amber" />
                  </div>
                  <p className="text-sm text-gray-700 dark:text-gray-300">
                    {candidate.proposed_content}
                  </p>
                  <p className="text-xs text-gray-500">{candidate.mailbox}</p>
                </div>
                {isAdmin ? (
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <Button
                      type="button"
                      size="sm"
                      tabIndex={0}
                      aria-label={`Accept proposed skill ${candidate.proposed_name}`}
                      disabled={
                        acceptCandidateMutation.isPending ||
                        dismissCandidateMutation.isPending
                      }
                      onClick={() => handleAcceptCandidate(candidate)}
                    >
                      Accept
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      tabIndex={0}
                      aria-label={`Dismiss proposed skill ${candidate.proposed_name}`}
                      disabled={
                        acceptCandidateMutation.isPending ||
                        dismissCandidateMutation.isPending
                      }
                      onClick={() => handleDismissCandidate(candidate)}
                    >
                      Dismiss
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
