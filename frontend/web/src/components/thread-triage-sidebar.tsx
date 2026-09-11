"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import {
  firstUrgency,
  feedbackBadge,
  isActivationKey,
  isFeedbackDone,
  teachingNoteFor,
} from "@/components/thread-triage/sidebar-helpers"
import { ThreadTriageSidebarView } from "@/components/thread-triage/sidebar-view"
import type { ApprovalScope } from "@/components/thread-triage/review-dialogs"
import { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"
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
}

export const ThreadTriageSidebar = (props: ThreadTriageSidebarProps) => (
  <ThreadTriageSidebarState key={props.threadId} {...props} />
)

const ThreadTriageSidebarState = ({
  threadId,
  thread,
  classification,
  draft,
  triage,
  auditLog,
  activity = [],
  messages = [],
  replyAddressee = null,
}: ThreadTriageSidebarProps) => {
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
    regenerateDraftMutation.mutate(instruction)
  }

  const handleSkipRewrite = () => {
    setRewriteOpen(false)
    setRewriteInstruction("")
    maybePromptResolve()
  }

  return (
    <ThreadTriageSidebarView
      threadId={threadId}
      thread={thread}
      draft={draft}
      triage={triage}
      auditLog={auditLog}
      activity={activity}
      messages={messages}
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
      busy={
        approveMutation.isPending ||
        rejectMutation.isPending ||
        generateDraftMutation.isPending ||
        regenerateDraftMutation.isPending ||
        resolveMutation.isPending
      }
      generatePending={generateDraftMutation.isPending || regenerateDraftMutation.isPending}
      suggestedActions={draft?.suggested_actions ?? []}
      draftId={draft?.id}
      replyAddressee={replyAddressee}
      actionError={actionError}
      approveOpen={approveOpen}
      rejectOpen={rejectOpen}
      resolvePromptOpen={resolvePromptOpen}
      rewriteOpen={rewriteOpen}
      rewritePending={regenerateDraftMutation.isPending}
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
      threadContextEnabled={threadContextEnabled}
      showLegacyInsights={showLegacyInsights}
    />
  )
}
