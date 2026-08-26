"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { ApplySiblingsDialog } from "@/components/apply-siblings-dialog"
import { AuditSection } from "@/components/thread-triage/audit-section"
import { ClassificationSection } from "@/components/thread-triage/classification-section"
import { DraftSection } from "@/components/thread-triage/draft-section"
import { InsightsPanel } from "@/components/thread-triage/insights-panel"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsIndicator, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useSiblingsPrompt } from "@/hooks/use-siblings-prompt"
import { api } from "@/lib/api-client"
import {
  REJECT_REASON_CODES,
  REJECT_REASON_LABELS,
  type RejectReasonCode,
} from "@/lib/routing"
import type {
  ActivityEntry,
  AuditEntry,
  ClassificationView,
  DraftView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

const REJECT_ITEMS = [
  { label: "Select a reason", value: null },
  ...REJECT_REASON_CODES.map((code) => ({
    label: REJECT_REASON_LABELS[code],
    value: code,
  })),
]

const feedbackBadge = (
  draft: DraftView,
): { label: string; tone: "green" | "blue" | "red" | "amber" } | null => {
  if (draft.feedback_action === "approve" && draft.edited_body) {
    return { label: "Edited & Approved", tone: "blue" }
  }
  if (draft.feedback_action === "approve" || draft.approved_at) {
    return { label: "Approved", tone: "green" }
  }
  if (draft.feedback_action === "reject" || draft.rejected_at) {
    return { label: "Rejected", tone: "red" }
  }
  if (draft.feedback_action === "wrong") {
    return { label: "Marked as no reply needed", tone: "amber" }
  }
  return null
}

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
    if (presentation?.suggest_resolve_default) {
      setResolvePromptOpen(true)
    }
  }

  const invalidateReviewQueues = async () => {
    await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
    await queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] })
    await queryClient.invalidateQueries({ queryKey: ["mailbox"] })
  }

  const draftId = draft?.id

  const approveMutation = useMutation({
    mutationFn: (body?: {
      edited_body?: string
      approval_note?: string
      approval_scope?: "once" | "similar"
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
    mutationFn: async (payload: {
      feedback_note: string
      reason_code: RejectReasonCode
    }) => {
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

  const handleOpenApprove = () => {
    setApproveBody(draft?.body ?? "")
    setApprovalNote("")
    setApprovalScope("")
    setApproveOpen(true)
  }

  const handleApproveKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleOpenApprove()
    }
  }

  const handleRejectKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      setRejectOpen(true)
    }
  }

  const handleConfirmApprove = () => {
    if (!draft) return
    const trimmed = approveBody.trim()
    if (!trimmed) return
    const note = approvalNote.trim()
    if (note && !approvalScope) return

    const payload: {
      edited_body?: string
      approval_note?: string
      approval_scope?: "once" | "similar"
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

  const teachingNote = thread.teaching_note ?? draft?.teaching_note ?? null
  const urgency =
    presentation?.urgency_assessed ??
    thread.urgency ??
    draft?.urgency ??
    classification?.urgency ??
    null
  const urgencyReason = draft?.urgency_reason ?? null
  const badge = draft ? feedbackBadge(draft) : null
  const feedbackDone = Boolean(
    draft?.approved_at || draft?.rejected_at || draft?.feedback_action,
  )
  const busy = approveMutation.isPending || rejectMutation.isPending
  const suggestedActions = draft?.suggested_actions ?? []

  const resolveMutation = useMutation({
    mutationFn: () => api.threads.resolve(threadId),
    onSuccess: async () => {
      setResolvePromptOpen(false)
      await invalidateReviewQueues()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

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
            onApprove={handleOpenApprove}
            onApproveKeyDown={handleApproveKeyDown}
            onReject={() => setRejectOpen(true)}
            onRejectKeyDown={handleRejectKeyDown}
          />
        </TabsContent>

        <TabsContent value="audit" className="outline-none">
          <AuditSection auditLog={auditLog} />
        </TabsContent>
      </Tabs>


      <Dialog open={approveOpen} onOpenChange={setApproveOpen}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Approve draft</DialogTitle>
            <DialogDescription>
              Review the reply body. Edit if needed, then approve. Email is never
              sent from this app.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <p className="mb-1 text-xs text-gray-500">Draft body</p>
              <Textarea
                value={approveBody}
                onChange={(event) => setApproveBody(event.target.value)}
                aria-label="Draft body to approve"
                name="approve_body"
                autoComplete="off"
                rows={14}
                className="min-h-[16rem]"
              />
            </div>
            <div className="space-y-3 border-t border-gray-200 pt-4 dark:border-gray-700">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  Learning note
                </p>
                <p className="mt-0.5 text-xs text-gray-500">
                  Optional. Add guidance only if you want to teach the system,
                  for example after an edit, or a rule that should apply to
                  similar emails later.
                </p>
              </div>
              <Textarea
                value={approvalNote}
                onChange={(event) => setApprovalNote(event.target.value)}
                aria-label="Approval learning note"
                name="approval_note"
                autoComplete="off"
                rows={3}
                placeholder="e.g. Soften tone and lead with the invoice number…"
              />
              <fieldset className="space-y-2">
                <legend className="text-xs text-gray-500">
                  If you add a note, where should it apply?
                </legend>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={approvalScope === "once" ? "default" : "outline"}
                    tabIndex={0}
                    aria-label="Apply learning to this thread only"
                    aria-pressed={approvalScope === "once"}
                    onClick={() => setApprovalScope("once")}
                  >
                    Just this thread
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={approvalScope === "similar" ? "default" : "outline"}
                    tabIndex={0}
                    aria-label="Apply learning to similar emails"
                    aria-pressed={approvalScope === "similar"}
                    onClick={() => setApprovalScope("similar")}
                  >
                    Similar emails in the future
                  </Button>
                </div>
                {approvalNote.trim() && !approvalScope ? (
                  <p className="text-xs text-amber-600 dark:text-amber-400" role="status">
                    Choose a scope when providing a learning note.
                  </p>
                ) : null}
              </fieldset>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel approve"
              onClick={() => setApproveOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Confirm approve draft"
              disabled={
                !approveBody.trim() ||
                busy ||
                (Boolean(approvalNote.trim()) && !approvalScope)
              }
              onClick={handleConfirmApprove}
            >
              {approveMutation.isPending ? "Approving…" : "Approve"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Reject draft</DialogTitle>
            <DialogDescription>
              Explain why this draft should be rejected. Email is never sent.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500" htmlFor="reject-reason">
                Why is this wrong?
              </label>
              <Select
                items={REJECT_ITEMS}
                value={rejectReason || null}
                onValueChange={(value) =>
                  setRejectReason((value ?? "") as RejectReasonCode | "")
                }
              >
                <SelectTrigger
                  id="reject-reason"
                  aria-label="Rejection reason"
                  className="mt-1 w-full"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {REJECT_ITEMS.map((item) => (
                      <SelectItem key={item.value ?? "placeholder"} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-gray-500">
                &ldquo;Wrong action / no reply needed&rdquo; marks this thread as no
                action and leaves Needs Attention. Any other reason will teach the
                system what to change next time.
              </p>
            </div>
            <Textarea
              value={rejectNote}
              onChange={(event) => setRejectNote(event.target.value)}
              aria-label="Rejection note"
              name="reject_note"
              autoComplete="off"
              rows={6}
              placeholder="Why is this draft wrong…"
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel reject"
              onClick={() => setRejectOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              tabIndex={0}
              aria-label="Confirm reject draft"
              disabled={!rejectNote.trim() || !rejectReason || busy}
              onClick={handleReject}
            >
              {rejectMutation.isPending ? "Rejecting…" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={resolvePromptOpen} onOpenChange={setResolvePromptOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark this thread resolved?</DialogTitle>
            <DialogDescription>
              Review is done. Mark resolved to leave Needs Attention, or keep it
              open if you are still waiting on someone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Keep thread open"
              onClick={() => setResolvePromptOpen(false)}
            >
              Keep open
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Mark thread resolved"
              disabled={resolveMutation.isPending}
              onClick={() => resolveMutation.mutate()}
            >
              {resolveMutation.isPending ? "Resolving…" : "Mark resolved"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
