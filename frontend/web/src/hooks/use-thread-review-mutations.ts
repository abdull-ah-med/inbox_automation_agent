"use client"

import { useMutation, type QueryClient } from "@tanstack/react-query"
import type { Dispatch, SetStateAction } from "react"

import { api } from "@/lib/api-client"
import type { RejectReasonCode } from "@/lib/routing"
import type { DraftView } from "@/lib/types"
import type { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"
import type { ApprovalScope } from "@/components/thread-triage/review-dialogs"

type SiblingsApi = ReturnType<typeof useSiblingsPrompt>

type UseThreadReviewMutationsArgs = {
  threadId: string
  draft: DraftView | null
  queryClient: QueryClient
  approveBody: string
  approvalNote: string
  approvalScope: ApprovalScope | ""
  rejectNote: string
  rejectReason: RejectReasonCode | ""
  setApproveOpen: Dispatch<SetStateAction<boolean>>
  setApprovalNote: Dispatch<SetStateAction<string>>
  setApprovalScope: Dispatch<SetStateAction<ApprovalScope | "">>
  setRejectOpen: Dispatch<SetStateAction<boolean>>
  setRejectNote: Dispatch<SetStateAction<string>>
  setRejectReason: Dispatch<SetStateAction<RejectReasonCode | "">>
  setActionError: Dispatch<SetStateAction<string | null>>
  setResolvePromptOpen: Dispatch<SetStateAction<boolean>>
  resolveActionsTaken: string
  resolveInvolved: string
  setResolveActionsTaken: Dispatch<SetStateAction<string>>
  setResolveInvolved: Dispatch<SetStateAction<string>>
  maybePromptResolve: () => void
  siblings: SiblingsApi
}

export const useThreadReviewMutations = ({
  threadId,
  draft,
  queryClient,
  approveBody,
  approvalNote,
  approvalScope,
  rejectNote,
  rejectReason,
  setApproveOpen,
  setApprovalNote,
  setApprovalScope,
  setRejectOpen,
  setRejectNote,
  setRejectReason,
  setActionError,
  setResolvePromptOpen,
  resolveActionsTaken,
  resolveInvolved,
  setResolveActionsTaken,
  setResolveInvolved,
  maybePromptResolve,
  siblings,
}: UseThreadReviewMutationsArgs) => {
  const draftId = draft?.id

  const invalidateReviewQueues = async () => {
    await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
    await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
    await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
  }

  const approveMutation = useMutation({
    mutationFn: (body?: {
      edited_body?: string
      approval_note?: string
      approval_scope?: ApprovalScope
    }) => {
      if (!draftId) {
        throw new Error("No draft available to approve")
      }
      return api.drafts.approve(draftId, body)
    },
    onSuccess: async () => {
      setApproveOpen(false)
      setApprovalNote("")
      setApprovalScope("")
      setActionError(null)
      await invalidateReviewQueues()
      maybePromptResolve()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const rejectMutation = useMutation({
    mutationFn: async (payload: { feedback_note: string; reason_code: RejectReasonCode }) => {
      if (!draftId) {
        throw new Error("No draft available to reject")
      }
      if (payload.reason_code === "wrong_action") {
        return api.drafts.markWrong(draftId, {
          feedback_note: payload.feedback_note,
          reason_code: payload.reason_code,
        })
      }
      return api.drafts.reject(draftId, payload)
    },
    onSuccess: async (_draft, payload) => {
      setRejectOpen(false)
      setRejectNote("")
      setRejectReason("")
      setActionError(null)
      await invalidateReviewQueues()
      if (payload.reason_code === "wrong_action") {
        await siblings.prompt("no_reply", payload.feedback_note)
      } else {
        maybePromptResolve()
      }
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const resolveMutation = useMutation({
    mutationFn: (body: { actions_taken: string; involved?: string | null }) =>
      api.threads.resolve(threadId, body),
    onSuccess: async () => {
      setResolvePromptOpen(false)
      setResolveActionsTaken("")
      setResolveInvolved("")
      await invalidateReviewQueues()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const handleConfirmApprove = () => {
    if (!draft) return
    const trimmed = approveBody.trim()
    if (!trimmed) return
    const note = approvalNote.trim()
    if (note && !approvalScope) return

    const payload: {
      edited_body?: string
      approval_note?: string
      approval_scope?: ApprovalScope
    } = {}
    if (trimmed !== draft.body.trim()) {
      payload.edited_body = trimmed
    }
    if (note && approvalScope) {
      payload.approval_note = note
      payload.approval_scope = approvalScope
    }
    if (Object.keys(payload).length === 0) {
      approveMutation.mutate(undefined)
      return
    }
    approveMutation.mutate(payload)
  }

  const handleReject = () => {
    if (!rejectNote.trim() || !rejectReason) return
    rejectMutation.mutate({
      feedback_note: rejectNote.trim(),
      reason_code: rejectReason,
    })
  }

  const handleConfirmResolve = () => {
    const actionsTaken = resolveActionsTaken.trim()
    if (!actionsTaken) return
    const involved = resolveInvolved.trim()
    resolveMutation.mutate({
      actions_taken: actionsTaken,
      involved: involved || null,
    })
  }

  return {
    approveMutation,
    rejectMutation,
    resolveMutation,
    handleConfirmApprove,
    handleReject,
    handleConfirmResolve,
  }
}
