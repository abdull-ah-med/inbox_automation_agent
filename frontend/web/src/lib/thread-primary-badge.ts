import { stateLabel } from "@/components/status-badge"
import type { BadgeNow, ThreadSummary } from "@/lib/types"

export const threadPrimaryBadge = (thread: ThreadSummary): BadgeNow => {
  if (thread.presentation?.primary_badge?.label) {
    return thread.presentation.primary_badge
  }
  const first = thread.presentation?.badges_now?.[0]
  if (first?.kind === "disposition" && first.label) {
    return first
  }
  if (thread.state === "DRAFTED") {
    return {
      kind: "disposition",
      label: thread.has_letter ? "Reply ready" : "FYI",
    }
  }
  return { kind: "state", label: stateLabel(thread.state) }
}
