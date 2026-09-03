import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { MailboxSummaryCard } from "@/components/mailbox-summary-card"
import type { MailboxOverview, ThreadSummary } from "@/lib/types"
import { renderWithProviders } from "@/test/render"

const thread = (subject: string, overrides: Partial<ThreadSummary> = {}): ThreadSummary => ({
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
  ...overrides,
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

const awaitingPreview = thread("Applicant Brittany Edwards", { id: "awaiting-1" })

const renderCard = (overview: MailboxOverview = mailbox({ recent_threads: [awaitingPreview] })) =>
  renderWithProviders(<MailboxSummaryCard mailbox={overview} />)

describe("MailboxSummaryCard", () => {
  it("opens the mailbox from anywhere on the card that is not a filter or thread", () => {
    renderCard()

    expect(screen.getByRole("link", { name: "Open Info inbox" })).toHaveAttribute(
      "href",
      "/mailboxes/info?state=AWAITING_ACTION",
    )
    expect(screen.getByRole("link", { name: "Review Applicant Brittany Edwards" })).toHaveAttribute(
      "href",
      "/threads/awaiting-1",
    )
  })

  it("shows awaiting-action previews instead of an empty queue message", () => {
    renderCard()

    expect(screen.getByText("26")).toBeInTheDocument()
    expect(screen.getByText("Applicant Brittany Edwards")).toBeInTheDocument()
    expect(screen.queryByText("No threads awaiting action right now.")).toBeNull()
  })

  it("shows CRITICAL only from live Now badges, not raw stored urgency", () => {
    renderCard(
      mailbox({
        recent_threads: [
          thread("Your domain is now authenticated", {
            id: "critical-live",
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
                has_action_items: false,
                needs_context: false,
                is_spam: false,
              },
            },
          }),
        ],
      }),
    )

    expect(screen.getByText("Your domain is now authenticated")).toBeInTheDocument()
    expect(screen.getByText("Action needed")).toBeInTheDocument()
    expect(screen.getByText("CRITICAL")).toBeInTheDocument()
    expect(screen.queryByText("Needs context")).toBeNull()
  })

  it("leads each preview with the subject, not a badge stack", () => {
    renderCard(
      mailbox({
        recent_threads: [
          thread("Follow up an DOT and non DOT proposal", {
            id: "subject-first",
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
        ],
      }),
    )

    const link = screen.getByRole("link", { name: "Review Follow up an DOT and non DOT proposal" })
    expect(link.textContent).toMatch(/^Follow up an DOT and non DOT proposal/)
    expect(screen.getByText("Reply ready")).toBeInTheDocument()
    expect(screen.queryByText("NORMAL")).toBeNull()
    expect(screen.queryByText("Needs context")).toBeNull()
    expect(screen.queryByText("Internal")).toBeNull()
  })

  it("does not duplicate stale as a header chip when the stats row already shows it", () => {
    renderCard()
    expect(screen.getAllByRole("link", { name: "View 22 stale threads in Info" })).toHaveLength(1)
  })

  it("hides stored CRITICAL when presentation marks urgency inactive", () => {
    renderCard(
      mailbox({
        recent_threads: [
          thread("Your domain is now authenticated", {
            id: "critical-inactive",
            urgency: "CRITICAL",
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
        ],
      }),
    )

    expect(screen.getByText("Resolved by DraftAssistant")).toBeInTheDocument()
    expect(screen.queryByText("CRITICAL")).toBeNull()
  })

  it("shows empty-queue copy when ingested threads are not awaiting action", () => {
    renderCard(
      mailbox({
        awaiting_action_count: 0,
        stale_count: 0,
        recent_threads: [],
      }),
    )

    expect(screen.getByText("No threads awaiting action right now.")).toBeInTheDocument()
  })

  it("sends count options to the mailbox list with that filter applied", () => {
    // Bug this catches: stale / spam / total / awaiting stay on the card and
    // only swap the three-thread preview instead of opening the filtered list.
    renderCard()

    expect(screen.getByRole("link", { name: "View 26 awaiting action in Info" })).toHaveAttribute(
      "href",
      "/mailboxes/info?state=AWAITING_ACTION",
    )
    expect(screen.getByRole("link", { name: "View all 98 threads in Info" })).toHaveAttribute(
      "href",
      "/mailboxes/info",
    )
    expect(screen.getByRole("link", { name: "View 22 stale threads in Info" })).toHaveAttribute(
      "href",
      "/mailboxes/info?state=STALE",
    )
    expect(screen.getByRole("link", { name: "View 37 filtered as spam in Info" })).toHaveAttribute(
      "href",
      "/mailboxes/info?state=FILTERED",
    )
  })

  it("omits stale and spam links when those counts are zero", () => {
    renderCard(
      mailbox({
        stale_count: 0,
        filtered_count: 0,
        recent_threads: [awaitingPreview],
      }),
    )

    expect(screen.queryByRole("link", { name: /stale threads/i })).toBeNull()
    expect(screen.queryByRole("link", { name: /filtered as spam/i })).toBeNull()
    expect(screen.getByRole("link", { name: "View all 98 threads in Info" })).toBeInTheDocument()
  })
})
