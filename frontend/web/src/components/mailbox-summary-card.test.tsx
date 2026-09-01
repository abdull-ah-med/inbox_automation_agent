import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

const listThreads = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxes: {
      threads: (...args: unknown[]) => listThreads(...args),
    },
  },
}))

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
const stalePreview = thread("Stale invoice follow-up", { id: "stale-1" })
const filteredPreview = thread("Discount blast", { id: "filtered-1", state: "SPAM" })
const totalPreview = thread("Newest ingested thread", { id: "total-1" })

const renderCard = (overview: MailboxOverview = mailbox({ recent_threads: [awaitingPreview] })) =>
  renderWithProviders(<MailboxSummaryCard mailbox={overview} />)

describe("MailboxSummaryCard", () => {
  beforeEach(() => {
    listThreads.mockReset()
  })

  it("highlights awaiting action as the default preview filter", () => {
    renderCard()

    expect(screen.getByRole("button", { name: "View 26 awaiting action in Info" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    expect(screen.getByText("awaiting action")).toHaveClass("text-foreground")
    expect(screen.getByText("awaiting action")).not.toHaveClass("text-muted-foreground")
    expect(screen.getByRole("button", { name: "View all 98 threads in Info" })).toHaveAttribute(
      "aria-pressed",
      "false",
    )
  })

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

  it("swaps the card preview to stale threads without leaving the dashboard", async () => {
    // Bug this catches: count clicks navigate to the mailbox page instead of
    // replacing the three-thread preview on this card.
    listThreads.mockImplementation(async (_key: string, params: { state?: string } = {}) => {
      if (params.state === "STALE") return { items: [stalePreview], next_cursor: null }
      return { items: [totalPreview], next_cursor: null }
    })
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole("button", { name: "View 22 stale threads in Info" }))

    expect(await screen.findByText("Stale invoice follow-up")).toBeInTheDocument()
    expect(screen.queryByText("Applicant Brittany Edwards")).toBeNull()
    expect(screen.getByRole("link", { name: "Review Stale invoice follow-up" })).toHaveAttribute(
      "href",
      "/threads/stale-1",
    )
    expect(screen.getByRole("link", { name: "Open Info inbox, stale threads" })).toHaveAttribute(
      "href",
      "/mailboxes/info?state=STALE",
    )
  })

  it("does not claim the stale queue is empty when the header count is 22", async () => {
    listThreads.mockResolvedValue({ items: [], next_cursor: null })
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole("button", { name: "View 22 stale threads in Info" }))

    expect(screen.queryByText("No stale threads right now.")).toBeNull()
    expect(
      await screen.findByRole("link", { name: "Open Info inbox, stale threads" }),
    ).toHaveAttribute("href", "/mailboxes/info?state=STALE")
  })

  it("swaps the card preview to all threads and spam/no-action from those counts", async () => {
    listThreads.mockImplementation(async (_key: string, params: { state?: string } = {}) => {
      if (params.state === "FILTERED") return { items: [filteredPreview], next_cursor: null }
      if (!params.state) return { items: [totalPreview], next_cursor: null }
      return { items: [], next_cursor: null }
    })
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole("button", { name: "View all 98 threads in Info" }))
    expect(await screen.findByText("Newest ingested thread")).toBeInTheDocument()

    await user.click(
      screen.getByRole("button", { name: "View 37 filtered as spam/no action in Info" }),
    )
    expect(await screen.findByText("Discount blast")).toBeInTheDocument()
    expect(screen.queryByText("Newest ingested thread")).toBeNull()
  })

  it("restores awaiting-action previews when that count is clicked again", async () => {
    listThreads.mockResolvedValue({ items: [stalePreview], next_cursor: null })
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole("button", { name: "View 22 stale threads in Info" }))
    expect(await screen.findByText("Stale invoice follow-up")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "View 26 awaiting action in Info" }))
    expect(screen.getByText("Applicant Brittany Edwards")).toBeInTheDocument()
    expect(screen.queryByText("Stale invoice follow-up")).toBeNull()
  })

  it("keeps another mailbox card on awaiting-action when this card filters to stale", async () => {
    listThreads.mockImplementation(async (key: string, params: { state?: string } = {}) => {
      if (key === "info" && params.state === "STALE") {
        return { items: [stalePreview], next_cursor: null }
      }
      return { items: [], next_cursor: null }
    })
    const user = userEvent.setup()
    renderWithProviders(
      <>
        <MailboxSummaryCard mailbox={mailbox({ recent_threads: [awaitingPreview] })} />
        <MailboxSummaryCard
          mailbox={mailbox({
            mailbox: "sales",
            label: "Sales",
            recent_threads: [thread("Quote request", { id: "sales-1" })],
          })}
        />
      </>,
    )

    await user.click(screen.getByRole("button", { name: "View 22 stale threads in Info" }))
    expect(await screen.findByText("Stale invoice follow-up")).toBeInTheDocument()
    expect(screen.getByText("Quote request")).toBeInTheDocument()
    expect(screen.queryByText("Applicant Brittany Edwards")).toBeNull()
  })

  it("omits stale and spam/no-action filters when those counts are zero", () => {
    renderCard(
      mailbox({
        stale_count: 0,
        filtered_count: 0,
        recent_threads: [awaitingPreview],
      }),
    )

    expect(screen.queryByRole("button", { name: /stale threads/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /filtered as spam/i })).toBeNull()
    expect(screen.getByRole("button", { name: "View all 98 threads in Info" })).toBeInTheDocument()
  })
})
