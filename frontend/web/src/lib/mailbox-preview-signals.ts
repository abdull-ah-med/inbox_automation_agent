import { threadPrimaryBadge } from "@/lib/thread-primary-badge"
import type { BadgeNow, ThreadSummary } from "@/lib/types"

const PRIORITY_URGENCY = new Set(["CRITICAL", "HIGH"])

export const mailboxPreviewSignals = (thread: ThreadSummary): BadgeNow[] => {
  const primary = threadPrimaryBadge(thread)
  const signals: BadgeNow[] = [primary]

  const presentation = thread.presentation
  if (presentation && !presentation.urgency_active) {
    return signals
  }

  const urgency = (presentation?.urgency_assessed ?? thread.urgency)?.toUpperCase() ?? null
  if (!urgency || !PRIORITY_URGENCY.has(urgency)) {
    return signals
  }
  if (signals.some((badge) => badge.kind === "urgency" && badge.label.toUpperCase() === urgency)) {
    return signals
  }

  signals.push({ kind: "urgency", label: urgency })
  return signals
}
