"use client"

import { useMutation, type QueryClient } from "@tanstack/react-query"
import type { Dispatch, SetStateAction } from "react"

import { isActivationKey } from "@/components/thread-triage/sidebar-helpers"
import type { ApprovalScope } from "@/components/thread-triage/review-dialogs"
import { api } from "@/lib/api-client"
import type { DraftView, ThreadDetail } from "@/lib/types"

const markThreadRegenRunning = (prev: ThreadDetail | undefined) =>
  prev ? { ...prev, draft_regen_in_progress: true, draft_regen_error: null } : prev

type UseThreadDraftActionsArgs = {
  threadId: string
  draft: DraftView | null
  queryClient: QueryClient
  draftRegenInProgress: boolean
  setApproveBody: Dispatch<SetStateAction<string>>
  setApprovalNote: Dispatch<SetStateAction<string>>
  setApprovalScope: Dispatch<SetStateAction<ApprovalScope | "">>
  setApproveOpen: Dispatch<SetStateAction<boolean>>
  setProcessNote: Dispatch<SetStateAction<string>>
  setRejectOpen: Dispatch<SetStateAction<boolean>>
  setRewriteOpen: Dispatch<SetStateAction<boolean>>
  setRewriteInstruction: Dispatch<SetStateAction<string>>
  setActionError: Dispatch<SetStateAction<string | null>>
  rewriteInstruction: string
  maybePromptResolve: () => void
}

export const useThreadDraftActions = ({
  threadId,
  draft,
  queryClient,
  draftRegenInProgress,
  setApproveBody,
  setApprovalNote,
  setApprovalScope,
  setApproveOpen,
  setProcessNote,
  setRejectOpen,
  setRewriteOpen,
  setRewriteInstruction,
  setActionError,
  rewriteInstruction,
  maybePromptResolve,
}: UseThreadDraftActionsArgs) => {
  const generateDraftMutation = useMutation({
    mutationFn: () => api.threads.generateDraft(threadId),
    onSuccess: async () => {
      setActionError(null)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
      await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const regenerateDraftMutation = useMutation({
    mutationFn: (instruction: string) => api.threads.regenerateDraft(threadId, { instruction }),
    onSuccess: async () => {
      setRewriteOpen(false)
      setRewriteInstruction("")
      setActionError(null)
      queryClient.setQueryData(["thread", threadId], markThreadRegenRunning)
      await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
      await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
      await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const handleGenerateDraft = () => {
    generateDraftMutation.mutate()
  }

  const handleGenerateDraftKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isActivationKey(event.key)) return
    event.preventDefault()
    handleGenerateDraft()
  }

  const handleOpenApprove = () => {
    setApproveBody(draft?.body ?? "")
    setApprovalNote("")
    setApprovalScope("")
    setApproveOpen(true)
  }

  const handleApproveKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isActivationKey(event.key)) return
    event.preventDefault()
    handleOpenApprove()
  }

  const handleOpenReject = () => {
    setProcessNote("")
    setRejectOpen(true)
  }

  const handleRejectKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isActivationKey(event.key)) return
    event.preventDefault()
    handleOpenReject()
  }

  const handleConfirmRewrite = () => {
    const instruction = rewriteInstruction.trim()
    if (!instruction) return
    setRewriteOpen(false)
    setRewriteInstruction("")
    regenerateDraftMutation.mutate(instruction)
  }

  const handleSkipRewrite = () => {
    setRewriteOpen(false)
    setRewriteInstruction("")
    maybePromptResolve()
  }

  const handleRewriteAgain = () => {
    const note = (draft?.feedback_note || "").trim()
    if (!note) return
    setRewriteInstruction(note)
    setRewriteOpen(true)
  }

  const handleRewriteAgainKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isActivationKey(event.key)) return
    event.preventDefault()
    handleRewriteAgain()
  }

  const regenPending = draftRegenInProgress || regenerateDraftMutation.isPending

  return {
    generateDraftMutation,
    regenPending,
    handleGenerateDraft,
    handleGenerateDraftKeyDown,
    handleOpenApprove,
    handleApproveKeyDown,
    handleOpenReject,
    handleRejectKeyDown,
    handleConfirmRewrite,
    handleSkipRewrite,
    handleRewriteAgain,
    handleRewriteAgainKeyDown,
  }
}
