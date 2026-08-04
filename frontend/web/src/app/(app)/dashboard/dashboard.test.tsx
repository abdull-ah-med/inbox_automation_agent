import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    dashboard: {
      overview: vi.fn().mockResolvedValue({
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
          },
        ],
        recent_activity: [],
        updated_at: new Date().toISOString(),
      }),
    },
  },
}))

import DashboardPage from "@/app/(app)/dashboard/page"

describe("dashboard page", () => {
  it("renders mailbox cards and attention queue with triage signals", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <DashboardPage />
      </QueryClientProvider>,
    )
    expect(
      await screen.findByText(/1 awaiting action · 0 stale/i),
    ).toBeInTheDocument()
    expect(screen.getAllByText("Inquiries").length).toBeGreaterThan(0)
    expect(screen.getByText("Needs attention")).toBeInTheDocument()
    expect(screen.getAllByText("Quote request").length).toBeGreaterThan(0)
    expect(screen.getByText("Needs context")).toBeInTheDocument()
  })
})
