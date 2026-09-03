import { describe, expect, it } from "vitest"

import { mailboxPreviewSignals } from "@/lib/mailbox-preview-signals"
import type { ThreadSummary } from "@/lib/types"

const thread = (overrides: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id: "preview-1",
  mailbox: "elise@sample-site.example.com",
  mailbox_key: "elise",
  subject: "Your domain is now authenticated",
  state: "DRAFTED",
  urgency: "NORMAL",
  urgency_reason: null,
  category: null,
  last_message_at: "2026-09-03T12:00:00Z",
  last_sender: "noreply@example.com",
  preview: "Domain auth",
  staleness_hours: 1,
  message_count: 2,
  has_draft: true,
  teaching_note: "Informational confirmation",
  triage: {
    is_spam: false,
    has_action_items: true,
    needs_context: true,
    spam_reason: null,
    context_reason: "Prior thread",
    action_items_summary: "Review",
    outcome: "triage.action_needed",
    is_internal: true,
    is_automated: true,
  },
  outlook_url: null,
  ...overrides,
})

describe("mailboxPreviewSignals", () => {
  it("keeps only the primary disposition when urgency is NORMAL", () => {
    const signals = mailboxPreviewSignals(
      thread({
        presentation: {
          is_finished: false,
          open_work: true,
          in_needs_attention: true,
          urgency_active: true,
          urgency_assessed: "NORMAL",
          suggest_resolve_default: false,
          show_resolution_banner: false,
          disposition: "reply_review",
          primary_badge: { kind: "disposition", label: "Reply ready" },
          badges_now: [
            { kind: "disposition", label: "Reply ready" },
            { kind: "urgency", label: "NORMAL" },
            { kind: "needs_context", label: "Needs context" },
            { kind: "internal", label: "Internal" },
          ],
          triage_history: {
            has_action_items: true,
            needs_context: true,
            is_spam: false,
          },
        },
      }),
    )

    expect(signals).toEqual([{ kind: "disposition", label: "Reply ready" }])
  })

  it("adds live CRITICAL as the only extra signal", () => {
    const signals = mailboxPreviewSignals(
      thread({
        urgency: "CRITICAL",
        presentation: {
          is_finished: false,
          open_work: true,
          in_needs_attention: true,
          urgency_active: true,
          urgency_assessed: "CRITICAL",
          suggest_resolve_default: false,
          show_resolution_banner: false,
          disposition: "action_no_draft",
          primary_badge: { kind: "disposition", label: "Action needed" },
          badges_now: [
            { kind: "disposition", label: "Action needed" },
            { kind: "urgency", label: "CRITICAL" },
            { kind: "needs_context", label: "Needs context" },
          ],
          triage_history: {
            has_action_items: true,
            needs_context: true,
            is_spam: false,
          },
        },
      }),
    )

    expect(signals).toEqual([
      { kind: "disposition", label: "Action needed" },
      { kind: "urgency", label: "CRITICAL" },
    ])
  })

  it("omits stored CRITICAL when urgency is inactive", () => {
    const signals = mailboxPreviewSignals(
      thread({
        urgency: "CRITICAL",
        presentation: {
          is_finished: false,
          open_work: true,
          in_needs_attention: false,
          urgency_active: false,
          urgency_assessed: "CRITICAL",
          suggest_resolve_default: false,
          show_resolution_banner: false,
          disposition: "fyi_briefing",
          primary_badge: { kind: "disposition", label: "FYI" },
          badges_now: [{ kind: "disposition", label: "FYI" }],
          triage_history: {
            has_action_items: false,
            needs_context: false,
            is_spam: false,
          },
        },
      }),
    )

    expect(signals).toEqual([{ kind: "disposition", label: "FYI" }])
  })
})
