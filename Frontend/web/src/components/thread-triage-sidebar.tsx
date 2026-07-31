"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Check, Pencil, RefreshCw, TriangleAlert, X } from "lucide-react"

import { EmailBody } from "@/components/email-body"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api-client"
import { formatEventName, formatRelativeTime } from "@/lib/design-tokens"
import type {
  AuditEntry,
  ClassificationView,
  DraftView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

const Panel = ({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) => {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
      <h3 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
        {title}
      </h3>
      {children}
    </section>
  )
}

const Field = ({
  label,
  value,
}: {
  label: string
  value: React.ReactNode
}) => {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <div className="mt-0.5 text-sm text-gray-900 dark:text-gray-100">{value}</div>
    </div>
  )
}

const feedbackBadge = (draft: DraftView): { label: string; tone: "green" | "blue" | "red" | "amber" } | null => {
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
    return { label: "Marked Wrong", tone: "amber" }
  }
  return null
}

const REGENERATE_PRESETS = [
  "Acknowledge only",
  "Escalate",
  "Forward to specific person",
  "Provide action items",
] as const

export const ThreadTriageSidebar = ({
  threadId,
  thread,
  classification,
  draft,
  triage,
  auditLog,
}: {
  threadId: string
  thread: ThreadSummary
  classification: ClassificationView | null
  draft: DraftView | null
  triage: TriageFlags | null
  auditLog: AuditEntry[]
}) => {
  const queryClient = useQueryClient()
  const [showAudit, setShowAudit] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [wrongOpen, setWrongOpen] = useState(false)
  const [regenOpen, setRegenOpen] = useState(false)
  const [editBody, setEditBody] = useState("")
  const [rejectNote, setRejectNote] = useState("")
  const [wrongNote, setWrongNote] = useState("")
  const [regenInstruction, setRegenInstruction] = useState("")
  const [actionError, setActionError] = useState<string | null>(null)

  const invalidateThread = async () => {
    await queryClient.invalidateQueries({ queryKey: ["thread", threadId] })
  }

  const draftId = draft?.id

  const approveMutation = useMutation({
    mutationFn: (body?: { edited_body?: string }) => {
      if (!draftId) {
        throw new Error("No draft available to approve")
      }
      return api.drafts.approve(draftId, body)
    },
    onSuccess: async () => {
      setEditOpen(false)
      setActionError(null)
      await invalidateThread()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const rejectMutation = useMutation({
    mutationFn: (feedback_note: string) => {
      if (!draftId) {
        throw new Error("No draft available to reject")
      }
      return api.drafts.reject(draftId, { feedback_note })
    },
    onSuccess: async () => {
      setRejectOpen(false)
      setRejectNote("")
      setActionError(null)
      await invalidateThread()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const wrongMutation = useMutation({
    mutationFn: (feedback_note: string) => {
      if (!draftId) {
        throw new Error("No draft available to mark wrong")
      }
      return api.drafts.markWrong(draftId, { feedback_note })
    },
    onSuccess: async () => {
      setWrongOpen(false)
      setWrongNote("")
      setActionError(null)
      await invalidateThread()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const regenMutation = useMutation({
    mutationFn: (instruction: string) =>
      api.drafts.regenerate(threadId, { instruction }),
    onSuccess: async () => {
      setRegenOpen(false)
      setRegenInstruction("")
      setActionError(null)
      await invalidateThread()
    },
    onError: (error: Error) => {
      setActionError(error.message)
    },
  })

  const handleToggleAudit = () => {
    setShowAudit((value) => !value)
  }

  const handleAuditKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      handleToggleAudit()
    }
  }

  const handleOpenEdit = () => {
    setEditBody(draft?.body ?? "")
    setEditOpen(true)
  }

  const handleApprove = () => {
    approveMutation.mutate(undefined)
  }

  const handleEditApprove = () => {
    if (!editBody.trim()) return
    approveMutation.mutate({ edited_body: editBody.trim() })
  }

  const handleReject = () => {
    if (!rejectNote.trim()) return
    rejectMutation.mutate(rejectNote.trim())
  }

  const handleWrong = () => {
    if (!wrongNote.trim()) return
    wrongMutation.mutate(wrongNote.trim())
  }

  const handleRegenerate = () => {
    if (!regenInstruction.trim()) return
    regenMutation.mutate(regenInstruction.trim())
  }

  const teachingNote = thread.teaching_note ?? draft?.teaching_note ?? null
  const urgency = thread.urgency ?? draft?.urgency ?? classification?.urgency ?? null
  const urgencyReason = draft?.urgency_reason ?? null
  const badge = draft ? feedbackBadge(draft) : null
  const feedbackDone = Boolean(
    draft?.approved_at || draft?.rejected_at || draft?.feedback_action,
  )
  const busy =
    approveMutation.isPending ||
    rejectMutation.isPending ||
    wrongMutation.isPending ||
    regenMutation.isPending
  const suggestedActions = draft?.suggested_actions ?? []

  return (
    <div className="space-y-4">
      <Panel title="Insights">
        <div className="space-y-4">
          <div>
            <p className="text-xs text-gray-400">Teaching note</p>
            {teachingNote ? (
              <EmailBody
                text={teachingNote}
                className="mt-1 rounded-md bg-blue-50 px-3 py-2 text-gray-800 dark:bg-blue-950/30 dark:text-gray-100"
              />
            ) : (
              <p className="mt-1 text-sm text-gray-400 italic">
                No teaching note yet — this thread hasn&apos;t produced a draft.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="State"
              value={
                <StatusBadge label={stateLabel(thread.state)} tone={stateTone(thread.state)} />
              }
            />
            <Field
              label="Urgency"
              value={
                urgency ? (
                  <div className="space-y-1">
                    <StatusBadge label={urgency} tone={urgencyTone(urgency)} />
                    {urgencyReason ? (
                      <p className="text-xs text-gray-500">{urgencyReason}</p>
                    ) : null}
                  </div>
                ) : (
                  "—"
                )
              }
            />
          </div>
        </div>
      </Panel>

      <Panel title="Suggested Process">
        {suggestedActions.length > 0 ? (
          <ol className="space-y-3">
            {suggestedActions
              .slice()
              .sort((a, b) => a.step - b.step)
              .map((item) => (
                <li key={`${item.step}-${item.action}`} className="flex gap-3">
                  <span
                    className="flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                    aria-hidden="true"
                  >
                    {item.step}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {item.action}
                    </p>
                    {item.stakeholder ? (
                      <span className="mt-1 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                        {item.stakeholder}
                      </span>
                    ) : null}
                    <p className="mt-1 text-xs text-gray-500">{item.rationale}</p>
                  </div>
                </li>
              ))}
          </ol>
        ) : (
          <p className="text-sm text-gray-500">
            No suggested process — this thread hasn&apos;t produced a draft.
          </p>
        )}
      </Panel>

      <Panel title="Triage">
        {triage ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {triage.is_spam != null ? (
                <StatusBadge
                  label={triage.is_spam ? "Spam" : "Not spam"}
                  tone={triage.is_spam ? "red" : "green"}
                />
              ) : null}
              {triage.has_action_items != null ? (
                <StatusBadge
                  label={triage.has_action_items ? "Action needed" : "No action"}
                  tone={triage.has_action_items ? "amber" : "neutral"}
                />
              ) : null}
              {triage.needs_context != null ? (
                <StatusBadge
                  label={triage.needs_context ? "Needs context" : "Context OK"}
                  tone={triage.needs_context ? "amber" : "green"}
                />
              ) : null}
            </div>
            {triage.action_items_summary ? (
              <Field label="Action items" value={triage.action_items_summary} />
            ) : null}
            {triage.spam_reason ? (
              <Field label="Spam reason" value={triage.spam_reason} />
            ) : null}
            {triage.context_reason ? (
              <Field label="Context reason" value={triage.context_reason} />
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            No triage result yet. Classification may still be pending.
          </p>
        )}
      </Panel>

      <Panel title="Thread details">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Category" value={thread.category ?? "—"} />
            <Field label="Messages" value={thread.message_count} />
            <Field label="Staleness" value={`${thread.staleness_hours.toFixed(1)}h`} />
            <Field label="Draft" value={thread.has_draft ? "Available" : "None"} />
          </div>
          {thread.outlook_url ? (
            <a
              href={thread.outlook_url}
              target="_blank"
              rel="noopener noreferrer"
              tabIndex={0}
              aria-label="Open thread in Outlook"
              className="inline-block cursor-pointer text-sm text-blue-600 hover:underline dark:text-blue-400"
            >
              Open in Outlook
            </a>
          ) : null}
        </div>
      </Panel>

      <Panel title="Draft reply">
        {draft ? (
          <div className="space-y-3">
            {badge ? (
              <StatusBadge label={badge.label} tone={badge.tone} />
            ) : null}
            <Field label="Subject" value={draft.subject} />
            <div>
              <p className="text-xs text-gray-400">Body</p>
              <div className="mt-1 overflow-auto">
                <EmailBody text={draft.body} />
              </div>
            </div>
            {draft.forward_to ? (
              <Field label="Forward to" value={draft.forward_to} />
            ) : null}
            {draft.feedback_note ? (
              <Field label="Feedback note" value={draft.feedback_note} />
            ) : null}

            {actionError ? (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {actionError}
              </p>
            ) : null}

            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                type="button"
                size="sm"
                tabIndex={0}
                aria-label="Approve draft"
                disabled={feedbackDone || busy}
                onClick={handleApprove}
              >
                <Check aria-hidden="true" />
                Approve
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                tabIndex={0}
                aria-label="Edit and approve draft"
                disabled={feedbackDone || busy}
                onClick={handleOpenEdit}
              >
                <Pencil aria-hidden="true" />
                Edit & Approve
              </Button>
              <Button
                type="button"
                size="sm"
                variant="destructive"
                tabIndex={0}
                aria-label="Reject draft"
                disabled={feedbackDone || busy}
                onClick={() => setRejectOpen(true)}
              >
                <X aria-hidden="true" />
                Reject
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                tabIndex={0}
                aria-label="Mark draft as wrong"
                disabled={feedbackDone || busy}
                onClick={() => setWrongOpen(true)}
              >
                <TriangleAlert aria-hidden="true" />
                Wrong
              </Button>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                tabIndex={0}
                aria-label="Regenerate draft with different approach"
                disabled={busy}
                onClick={() => setRegenOpen(true)}
              >
                <RefreshCw aria-hidden="true" />
                Regenerate
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">No draft generated for this thread.</p>
        )}
      </Panel>

      <Panel title="Audit">
        <button
          type="button"
          tabIndex={0}
          aria-expanded={showAudit}
          aria-label="Toggle audit log"
          onClick={handleToggleAudit}
          onKeyDown={handleAuditKeyDown}
          className="cursor-pointer text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          {showAudit ? "Hide audit log" : `Show audit log (${auditLog.length})`}
        </button>
        {showAudit ? (
          <ul className="mt-3 space-y-2">
            {auditLog.length === 0 ? (
              <li className="text-sm text-gray-500">No audit events.</li>
            ) : (
              auditLog.map((entry, index) => (
                <li
                  key={`${entry.timestamp}-${index}`}
                  className="rounded border border-gray-100 p-2 dark:border-gray-800"
                >
                  <div className="flex justify-between gap-2">
                    <span className="text-xs font-medium">
                      {formatEventName(entry.event)}
                    </span>
                    <span className="text-xs text-gray-400">
                      {formatRelativeTime(entry.timestamp)}
                    </span>
                  </div>
                  {entry.detail ? (
                    <p className="mt-1 line-clamp-3 text-xs text-gray-500">
                      {entry.detail}
                    </p>
                  ) : null}
                </li>
              ))
            )}
          </ul>
        ) : null}
      </Panel>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Edit & approve draft</DialogTitle>
            <DialogDescription>
              Adjust the reply body, then approve. Email is never sent from this app.
            </DialogDescription>
          </DialogHeader>
          <textarea
            value={editBody}
            onChange={(event) => setEditBody(event.target.value)}
            aria-label="Edited draft body"
            rows={10}
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel edit"
              onClick={() => setEditOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Save edited draft and approve"
              disabled={!editBody.trim() || busy}
              onClick={handleEditApprove}
            >
              {approveMutation.isPending ? "Saving…" : "Save & Approve"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject draft</DialogTitle>
            <DialogDescription>
              Explain why this draft should be rejected. Email is never sent.
            </DialogDescription>
          </DialogHeader>
          <textarea
            value={rejectNote}
            onChange={(event) => setRejectNote(event.target.value)}
            aria-label="Rejection note"
            rows={4}
            placeholder="Why is this draft wrong?"
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
          />
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
              disabled={!rejectNote.trim() || busy}
              onClick={handleReject}
            >
              {rejectMutation.isPending ? "Rejecting…" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={wrongOpen} onOpenChange={setWrongOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark draft as wrong</DialogTitle>
            <DialogDescription>
              Tell the system what was wrong so future drafts can improve.
            </DialogDescription>
          </DialogHeader>
          <textarea
            value={wrongNote}
            onChange={(event) => setWrongNote(event.target.value)}
            aria-label="Wrong draft note"
            rows={4}
            placeholder="What should have been different?"
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel mark wrong"
              onClick={() => setWrongOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Confirm mark draft as wrong"
              disabled={!wrongNote.trim() || busy}
              onClick={handleWrong}
            >
              {wrongMutation.isPending ? "Saving…" : "Mark Wrong"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={regenOpen} onOpenChange={setRegenOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Regenerate with a different approach</DialogTitle>
            <DialogDescription>
              Describe the path you want (acknowledge only, escalate, etc.). A new draft is created.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-wrap gap-2">
            {REGENERATE_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                tabIndex={0}
                aria-label={`Use preset: ${preset}`}
                onClick={() => setRegenInstruction(preset)}
                className="cursor-pointer rounded-full border border-gray-300 px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
              >
                {preset}
              </button>
            ))}
          </div>
          <textarea
            value={regenInstruction}
            onChange={(event) => setRegenInstruction(event.target.value)}
            aria-label="Regeneration instruction"
            rows={4}
            placeholder="e.g., just acknowledge receipt, no action items"
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              tabIndex={0}
              aria-label="Cancel regenerate"
              onClick={() => setRegenOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              tabIndex={0}
              aria-label="Regenerate draft"
              disabled={!regenInstruction.trim() || busy}
              onClick={handleRegenerate}
            >
              {regenMutation.isPending ? "Regenerating…" : "Regenerate Draft"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
