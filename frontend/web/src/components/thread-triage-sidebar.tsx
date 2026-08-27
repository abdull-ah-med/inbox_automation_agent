"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import {
  firstUrgency,
  feedbackBadge,
  isActivationKey,
  isFeedbackDone,
  teachingNoteFor,
} from "@/components/thread-triage/sidebar-helpers"
import { ThreadTriageSidebarView } from "@/components/thread-triage/sidebar-view"
import { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"
import { useThreadReviewMutations } from "@/hooks/use-thread-review-mutations"
import type { RejectReasonCode } from "@/lib/routing"
import type {
  ActivityEntry,
  AuditEntry,
  ClassificationView,
  DraftView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

export const ThreadTriageSidebar = ({
  threadId,
  thread,
  classification,
  draft,
  triage,
  auditLog,
  activity = [],
}: {
  threadId: string
  thread: ThreadSummary
  classification: ClassificationView | null
  draft: DraftView | null
  triage: TriageFlags | null
  auditLog: AuditEntry[]
  activity?: ActivityEntry[]
}) => {
  const queryClient = useQueryClient()
  const [approveOpen, setApproveOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [resolvePromptOpen, setResolvePromptOpen] = useState(false)
  const [approveBody, setApproveBody] = useState("")
  const [approvalNote, setApprovalNote] = useState("")
  const [approvalScope, setApprovalScope] = useState<"once" | "similar" | "">("")
  const [rejectNote, setRejectNote] = useState("")
  const [rejectReason, setRejectReason] = useState<RejectReasonCode | "">("")
  const [actionError, setActionError] = useState<string | null>(null)
  const siblings = useSiblingsPrompt(threadId)
  const presentation = thread.presentation

  const maybePromptResolve = () => {
    if (presentation?.is_finished) return
    if (!presentation?.suggest_resolve_default) return
    setResolvePromptOpen(true)
  }

  const { approveMutation, rejectMutation, resolveMutation, handleConfirmApprove, handleReject } =
    useThreadReviewMutations({
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
      maybePromptResolve,
      siblings,
    })

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

  const handleRejectKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isActivationKey(event.key)) return
    event.preventDefault()
    setRejectOpen(true)
  }

  return (
    <ThreadTriageSidebarView
      threadId={threadId}
      thread={thread}
      draft={draft}
      triage={triage}
      auditLog={auditLog}
      activity={activity}
      teachingNote={teachingNoteFor(thread.teaching_note, draft?.teaching_note)}
      urgency={firstUrgency(
        presentation?.urgency_assessed,
        thread.urgency,
        draft?.urgency,
        classification?.urgency,
      )}
      urgencyReason={draft?.urgency_reason ?? null}
      badge={draft ? feedbackBadge(draft) : null}
      feedbackDone={isFeedbackDone(draft)}
      busy={approveMutation.isPending || rejectMutation.isPending}
      suggestedActions={draft?.suggested_actions ?? []}
      draftId={draft?.id}
      actionError={actionError}
      approveOpen={approveOpen}
      rejectOpen={rejectOpen}
      resolvePromptOpen={resolvePromptOpen}
      approveBody={approveBody}
      approvalNote={approvalNote}
      approvalScope={approvalScope}
      rejectNote={rejectNote}
      rejectReason={rejectReason}
      approvePending={approveMutation.isPending}
      rejectPending={rejectMutation.isPending}
      resolvePending={resolveMutation.isPending}
      siblings={siblings}
      onApproveOpenChange={setApproveOpen}
      onRejectOpenChange={setRejectOpen}
      onResolvePromptOpenChange={setResolvePromptOpen}
      onApproveBodyChange={setApproveBody}
      onApprovalNoteChange={setApprovalNote}
      onApprovalScopeChange={setApprovalScope}
      onRejectNoteChange={setRejectNote}
      onRejectReasonChange={setRejectReason}
      onOpenApprove={handleOpenApprove}
      onApproveKeyDown={handleApproveKeyDown}
      onRejectKeyDown={handleRejectKeyDown}
      onConfirmApprove={handleConfirmApprove}
      onConfirmReject={handleReject}
      onConfirmResolve={() => resolveMutation.mutate()}
    />
  )
}
