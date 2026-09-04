"use client"

import { EmailBody } from "@/components/email-body"
import { PresentationBadges } from "@/components/presentation-badges"
import { StatusBadge, urgencyTone } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import { UrgencyEditPopover } from "@/components/urgency-edit-popover"
import { Button } from "@/components/ui/button"
import { threadPrimaryBadge } from "@/lib/thread-primary-badge"
import type { ThreadSummary } from "@/lib/types"

type InsightSectionProps = {
  thread: ThreadSummary
  teachingNote: string | null
  urgency: string | null
  urgencyReason: string | null
  draftId: string | undefined
  feedbackDone: boolean
  busy: boolean
  resolvePending: boolean
  onMarkResolved: () => void
  onUrgencySaved: (payload: {
    reason: string
    urgency: "CRITICAL" | "HIGH" | "NORMAL" | "LOW"
  }) => void
}

const UrgencyFieldValue = ({
  urgency,
  urgencyReason,
  draftId,
  feedbackDone,
  busy,
  threadId,
  presentation,
  onUrgencySaved,
}: {
  urgency: string | null
  urgencyReason: string | null
  draftId: string | undefined
  feedbackDone: boolean
  busy: boolean
  threadId: string
  presentation: ThreadSummary["presentation"]
  onUrgencySaved: InsightSectionProps["onUrgencySaved"]
}) => {
  if (!urgency) return "—"

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <StatusBadge
          label={urgency}
          tone={presentation && !presentation.urgency_active ? "neutral" : urgencyTone(urgency)}
        />
        {presentation && !presentation.urgency_active ? (
          <span className="text-muted-foreground text-xs">
            inactive — no longer drives priority
          </span>
        ) : null}
        {draftId && !feedbackDone && presentation?.urgency_active !== false ? (
          <UrgencyEditPopover
            draftId={draftId}
            threadId={threadId}
            currentUrgency={urgency}
            disabled={busy}
            onSaved={onUrgencySaved}
          />
        ) : null}
      </div>
      {urgencyReason ? <p className="text-xs text-gray-500">{urgencyReason}</p> : null}
    </div>
  )
}

export const InsightSection = ({
  thread,
  teachingNote,
  urgency,
  urgencyReason,
  draftId,
  feedbackDone,
  busy,
  resolvePending,
  onMarkResolved,
  onUrgencySaved,
}: InsightSectionProps) => {
  const presentation = thread.presentation
  const showResolve = Boolean(presentation?.open_work && !presentation?.is_finished)

  return (
    <Panel title="Insight">
      <div className="space-y-4">
        <div>
          <p className="text-muted-foreground text-xs">Teaching note</p>
          {teachingNote ? (
            <EmailBody
              text={teachingNote}
              className="mt-1 rounded-md bg-blue-50 px-3 py-2 text-gray-800 dark:bg-blue-950/30 dark:text-gray-100"
            />
          ) : (
            <p className="text-muted-foreground mt-1 text-sm italic">
              No teaching note yet. This thread hasn&apos;t produced a draft.
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field
            label="State"
            value={<PresentationBadges badges={[threadPrimaryBadge(thread)]} />}
          />
          <Field
            label="Urgency"
            value={
              <UrgencyFieldValue
                urgency={urgency}
                urgencyReason={urgencyReason}
                draftId={draftId}
                feedbackDone={feedbackDone}
                busy={busy}
                threadId={thread.id}
                presentation={presentation}
                onUrgencySaved={onUrgencySaved}
              />
            }
          />
        </div>

        {showResolve ? (
          <Button
            type="button"
            variant="outline"
            tabIndex={0}
            aria-label="Mark thread resolved"
            disabled={busy || resolvePending}
            onClick={onMarkResolved}
          >
            {resolvePending ? "Resolving…" : "Mark resolved"}
          </Button>
        ) : null}
      </div>
    </Panel>
  )
}
