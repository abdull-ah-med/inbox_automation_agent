"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import {
  isFeedbackDone,
  sidebarDraftDisplay,
  sidebarOptionalDefaults,
} from "@/components/thread-triage/sidebar-helpers"
import { ThreadTriageSidebarView } from "@/components/thread-triage/sidebar-view"
import type { ApprovalScope } from "@/components/thread-triage/review-dialogs"
import { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"
import { useThreadDraftActions } from "@/hooks/use-thread-draft-actions"
import { useThreadReviewMutations } from "@/hooks/use-thread-review-mutations"
import { api } from "@/lib/api-client"
import type { RejectReasonCode } from "@/lib/routing"
import type {
  ActivityEntry,
  AuditEntry,
  ClassificationView,
  DraftView,
  MessageDetail,
  ReplyAddresseeView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

type ThreadTriageSidebarProps = {
  threadId: string
  thread: ThreadSummary
  classification: ClassificationView | null
  draft: DraftView | null
  triage: TriageFlags | null
  auditLog: AuditEntry[]
  activity?: ActivityEntry[]
  messages?: MessageDetail[]
  replyAddressee?: ReplyAddresseeView | null
  draftRegenInProgress?: boolean
  draftRegenError?: string | null
}

export const ThreadTriageSidebar = (props: ThreadTriageSidebarProps) => (
  <ThreadTriageSidebarState key={props.threadId} {...props} />
)

const ThreadTriageSidebarState = (props: ThreadTriageSidebarProps) => {
  const { threadId, thread, classification, draft, triage, auditLog } = props
  const { activity, messages, replyAddressee, draftRegenInProgress, draftRegenError } =
    sidebarOptionalDefaults(props)
  const { teachingNote, urgency, urgencyReason, badge, suggestedActions } = sidebarDraftDisplay(
    thread,
    draft,
    classification,
  )
  const queryClient = useQueryClient()
  const [approveOpen, setApproveOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [resolvePromptOpen, setResolvePromptOpen] = useState(false)
  const [resolveActionsTaken, setResolveActionsTaken] = useState("")
  const [resolveInvolved, setResolveInvolved] = useState("")
  const [approveBody, setApproveBody] = useState("")
  const [approvalNote, setApprovalNote] = useState("")
  const [approvalScope, setApprovalScope] = useState<ApprovalScope | "">("")
  const [rejectNote, setRejectNote] = useState("")
  const [rejectReason, setRejectReason] = useState<RejectReasonCode | "">("")
  const [processNote, setProcessNote] = useState("")
  const [rewriteOpen, setRewriteOpen] = useState(false)
  const [rewriteInstruction, setRewriteInstruction] = useState("")
  const [actionError, setActionError] = useState<string | null>(null)
  const siblings = useSiblingsPrompt(threadId)
  const presentation = thread.presentation
  const contextQuery = useQuery({
    queryKey: ["thread", threadId, "context"],
    queryFn: () => api.threads.getContext(threadId),
    retry: false,
  })
  const threadContextEnabled = contextQuery.isSuccess
  const showLegacyInsights = contextQuery.isFetched && !threadContextEnabled

  const openResolvePrompt = () => {
    setResolveActionsTaken("")
    setResolveInvolved("")
    setResolvePromptOpen(true)
  }

  const maybePromptResolve = () => {
    if (presentation?.is_finished) return
    if (!presentation?.suggest_resolve_default) return
    openResolvePrompt()
  }

  const handleMarkResolved = () => {
    openResolvePrompt()
  }

  const {
    approveMutation,
    rejectMutation,
    resolveMutation,
    handleConfirmApprove,
    handleReject,
    handleConfirmResolve,
  } = useThreadReviewMutations({
    threadId,
    draft,
    queryClient,
    approveBody,
    approvalNote,
    approvalScope,
    rejectNote,
    rejectReason,
    processNote,
    setApproveOpen,
    setApprovalNote,
    setApprovalScope,
    setRejectOpen,
    setRejectNote,
    setRejectReason,
    setProcessNote,
    setRewriteOpen,
    setRewriteInstruction,
    setActionError,
    setResolvePromptOpen,
    resolveActionsTaken,
    resolveInvolved,
    setResolveActionsTaken,
    setResolveInvolved,
    maybePromptResolve,
    siblings,
  })

  const {
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
  } = useThreadDraftActions({
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
  })

  return (
    <ThreadTriageSidebarView
      threadId={threadId}
      thread={thread}
      draft={draft}
      triage={triage}
      auditLog={auditLog}
      activity={activity}
      messages={messages}
      teachingNote={teachingNote}
      urgency={urgency}
      urgencyReason={urgencyReason}
      badge={badge}
      feedbackDone={isFeedbackDone(draft)}
      busy={
        approveMutation.isPending ||
        rejectMutation.isPending ||
        generateDraftMutation.isPending ||
        regenPending ||
        resolveMutation.isPending
      }
      generatePending={generateDraftMutation.isPending || regenPending}
      suggestedActions={suggestedActions}
      draftId={draft?.id}
      replyAddressee={replyAddressee}
      actionError={actionError ?? draftRegenError}
      approveOpen={approveOpen}
      rejectOpen={rejectOpen}
      resolvePromptOpen={resolvePromptOpen}
      rewriteOpen={rewriteOpen}
      rewritePending={regenPending}
      resolveActionsTaken={resolveActionsTaken}
      resolveInvolved={resolveInvolved}
      approveBody={approveBody}
      approvalNote={approvalNote}
      approvalScope={approvalScope}
      rejectNote={rejectNote}
      rejectReason={rejectReason}
      processNote={processNote}
      approvePending={approveMutation.isPending}
      rejectPending={rejectMutation.isPending}
      resolvePending={resolveMutation.isPending}
      siblings={siblings}
      onApproveOpenChange={setApproveOpen}
      onRejectOpenChange={setRejectOpen}
      onResolvePromptOpenChange={setResolvePromptOpen}
      onRewriteOpenChange={setRewriteOpen}
      onResolveActionsTakenChange={setResolveActionsTaken}
      onResolveInvolvedChange={setResolveInvolved}
      onMarkResolved={handleMarkResolved}
      onApproveBodyChange={setApproveBody}
      onApprovalNoteChange={setApprovalNote}
      onApprovalScopeChange={setApprovalScope}
      onRejectNoteChange={setRejectNote}
      onRejectReasonChange={setRejectReason}
      onProcessNoteChange={setProcessNote}
      onOpenApprove={handleOpenApprove}
      onOpenReject={handleOpenReject}
      onApproveKeyDown={handleApproveKeyDown}
      onRejectKeyDown={handleRejectKeyDown}
      onConfirmApprove={handleConfirmApprove}
      onConfirmReject={handleReject}
      onConfirmResolve={handleConfirmResolve}
      onConfirmRewrite={handleConfirmRewrite}
      onSkipRewrite={handleSkipRewrite}
      onGenerateDraft={handleGenerateDraft}
      onGenerateDraftKeyDown={handleGenerateDraftKeyDown}
      onRewriteAgain={handleRewriteAgain}
      onRewriteAgainKeyDown={handleRewriteAgainKeyDown}
      threadContextEnabled={threadContextEnabled}
      showLegacyInsights={showLegacyInsights}
    />
  )
}
