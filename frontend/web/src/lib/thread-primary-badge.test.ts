import { describe, expect, it } from "vitest"

import { threadPrimaryBadge } from "@/lib/thread-primary-badge"
import type { ThreadSummary } from "@/lib/types"

const thread = (overrides: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id: "t1",
  mailbox: "sales@example.com",
  mailbox_key: "sales",
  subject: "Notice",
  state: "DRAFTED",
  urgency: "LOW",
  urgency_reason: null,
  category: null,
  last_message_at: "2026-09-03T12:00:00Z",
  last_sender: "sam@example.com",
  preview: "FYI",
  staleness_hours: 1,
  message_count: 1,
  has_draft: true,
  teaching_note: null,
  triage: null,
  outlook_url: null,
  ...overrides,
})

describe("threadPrimaryBadge", () => {
  it("uses presentation.primary_badge when present", () => {
    const badge = threadPrimaryBadge(
      thread({
        presentation: {
          is_finished: false,
          open_work: true,
          in_needs_attention: false,
          urgency_assessed: "LOW",
          urgency_active: true,
          badges_now: [{ kind: "disposition", label: "FYI" }],
          triage_history: {
            has_action_items: true,
            needs_context: false,
            is_spam: false,
          },
          suggest_resolve_default: false,
          show_resolution_banner: false,
          disposition: "fyi_briefing",
          primary_badge: { kind: "disposition", label: "FYI" },
        },
      }),
    )
    expect(badge).toEqual({ kind: "disposition", label: "FYI" })
  })

  it("never falls back to Drafted or Draft ready for DRAFTED threads", () => {
    const badge = threadPrimaryBadge(thread({ state: "DRAFTED", has_letter: false }))
    expect(badge.label.toLowerCase()).not.toContain("draft")
    expect(badge.label).toBe("FYI")
  })

  it("falls back to Reply ready when a DRAFTED thread has a letter", () => {
    const badge = threadPrimaryBadge(thread({ state: "DRAFTED", has_letter: true }))
    expect(badge.label).toBe("Reply ready")
  })
})
