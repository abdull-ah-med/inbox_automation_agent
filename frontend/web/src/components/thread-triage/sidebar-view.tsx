"use client"

import { ApplySiblingsDialog } from "@/components/apply-siblings-dialog"
import { AuditSection } from "@/components/thread-triage/audit-section"
import { ClassificationSection } from "@/components/thread-triage/classification-section"
import { DraftSection } from "@/components/thread-triage/draft-section"
import { InsightsPanel } from "@/components/thread-triage/insights-panel"
import {
  ApproveDraftDialog,
  RejectDraftDialog,
  ResolvePromptDialog,
} from "@/components/thread-triage/review-dialogs"
import { Tabs, TabsContent, TabsIndicator, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"
import type { RejectReasonCode } from "@/lib/routing"
import type {
  ActivityEntry,
  AuditEntry,
  DraftView,
  ReplyAddresseeView,
  SuggestedAction,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

type FeedbackBadge = { label: string; tone: "green" | "blue" | "red" | "amber" } | null
type SiblingsPrompt = ReturnType<typeof useSiblingsPrompt>

type ThreadTriageSidebarViewProps = {
  threadId: string
  thread: ThreadSummary
  draft: DraftView | null
  triage: TriageFlags | null
  auditLog: AuditEntry[]
  activity: ActivityEntry[]
  teachingNote: string | null
  urgency: string | null
  urgencyReason: string | null
  badge: FeedbackBadge
  feedbackDone: boolean
  busy: boolean
  generatePending: boolean
  suggestedActions: SuggestedAction[]
  draftId: string | undefined
  replyAddressee: ReplyAddresseeView | null
  actionError: string | null
  approveOpen: boolean
  rejectOpen: boolean
  resolvePromptOpen: boolean
  resolveActionsTaken: string
  resolveInvolved: string
  approveBody: string
  approvalNote: string
  approvalScope: "once" | "similar" | ""
  rejectNote: string
  rejectReason: RejectReasonCode | ""
  approvePending: boolean
  rejectPending: boolean
  resolvePending: boolean
  siblings: SiblingsPrompt
  onApproveOpenChange: (open: boolean) => void
  onRejectOpenChange: (open: boolean) => void
  onResolvePromptOpenChange: (open: boolean) => void
  onResolveActionsTakenChange: (value: string) => void
  onResolveInvolvedChange: (value: string) => void
  onMarkResolved: () => void
  onApproveBodyChange: (value: string) => void
  onApprovalNoteChange: (value: string) => void
  onApprovalScopeChange: (value: "once" | "similar" | "") => void
  onRejectNoteChange: (value: string) => void
  onRejectReasonChange: (value: RejectReasonCode | "") => void
  onOpenApprove: () => void
  onApproveKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onRejectKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onConfirmApprove: () => void
  onConfirmReject: () => void
  onConfirmResolve: () => void
  onGenerateDraft: () => void
  onGenerateDraftKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
}

export const ThreadTriageSidebarView = (props: ThreadTriageSidebarViewProps) => {
  const {
    threadId,
    thread,
    draft,
    triage,
    auditLog,
    activity,
    teachingNote,
    urgency,
    urgencyReason,
    badge,
    feedbackDone,
    busy,
    generatePending,
    suggestedActions,
    draftId,
    actionError,
    approveOpen,
    rejectOpen,
    resolvePromptOpen,
    resolveActionsTaken,
    resolveInvolved,
    approveBody,
    approvalNote,
    approvalScope,
    rejectNote,
    rejectReason,
    approvePending,
    rejectPending,
    resolvePending,
    siblings,
    onApproveOpenChange,
    onRejectOpenChange,
    onResolvePromptOpenChange,
    onResolveActionsTakenChange,
    onResolveInvolvedChange,
    onMarkResolved,
    onApproveBodyChange,
    onApprovalNoteChange,
    onApprovalScopeChange,
    onRejectNoteChange,
    onRejectReasonChange,
    onOpenApprove,
    onApproveKeyDown,
    onRejectKeyDown,
    onConfirmApprove,
    onConfirmReject,
    onConfirmResolve,
    onGenerateDraft,
    onGenerateDraftKeyDown,
  } = props

  return (
    <div className="space-y-4">
      <InsightsPanel
        thread={thread}
        teachingNote={teachingNote}
        urgency={urgency}
        urgencyReason={urgencyReason}
        draftId={draftId}
        feedbackDone={feedbackDone}
        busy={busy}
        activity={activity}
        resolvePending={resolvePending}
        onMarkResolved={onMarkResolved}
        onUrgencySaved={(payload) => {
          void siblings.prompt("urgency", payload.reason, payload.urgency)
        }}
      />

      <Tabs defaultValue="classification" className="w-full gap-3">
        <TabsList className="w-full" aria-label="Thread review sections">
          <TabsTrigger value="classification">Classification</TabsTrigger>
          <TabsTrigger value="draft">Draft</TabsTrigger>
          <TabsTrigger value="audit">Audit ({auditLog.length})</TabsTrigger>
          <TabsIndicator />
        </TabsList>

        <TabsContent value="classification" className="space-y-4 outline-none">
          <ClassificationSection
            threadId={threadId}
            thread={thread}
            triage={triage}
            suggestedActions={suggestedActions}
          />
        </TabsContent>

        <TabsContent value="draft" className="outline-none">
          <DraftSection
            draft={draft}
            badge={badge}
            actionError={actionError}
            feedbackDone={feedbackDone}
            busy={busy}
            generatePending={generatePending}
            mailboxKey={thread.mailbox_key}
            replyAddressee={props.replyAddressee}
            onApprove={onOpenApprove}
            onApproveKeyDown={onApproveKeyDown}
            onReject={() => onRejectOpenChange(true)}
            onRejectKeyDown={onRejectKeyDown}
            onGenerateDraft={onGenerateDraft}
            onGenerateDraftKeyDown={onGenerateDraftKeyDown}
          />
        </TabsContent>

        <TabsContent value="audit" className="outline-none">
          <AuditSection auditLog={auditLog} />
        </TabsContent>
      </Tabs>

      <ApproveDraftDialog
        open={approveOpen}
        onOpenChange={onApproveOpenChange}
        approveBody={approveBody}
        onApproveBodyChange={onApproveBodyChange}
        approvalNote={approvalNote}
        onApprovalNoteChange={onApprovalNoteChange}
        approvalScope={approvalScope}
        onApprovalScopeChange={onApprovalScopeChange}
        busy={busy}
        isPending={approvePending}
        onConfirm={onConfirmApprove}
      />
      <RejectDraftDialog
        open={rejectOpen}
        onOpenChange={onRejectOpenChange}
        rejectNote={rejectNote}
        onRejectNoteChange={onRejectNoteChange}
        rejectReason={rejectReason}
        onRejectReasonChange={onRejectReasonChange}
        busy={busy}
        isPending={rejectPending}
        onConfirm={onConfirmReject}
      />
      <ResolvePromptDialog
        open={resolvePromptOpen}
        onOpenChange={onResolvePromptOpenChange}
        actionsTaken={resolveActionsTaken}
        onActionsTakenChange={onResolveActionsTakenChange}
        involved={resolveInvolved}
        onInvolvedChange={onResolveInvolvedChange}
        isPending={resolvePending}
        onConfirm={onConfirmResolve}
      />
      <ApplySiblingsDialog
        key={siblings.items.map((item) => item.thread_id).join(",")}
        open={siblings.open}
        sourceThreadId={threadId}
        items={siblings.items}
        treatment={siblings.treatment}
        reason={siblings.reason}
        urgency={siblings.urgency}
        onOpenChange={siblings.setOpen}
      />
    </div>
  )
}
