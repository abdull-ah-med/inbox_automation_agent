"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Check, X } from "lucide-react"

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
import {
  REJECT_REASON_CODES,
  type RejectReasonCode,
} from "@/lib/routing"
import type {
  AuditEntry,
  ClassificationView,
  DraftView,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"

const REJECT_REASON_LABELS: Record<RejectReasonCode, string> = {
  tone: "Tone off",
  factual: "Factual error",
  wrong_action: "Wrong action / no reply needed",
  incomplete: "Incomplete",
  policy: "Policy conflict",
  recipients: "Wrong recipients",
  other: "Other",
}

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
  const [approveOpen, setApproveOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [approveBody, setApproveBody] = useState("")
  const [rejectNote, setRejectNote] = useState("")
  const [rejectReason, setRejectReason] = useState<RejectReasonCode | "">("")
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
      setApproveOpen(false)
      setActionError(null)
      await invalidateThread()
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
    onSuccess: async () => {
      setRejectOpen(false)
      setRejectNote("")
      setRejectReason("")
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

  const handleOpenApprove = () => {
    setApproveBody(draft?.body ?? "")
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
    if (trimmed === draft.body.trim()) {
      approveMutation.mutate(undefined)
      return
    }
    approveMutation.mutate({ edited_body: trimmed })
  }

  const handleReject = () => {
    if (!rejectNote.trim() || !rejectReason) return
    rejectMutation.mutate({
      feedback_note: rejectNote.trim(),
      reason_code: rejectReason,
    })
  }

  const teachingNote = thread.teaching_note ?? draft?.teaching_note ?? null
  const urgency = thread.urgency ?? draft?.urgency ?? classification?.urgency ?? null
  const urgencyReason = draft?.urgency_reason ?? null
  const badge = draft ? feedbackBadge(draft) : null
  const feedbackDone = Boolean(
    draft?.approved_at || draft?.rejected_at || draft?.feedback_action,
  )
  const busy = approveMutation.isPending || rejectMutation.isPending
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

            <div>
              <p className="text-xs text-gray-400">Skills used</p>
              {(draft.applied_skills?.length ?? 0) === 0 ? (
                <p className="mt-1 text-sm text-gray-500">
                  No skills applied for this draft
                </p>
              ) : (
                <ul className="mt-2 space-y-2">
                  {draft.applied_skills.map((skill) => {
                    const refs = (draft.tool_calls ?? [])
                      .filter(
                        (call) =>
                          call.skill_id === skill.id &&
                          !call.is_error &&
                          Boolean(call.path),
                      )
                      .map((call) => call.path)
                    const uniqueRefs = [...new Set(refs)]
                    return (
                      <li key={skill.id} className="space-y-1">
                        <StatusBadge label={skill.name} tone="blue" />
                        {uniqueRefs.length > 0 ? (
                          <ul className="ml-1 list-disc space-y-0.5 pl-4 text-xs text-gray-600 dark:text-gray-400">
                            {uniqueRefs.map((path) => (
                              <li key={path}>{path}</li>
                            ))}
                          </ul>
                        ) : null}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>

            {actionError ? (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {actionError}
              </p>
            ) : null}

            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                type="button"
                size="sm"
                variant="outline"
                tabIndex={0}
                aria-label="Approve draft"
                disabled={feedbackDone || busy}
                onClick={handleOpenApprove}
                onKeyDown={handleApproveKeyDown}
              >
                <Check aria-hidden="true" />
                Approve
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                tabIndex={0}
                aria-label="Reject draft"
                disabled={feedbackDone || busy}
                onClick={() => setRejectOpen(true)}
                onKeyDown={handleRejectKeyDown}
              >
                <X aria-hidden="true" />
                Reject
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

      <Dialog open={approveOpen} onOpenChange={setApproveOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Approve draft</DialogTitle>
            <DialogDescription>
              Review the reply body. Edit if needed, then approve. Email is never
              sent from this app.
            </DialogDescription>
          </DialogHeader>
          <textarea
            value={approveBody}
            onChange={(event) => setApproveBody(event.target.value)}
            aria-label="Draft body to approve"
            name="approve_body"
            autoComplete="off"
            rows={10}
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
          />
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
              disabled={!approveBody.trim() || busy}
              onClick={handleConfirmApprove}
            >
              {approveMutation.isPending ? "Approving…" : "Approve"}
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
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500" htmlFor="reject-reason">
                Why is this wrong?
              </label>
              <select
                id="reject-reason"
                name="reject_reason"
                value={rejectReason}
                onChange={(event) =>
                  setRejectReason(event.target.value as RejectReasonCode | "")
                }
                aria-label="Rejection reason"
                autoComplete="off"
                className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
              >
                <option value="">Select a reason</option>
                {REJECT_REASON_CODES.map((code) => (
                  <option key={code} value={code}>
                    {REJECT_REASON_LABELS[code]}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-gray-500">
                &ldquo;Wrong action / no reply needed&rdquo; just flags this thread as
                not requiring a reply. Any other reason will teach the system what
                to change next time.
              </p>
            </div>
            <textarea
              value={rejectNote}
              onChange={(event) => setRejectNote(event.target.value)}
              aria-label="Rejection note"
              name="reject_note"
              autoComplete="off"
              rows={4}
              placeholder="Why is this draft wrong…"
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
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
    </div>
  )
}
