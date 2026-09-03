import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const replace = vi.fn()
const overview = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    reports: {
      downloadWeekly: vi.fn(),
    },
    dashboard: {
      overview: (...args: unknown[]) => overview(...args),
    },
  },
}))

import DashboardPage from "@/app/(app)/dashboard/page"

const overviewPayload = {
  mailboxes: [
    {
      mailbox: "inquiries",
      email_address: "inquiries@example.com",
      label: "Inquiries",
      thread_count: 2,
      unread_count: 0,
      awaiting_action_count: 1,
      filtered_count: 0,
      stale_count: 0,
      urgency_breakdown: { HIGH: 1 },
      recent_threads: [
        {
          id: "t1",
          mailbox: "inquiries@example.com",
          mailbox_key: "inquiries",
          subject: "Quote request",
          state: "NEW",
          urgency: "HIGH",
          category: "CLIENT",
          last_message_at: new Date().toISOString(),
          last_sender: "alice@client.com",
          preview: "Can you send pricing?",
          staleness_hours: 1,
          message_count: 2,
          has_draft: true,
          teaching_note: null,
          outlook_url: null,
          triage: {
            is_spam: false,
            has_action_items: true,
            needs_context: false,
            spam_reason: null,
            context_reason: null,
            action_items_summary: "Send pricing",
            outcome: "triage.action_needed",
          },
        },
      ],
    },
    {
      mailbox: "support",
      email_address: "support@example.com",
      label: "Support",
      thread_count: 1,
      unread_count: 0,
      awaiting_action_count: 0,
      filtered_count: 0,
      stale_count: 0,
      urgency_breakdown: {},
      recent_threads: [],
    },
  ],
  total_threads: 3,
  total_awaiting: 1,
  total_stale: 0,
  needs_attention: [
    {
      id: "t1",
      mailbox: "inquiries@example.com",
      mailbox_key: "inquiries",
      subject: "Quote request",
      state: "NEW",
      urgency: "HIGH",
      category: "CLIENT",
      last_message_at: new Date().toISOString(),
      last_sender: "alice@client.com",
      preview: "Can you send pricing?",
      staleness_hours: 1,
      message_count: 2,
      has_draft: true,
      teaching_note: null,
      outlook_url: null,
      triage: {
        is_spam: false,
        has_action_items: true,
        needs_context: true,
        spam_reason: null,
        context_reason: "Prior quote thread",
        action_items_summary: "Send pricing",
        outcome: "triage.action_needed",
      },
      presentation: {
        is_finished: false,
        open_work: true,
        in_needs_attention: true,
        urgency_active: true,
        urgency_assessed: "HIGH",
        suggest_resolve_default: false,
        show_resolution_banner: false,
        disposition: "processing",
        primary_badge: { kind: "disposition", label: "Processing" },
        badges_now: [
          { kind: "disposition", label: "Processing" },
          { kind: "urgency", label: "HIGH" },
          { kind: "needs_context", label: "Needs context" },
          { kind: "category", label: "CLIENT" },
        ],
        triage_history: {
          has_action_items: true,
          needs_context: true,
          is_spam: false,
        },
      },
    },
  ],
  recent_activity: [],
  updated_at: new Date().toISOString(),
}

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DashboardPage />
    </QueryClientProvider>,
  )
}

describe("dashboard page", () => {
  beforeEach(() => {
    replace.mockReset()
    overview.mockReset()
    overview.mockResolvedValue(overviewPayload)
  })

  it("renders mailbox cards and attention queue with triage signals", async () => {
    renderPage()
    expect(await screen.findByText(/1 awaiting action · 0 stale/i)).toBeInTheDocument()
    expect(screen.getAllByText("Inquiries").length).toBeGreaterThan(0)
    expect(screen.getByText("Needs attention")).toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Sort needs attention queue" })).toBeInTheDocument()
    expect(screen.getAllByText("Quote request").length).toBeGreaterThan(0)
    expect(screen.getByText("Needs context")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Download reports" })).toBeInTheDocument()
    expect(screen.queryByText("Open FYI")).toBeNull()
    expect(screen.queryByText("Recently resolved by DraftAssistant")).toBeNull()
  })

  it("shows the sort label in the trigger, not the raw value", async () => {
    renderPage()
    const sort = await screen.findByRole("combobox", { name: "Sort needs attention queue" })
    expect(sort).toHaveTextContent("Highest urgency")
    expect(sort).not.toHaveTextContent(/^urgency$/i)
  })

  it("keeps mailbox cards visible while the attention sort refetch runs", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    let finishRecent: ((value: typeof overviewPayload) => void) | undefined
    overview
      .mockImplementationOnce(async () => overviewPayload)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishRecent = resolve
          }),
      )

    renderPage()
    expect(await screen.findByText(/1 awaiting action · 0 stale/i)).toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: "Sort needs attention queue" }))
    await user.click(await screen.findByRole("option", { name: "Most recent" }))

    expect(screen.getByText(/1 awaiting action · 0 stale/i)).toBeInTheDocument()
    expect(screen.getAllByText("Inquiries").length).toBeGreaterThan(0)
    expect(replace).not.toHaveBeenCalled()

    finishRecent?.(overviewPayload)
    await waitFor(() => {
      expect(overview).toHaveBeenCalledWith("recent")
    })
  })
})
