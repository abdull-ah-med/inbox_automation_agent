import { Check, X } from "lucide-react"

import { EmailBody } from "@/components/email-body"
import { StatusBadge } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import { Button } from "@/components/ui/button"
import type { DraftView } from "@/lib/types"

type DraftSectionProps = {
  draft: DraftView | null
  badge: { label: string; tone: "green" | "blue" | "red" | "amber" } | null
  actionError: string | null
  feedbackDone: boolean
  busy: boolean
  onApprove: () => void
  onApproveKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
  onReject: () => void
  onRejectKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
}

export const DraftSection = ({
  draft,
  badge,
  actionError,
  feedbackDone,
  busy,
  onApprove,
  onApproveKeyDown,
  onReject,
  onRejectKeyDown,
}: DraftSectionProps) => {
  return (
    <Panel title="Draft reply">
      {draft ? (
        <div className="space-y-3">
          {badge ? <StatusBadge label={badge.label} tone={badge.tone} /> : null}
          <Field label="Subject" value={draft.subject} />
          <div>
            <p className="text-muted-foreground text-xs">Body</p>
            <div className="mt-1 overflow-auto">
              <EmailBody text={draft.body} />
            </div>
          </div>
          {draft.forward_to ? <Field label="Forward to" value={draft.forward_to} /> : null}
          {draft.feedback_note ? <Field label="Feedback note" value={draft.feedback_note} /> : null}
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
            <p className="text-muted-foreground text-xs">Skills used</p>
            {(draft.applied_skills?.length ?? 0) === 0 ? (
              <p className="mt-1 text-sm text-gray-500">No skills applied for this draft</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {draft.applied_skills.map((skill) => {
                  const refs = (draft.tool_calls ?? [])
                    .filter(
                      (call) => call.skill_id === skill.id && !call.is_error && Boolean(call.path),
                    )
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
              onClick={onApprove}
              onKeyDown={onApproveKeyDown}
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
              onClick={onReject}
              onKeyDown={onRejectKeyDown}
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
  )
}
