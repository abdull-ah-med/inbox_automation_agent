import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { UrgencyRulesSection } from "@/app/(app)/settings/_sections/urgency-rules-section"

const listMock = vi.fn()
const pauseMock = vi.fn()
const mailboxesMock = vi.fn()

vi.mock("@/features/auth/use-auth", () => ({
  useAuthState: () => ({
    user: { email: "admin@example.com", role: "admin" },
  }),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxes: { list: (...args: unknown[]) => mailboxesMock(...args) },
    urgencyRules: {
      list: (...args: unknown[]) => listMock(...args),
      pause: (...args: unknown[]) => pauseMock(...args),
      resume: vi.fn(),
      archive: vi.fn(),
    },
  },
}))

const mailbox = {
  mailbox: "sales@example.com",
  email_address: "sales@example.com",
  label: "Sales",
  thread_count: 0,
  unread_count: 0,
  awaiting_action_count: 0,
  filtered_count: 0,
  stale_count: 0,
  urgency_breakdown: {},
  recent_threads: [],
}

const renderSection = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <UrgencyRulesSection />
    </QueryClientProvider>,
  )
}

describe("UrgencyRulesSection", () => {
  beforeEach(() => {
    listMock.mockReset()
    pauseMock.mockReset()
    mailboxesMock.mockReset()
    mailboxesMock.mockResolvedValue([mailbox])
  })

  it("pauses a live rule by id", async () => {
    const user = userEvent.setup()
    listMock.mockResolvedValue([
      {
        id: "rule-1",
        mailbox: "sales@example.com",
        scope: "sender_domain",
        scope_key: "domain:statuspage.io",
        condition: {},
        action: {},
        status: "canary",
        canary_until: null,
        activated_at: null,
        paused_at: null,
        impact_num: 4,
        impact_den: 4,
        precision_num: null,
        precision_den: null,
        hit_count: 2,
        override_count: 0,
        person_bound: false,
        previous_status: null,
        created_at: null,
        updated_at: null,
      },
    ])
    pauseMock.mockResolvedValue({ id: "rule-1", status: "paused" })
    renderSection()
    expect(await screen.findByText("Urgency rules")).toBeInTheDocument()
    await user.click(
      await screen.findByRole("button", { name: "Pause urgency rule domain:statuspage.io" }),
    )
    await waitFor(() => {
      expect(pauseMock).toHaveBeenCalledWith("rule-1")
    })
  })
})
