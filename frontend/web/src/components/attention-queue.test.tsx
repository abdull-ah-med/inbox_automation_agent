import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { AttentionQueue } from "@/components/attention-queue"
import type { ThreadSummary } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

const thread = (overrides: Partial<ThreadSummary> = {}): ThreadSummary => ({
  id: "e45649af-85ab-470e-8043-8d3c32faddce",
  mailbox: "sampleagent@sample-site.example.com",
  mailbox_key: "elise",
  subject: "Your domain is now authenticated",
  state: "DRAFTED",
  urgency: "CRITICAL",
  urgency_reason: "Urgency bumped automatically: 3 similar alerts in 48h",
  category: null,
  last_message_at: "2026-09-03T12:00:00Z",
  last_sender: "noreply@example.com",
  preview: "This is an automated confirmation email",
  staleness_hours: 1,
  message_count: 3,
  has_draft: true,
  teaching_note: null,
  triage: {
    is_spam: false,
    has_action_items: false,
    needs_context: false,
    spam_reason: null,
    context_reason: null,
    action_items_summary: null,
    outcome: "triage.action_needed",
    is_automated: true,
  },
  outlook_url: null,
  ...overrides,
})

describe("AttentionQueue urgency Now view", () => {
  it("shows CRITICAL from presentation Now badges when urgency is live", () => {
    renderWithProviders(
      <AttentionQueue
        threads={[
          thread({
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
                { kind: "automated_action", label: "Automated · action needed" },
              ],
              triage_history: {
                has_action_items: false,
                needs_context: false,
                is_spam: false,
              },
            },
          }),
        ]}
      />,
    )

    expect(screen.getByText("Action needed")).toBeInTheDocument()
    expect(screen.getByText("CRITICAL")).toBeInTheDocument()
    expect(screen.getByText("Automated · action needed")).toBeInTheDocument()
  })

  it("does not paint stored CRITICAL when urgency is inactive", () => {
    renderWithProviders(
      <AttentionQueue
        threads={[
          thread({
            presentation: {
              is_finished: true,
              open_work: false,
              in_needs_attention: false,
              urgency_active: false,
              urgency_assessed: "CRITICAL",
              suggest_resolve_default: false,
              show_resolution_banner: true,
              disposition: "resolved_draftassistant",
              primary_badge: { kind: "disposition", label: "Resolved by DraftAssistant" },
              badges_now: [{ kind: "disposition", label: "Resolved by DraftAssistant" }],
              triage_history: {
                has_action_items: false,
                needs_context: false,
                is_spam: false,
              },
            },
          }),
        ]}
      />,
    )

    expect(screen.getByText("Resolved by DraftAssistant")).toBeInTheDocument()
    expect(screen.queryByText("CRITICAL")).toBeNull()
  })
})
