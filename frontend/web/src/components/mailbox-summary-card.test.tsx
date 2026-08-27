import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { MailboxSummaryCard } from "@/components/mailbox-summary-card"
import type { MailboxOverview, ThreadSummary } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

const thread = (subject: string): ThreadSummary => ({
  id: "e25cc63b-9db9-4c2d-af45-e598a639eaec",
  mailbox: "info@sample-services.example.com",
  mailbox_key: "info",
  subject,
  state: "DRAFTED",
  urgency: "NORMAL",
  urgency_reason: null,
  category: null,
  last_message_at: "2026-08-25T12:00:00Z",
  last_sender: "helpdesk@sample-helpdesk.example.com",
  preview: "Ticket updated",
  staleness_hours: 1,
  message_count: 14,
  has_draft: true,
  teaching_note: null,
  triage: {
    is_spam: false,
    has_action_items: true,
    needs_context: false,
    spam_reason: null,
    context_reason: null,
    action_items_summary: "Reply to ticket",
    outcome: "triage.action_needed",
  },
  outlook_url: null,
})

const mailbox = (overrides: Partial<MailboxOverview>): MailboxOverview => ({
  mailbox: "info",
  email_address: "info@sample-services.example.com",
  label: "Info",
  thread_count: 98,
  unread_count: 0,
  awaiting_action_count: 26,
  filtered_count: 37,
  stale_count: 22,
  urgency_breakdown: { NORMAL: 26 },
  recent_threads: [],
  ...overrides,
})

describe("MailboxSummaryCard", () => {
  it("shows awaiting-action previews instead of an empty queue message", () => {
    renderWithProviders(
      <MailboxSummaryCard
        mailbox={mailbox({
          recent_threads: [thread("Applicant Brittany Edwards")],
        })}
      />,
    )

    expect(screen.getByText("26")).toBeInTheDocument()
    expect(screen.getByText("Applicant Brittany Edwards")).toBeInTheDocument()
    expect(screen.queryByText("No threads awaiting action right now.")).toBeNull()
  })

  it("shows empty-queue copy when ingested threads are not awaiting action", () => {
    renderWithProviders(
      <MailboxSummaryCard
        mailbox={mailbox({
          awaiting_action_count: 0,
          stale_count: 0,
          recent_threads: [],
        })}
      />,
    )

    expect(screen.getByText("No threads awaiting action right now.")).toBeInTheDocument()
  })
})
