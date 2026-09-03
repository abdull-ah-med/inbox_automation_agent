import { EmailBody } from "@/components/email-body"
import { PresentationBadges } from "@/components/presentation-badges"
import { StatusBadge, stateLabel, stateTone, urgencyTone } from "@/components/status-badge"
import { Field, Panel } from "@/components/thread-triage/panel"
import { UrgencyEditPopover } from "@/components/urgency-edit-popover"
import { Button } from "@/components/ui/button"
import { formatRelativeTime } from "@/lib/design-tokens"
import type { ActivityEntry, ThreadSummary } from "@/lib/types"

type InsightsPanelProps = {
  thread: ThreadSummary
  teachingNote: string | null
  urgency: string | null
  urgencyReason: string | null
  draftId: string | undefined
  feedbackDone: boolean
  busy: boolean
  activity: ActivityEntry[]
  resolvePending: boolean
  onMarkResolved: () => void
  onUrgencySaved: (payload: {
    reason: string
    urgency: "CRITICAL" | "HIGH" | "NORMAL" | "LOW"
  }) => void
}

type UrgencyFieldValueProps = {
  urgency: string | null
  urgencyReason: string | null
  draftId: string | undefined
  feedbackDone: boolean
  busy: boolean
  threadId: string
  presentation: ThreadSummary["presentation"]
  onUrgencySaved: InsightsPanelProps["onUrgencySaved"]
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
}: UrgencyFieldValueProps) => {
  if (!urgency) return "—"

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <StatusBadge
          label={urgency}
          tone={presentation && !presentation.urgency_active ? "neutral" : urgencyTone(urgency)}
        />
        {presentation && !presentation.urgency_active ? (
          <span className="text-muted-foreground text-xs">inactive</span>
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

export const InsightsPanel = ({
  thread,
  teachingNote,
  urgency,
  urgencyReason,
  draftId,
  feedbackDone,
  busy,
  activity,
  resolvePending,
  onMarkResolved,
  onUrgencySaved,
}: InsightsPanelProps) => {
  const presentation = thread.presentation
  const showResolve = Boolean(presentation?.open_work && !presentation?.is_finished)

  return (
    <Panel title="Insights">
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

        {activity.length > 0 ? (
          <div>
            <p className="text-muted-foreground text-xs">Activity</p>
            <ul className="mt-2 space-y-2">
              {activity
                .toReversed()
                .slice(0, 5)
                .map((entry) => (
                  <li
                    key={`${entry.event_type}-${entry.timestamp}`}
                    className="border-border/60 rounded-md border px-2.5 py-2"
                  >
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {entry.title}
                    </p>
                    <p className="text-muted-foreground mt-0.5 text-xs">{entry.body}</p>
                    <p className="text-muted-foreground mt-1 text-[11px]">
                      {formatRelativeTime(entry.timestamp)}
                    </p>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Panel>
  )
}
