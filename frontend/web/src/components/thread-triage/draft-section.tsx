import { Check, X } from "lucide-react"

import { EmailBody } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import { SaluteChip } from "@/components/thread-triage/salute-chip"
import { Button } from "@/components/ui/button"
import type { DraftView, ReplyAddresseeView } from "@/lib/types"

type DraftSectionProps = {
  draft: DraftView | null
  badge: { label: string; tone: "green" | "blue" | "red" | "amber" } | null
  actionError: string | null
  feedbackDone: boolean
  busy: boolean
  generatePending?: boolean
  mailboxKey?: string
  replyAddressee?: ReplyAddresseeView | null
  onApprove: () => void
  onApproveKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onReject: () => void
  onRejectKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onGenerateDraft?: () => void
  onGenerateDraftKeyDown?: (event: React.KeyboardEvent<HTMLButtonElement>) => void
}

const DraftSkills = ({ draft }: { draft: DraftView }) => {
  if ((draft.applied_skills?.length ?? 0) === 0) {
    return <p className="mt-1 text-sm text-gray-500">No skills applied for this draft</p>
  }
  return (
    <ul className="mt-2 space-y-2">
      {draft.applied_skills.map((skill) => {
        const refs = (draft.tool_calls ?? [])
          .filter((call) => call.skill_id === skill.id && !call.is_error && Boolean(call.path))
          .map((call) => call.path)
        const uniqueRefs = [...new Set(refs)]
        return (
          <li key={skill.id} className="space-y-1">
            <StatusBadge label={skill.name} tone="blue" />
            {uniqueRefs.length > 0 ? (
              <ul className="dark:text-muted-foreground ml-1 list-disc space-y-0.5 pl-4 text-xs text-gray-600">
                {uniqueRefs.map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

const approvalScopeLabel = (scope: DraftView["approval_scope"]): string => {
  if (scope === "similar") return "Applies to similar emails"
  if (scope === "sender_address") return "Applies to this sender"
  if (scope === "mailbox") return "Applies to this mailbox"
  return "This thread only"
}

const DraftApprovalNote = ({ draft }: { draft: DraftView }) => {
  if (!draft.approval_note) return null
  return (
    <div className="space-y-1">
      <Field label="Learning context" value={draft.approval_note} />
      {draft.approval_scope ? (
        <StatusBadge label={approvalScopeLabel(draft.approval_scope)} tone="blue" />
      ) : null}
    </div>
  )
}

type DraftActionButtonsProps = {
  noEmailReply: boolean
  feedbackDone: boolean
  busy: boolean
  generatePending: boolean
  onGenerateDraft?: () => void
  onGenerateDraftKeyDown?: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onApprove: () => void
  onApproveKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onReject: () => void
  onRejectKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
}

const DraftActionButtons = ({
  noEmailReply,
  feedbackDone,
  busy,
  generatePending,
  onGenerateDraft,
  onGenerateDraftKeyDown,
  onApprove,
  onApproveKeyDown,
  onReject,
  onRejectKeyDown,
}: DraftActionButtonsProps) => (
  <div className="flex flex-wrap gap-2 pt-1">
    {noEmailReply && onGenerateDraft ? (
      <Button
        type="button"
        size="sm"
        variant="default"
        tabIndex={0}
        aria-label="Generate draft"
        disabled={feedbackDone || busy || generatePending}
        onClick={onGenerateDraft}
        onKeyDown={onGenerateDraftKeyDown}
      >
        {generatePending ? "Generating…" : "Generate draft"}
      </Button>
    ) : null}
    {!noEmailReply ? (
      <Button
        type="button"
        size="sm"
        variant="outline"
        tabIndex={0}
        aria-label="Approve draft"
        disabled={feedbackDone || busy}
        onClick={onApprove}
        onKeyDown={onApproveKeyDown}
      >
        <Check aria-hidden="true" />
        Approve
      </Button>
    ) : null}
    <Button
      type="button"
      size="sm"
      variant="outline"
      tabIndex={0}
      aria-label="Reject draft"
      disabled={feedbackDone || busy}
      onClick={onReject}
      onKeyDown={onRejectKeyDown}
    >
      <X aria-hidden="true" />
      Reject
    </Button>
  </div>
)

export const DraftSection = ({
  draft,
  badge,
  actionError,
  feedbackDone,
  busy,
  generatePending = false,
  mailboxKey,
  replyAddressee = null,
  onApprove,
  onApproveKeyDown,
  onReject,
  onRejectKeyDown,
  onGenerateDraft,
  onGenerateDraftKeyDown,
}: DraftSectionProps) => {
  if (!draft) {
    return (
      <Panel title="Draft reply">
        <p className="text-sm text-gray-500">No draft generated for this thread.</p>
      </Panel>
    )
  }

  if (generatePending && (draft.body || "").trim()) {
    return (
      <Panel title="Draft reply">
        <p className="text-sm text-gray-500" aria-live="polite">
          Regenerating draft…
        </p>
      </Panel>
    )
  }

  const noEmailReply = !(draft.body || "").trim()

  return (
    <Panel title="Draft reply">
      <div className="space-y-3">
        {badge ? <StatusBadge label={badge.label} tone={badge.tone} /> : null}

        {!noEmailReply && replyAddressee && mailboxKey && draft?.id ? (
          <SaluteChip mailboxKey={mailboxKey} draftId={draft.id} replyAddressee={replyAddressee} />
        ) : null}

        <Field label="Subject" value={draft.subject} />
        {noEmailReply ? (
          <p className="text-sm text-gray-700 dark:text-gray-200">No email reply needed</p>
        ) : (
          <div>
            <p className="text-muted-foreground text-xs">Body</p>
            <div className="mt-1 overflow-auto">
              <EmailBody text={draft.body} />
            </div>
          </div>
        )}
        {draft.forward_to ? <Field label="Forward to" value={draft.forward_to} /> : null}
        {draft.feedback_note ? <Field label="Feedback note" value={draft.feedback_note} /> : null}
        <DraftApprovalNote draft={draft} />

        <div>
          <p className="text-muted-foreground text-xs">Skills used</p>
          <DraftSkills draft={draft} />
        </div>

        {actionError ? (
          <p className="text-sm text-red-600 dark:text-red-400" role="alert">
            {actionError}
          </p>
        ) : null}

        <DraftActionButtons
          noEmailReply={noEmailReply}
          feedbackDone={feedbackDone}
          busy={busy}
          generatePending={generatePending}
          onGenerateDraft={onGenerateDraft}
          onGenerateDraftKeyDown={onGenerateDraftKeyDown}
          onApprove={onApprove}
          onApproveKeyDown={onApproveKeyDown}
          onReject={onReject}
          onRejectKeyDown={onRejectKeyDown}
        />
      </div>
    </Panel>
  )
}
