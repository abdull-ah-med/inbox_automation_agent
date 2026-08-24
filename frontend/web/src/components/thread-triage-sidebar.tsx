"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Check, X } from "lucide-react"

import { ApplySiblingsDialog } from "@/components/apply-siblings-dialog"
import { EmailBody } from "@/components/email-body"
import { NotSpamButton } from "@/components/not-spam-button"
import { PresentationBadges } from "@/components/presentation-badges"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { UrgencyEditPopover } from "@/components/urgency-edit-popover"
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api } from "@/lib/api-client"
import { formatEventName, formatRelativeTime } from "@/lib/design-tokens"
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
  RelatedThreadItem,
  ThreadSummary,
  TriageFlags,
} from "@/lib/types"
import { cn, textLinkClass } from "@/lib/utils"

const REJECT_ITEMS = [
  { label: "Select a reason", value: null },
  ...REJECT_REASON_CODES.map((code) => ({
    label: REJECT_REASON_LABELS[code],
    value: code,
  })),
]

const Panel = ({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) => {
  return (
    <Card>
      <CardHeader className="pb-0">
        <CardTitle className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
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
  const [siblingOpen, setSiblingOpen] = useState(false)
  const [siblingItems, setSiblingItems] = useState<RelatedThreadItem[]>([])
  const [siblingTreatment, setSiblingTreatment] = useState<"no_reply" | "urgency">(
    "no_reply",
  )
  const [siblingReason, setSiblingReason] = useState("")
  const [siblingUrgency, setSiblingUrgency] = useState<
    "CRITICAL" | "HIGH" | "NORMAL" | "LOW" | undefined
  >(undefined)

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

  const promptSiblings = async (
    treatment: "no_reply" | "urgency",
    reason: string,
    urgency?: "CRITICAL" | "HIGH" | "NORMAL" | "LOW",
  ) => {
    try {
      const data = await api.threads.related(threadId, "siblings")
      if (data.items.length === 0) return
      setSiblingItems(data.items)
      setSiblingTreatment(treatment)
      setSiblingReason(reason)
      setSiblingUrgency(urgency)
      setSiblingOpen(true)
    } catch {
      // Source action already succeeded; an empty prompt is the fallback.
    }
  }

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
        await promptSiblings("no_reply", payload.feedback_note)
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
  const history = presentation?.triage_history

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
                No teaching note yet. This thread hasn&apos;t produced a draft.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="State"
              value={
                presentation?.badges_now?.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    <PresentationBadges
                      badges={presentation.badges_now.filter((b) => b.kind === "state")}
                    />
                  </div>
                ) : (
                  <StatusBadge label={stateLabel(thread.state)} tone={stateTone(thread.state)} />
                )
              }
            />
            <Field
              label="Urgency"
              value={
                urgency ? (
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <StatusBadge
                        label={urgency}
                        tone={
                          presentation && !presentation.urgency_active
                            ? "neutral"
                            : urgencyTone(urgency)
                        }
                      />
                      {presentation && !presentation.urgency_active ? (
                        <span className="text-xs text-muted-foreground">inactive</span>
                      ) : null}
                      {draftId && !feedbackDone && presentation?.urgency_active !== false ? (
                        <UrgencyEditPopover
                          draftId={draftId}
                          threadId={threadId}
                          currentUrgency={urgency}
                          disabled={busy}
                          onSaved={(payload) => {
                            void promptSiblings(
                              "urgency",
                              payload.reason,
                              payload.urgency,
                            )
                          }}
                        />
                      ) : null}
                    </div>
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

          {activity.length > 0 ? (
            <div>
              <p className="text-xs text-gray-400">Activity</p>
              <ul className="mt-2 space-y-2">
                {activity
                  .slice()
                  .reverse()
                  .slice(0, 5)
                  .map((entry) => (
                    <li
                      key={`${entry.event_type}-${entry.timestamp}`}
                      className="rounded-md border border-border/60 px-2.5 py-2"
                    >
                      <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                        {entry.title}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">{entry.body}</p>
                      <p className="mt-1 text-[11px] text-gray-400">
                        {formatRelativeTime(entry.timestamp)}
                      </p>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
        </div>
      </Panel>

      <Tabs defaultValue="classification" className="w-full gap-3">
        <TabsList className="w-full" aria-label="Thread review sections">
          <TabsTrigger value="classification">Classification</TabsTrigger>
          <TabsTrigger value="draft">Draft</TabsTrigger>
          <TabsTrigger value="audit">Audit ({auditLog.length})</TabsTrigger>
          <TabsIndicator />
        </TabsList>

        <TabsContent value="classification" className="space-y-4 outline-none">
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
                No suggested process. This thread hasn&apos;t produced a draft.
              </p>
            )}
          </Panel>

          <Panel title="When we triaged">
            <p className="mb-3 text-xs text-muted-foreground">
              Historical classification from triage — not current open-work status.
            </p>
            {triage ? (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  {triage.is_internal ? (
                    <StatusBadge label="Internal" tone="blue" />
                  ) : null}
                  {triage.is_automated ? (
                    <StatusBadge label="Automated" tone="neutral" />
                  ) : null}
                  {triage.is_spam != null ? (
                    <StatusBadge
                      label={triage.is_spam ? "Spam" : "Not spam"}
                      tone={triage.is_spam ? "red" : "green"}
                    />
                  ) : null}
                  {(history?.has_action_items ?? triage.has_action_items) != null ? (
                    <StatusBadge
                      label={
                        (history?.has_action_items ?? triage.has_action_items)
                          ? "Action needed (at triage)"
                          : "No action (at triage)"
                      }
                      tone="neutral"
                    />
                  ) : null}
                  {(history?.needs_context ?? triage.needs_context) != null ? (
                    <StatusBadge
                      label={
                        (history?.needs_context ?? triage.needs_context)
                          ? "Needed context (at triage)"
                          : "Context OK (at triage)"
                      }
                      tone="neutral"
                    />
                  ) : null}
                </div>
                {triage.action_items_summary ? (
                  <Field label="Action items" value={triage.action_items_summary} />
                ) : null}
                {triage.spam_reason ? (
                  <Field label="Spam reason" value={triage.spam_reason} />
                ) : null}
                {thread.state === "SPAM" || triage.is_spam ? (
                  <NotSpamButton
                    threadId={threadId}
                    sender={thread.last_sender}
                    size="sm"
                  />
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
                  className={cn(textLinkClass, "text-sm")}
                >
                  Open in Outlook
                </a>
              ) : null}
            </div>
          </Panel>
        </TabsContent>

        <TabsContent value="draft" className="outline-none">
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
                {draft.approval_note ? (
                  <div className="space-y-1">
                    <Field label="Learning context" value={draft.approval_note} />
                    {draft.approval_scope ? (
                      <StatusBadge
                        label={
                          draft.approval_scope === "similar"
                            ? "Applies to similar emails"
                            : "This thread only"
                        }
                        tone="blue"
                      />
                    ) : null}
                  </div>
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
        </TabsContent>

        <TabsContent value="audit" className="outline-none">
          <Panel title="Audit log">
            <ul className="space-y-2">
              {auditLog.length === 0 ? (
                <li className="text-sm text-gray-500">No audit events.</li>
              ) : (
                auditLog.map((entry) => (
                  <li
                    key={`${entry.timestamp}|${entry.event}|${entry.source}|${entry.detail}`}
                    className="rounded-xl bg-muted/40 p-3 ring-1 ring-foreground/10"
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
          </Panel>
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
        key={siblingItems.map((item) => item.thread_id).join(",")}
        open={siblingOpen}
        sourceThreadId={threadId}
        items={siblingItems}
        treatment={siblingTreatment}
        reason={siblingReason}
        urgency={siblingUrgency}
        onOpenChange={setSiblingOpen}
      />
    </div>
  )
}
