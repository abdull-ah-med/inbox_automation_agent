import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { InsightsPanel } from "@/components/thread-triage/insights-panel"
import type { ThreadSummary } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

const baseThread: ThreadSummary = {
  id: "thread-1",
  subject: "Daily Drivers changes update",
  mailbox: "support@example.com",
  mailbox_key: "support",
  state: "DRAFTED",
  urgency: "NORMAL",
  urgency_reason: "Routine notice",
  category: "support",
  last_message_at: "2026-08-26T12:00:00Z",
  last_sender: "accounting@example.com",
  preview: "Pending driver changes",
  staleness_hours: 2,
  message_count: 1,
  has_draft: true,
  teaching_note: null,
  triage: null,
  outlook_url: null,
  presentation: {
    is_finished: false,
    open_work: true,
    in_needs_attention: true,
    urgency_active: true,
    urgency_assessed: "NORMAL",
    suggest_resolve_default: false,
    show_resolution_banner: false,
    badges_now: [],
    triage_history: {
      has_action_items: true,
      needs_context: false,
      is_spam: false,
      spam_reason: null,
      context_reason: null,
    },
  },
}

const renderPanel = (thread: ThreadSummary, onMarkResolved = vi.fn()) => {
  renderWithProviders(
    <InsightsPanel
      thread={thread}
      teachingNote={null}
      urgency="NORMAL"
      urgencyReason="Routine notice"
      draftId="draft-1"
      feedbackDone={false}
      busy={false}
      activity={[]}
      resolvePending={false}
      onMarkResolved={onMarkResolved}
      onUrgencySaved={() => undefined}
    />,
  )
  return { onMarkResolved }
}

describe("InsightsPanel resolve control", () => {
  it("shows Mark resolved when thread has open work and is not finished", () => {
    renderPanel(baseThread)
    expect(screen.getByRole("button", { name: "Mark thread resolved" })).toBeInTheDocument()
  })

  it("hides Mark resolved when thread is finished", () => {
    renderPanel({
      ...baseThread,
      state: "RESOLVED",
      presentation: {
        ...baseThread.presentation!,
        is_finished: true,
        open_work: false,
      },
    })
    expect(screen.queryByRole("button", { name: "Mark thread resolved" })).not.toBeInTheDocument()
  })

  it("opens resolve flow when Mark resolved is clicked", async () => {
    const user = userEvent.setup()
    const { onMarkResolved } = renderPanel(baseThread)

    await user.click(screen.getByRole("button", { name: "Mark thread resolved" }))

    expect(onMarkResolved).toHaveBeenCalledTimes(1)
  })
})
