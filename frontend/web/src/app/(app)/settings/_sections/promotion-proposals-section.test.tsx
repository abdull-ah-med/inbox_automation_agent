import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { PromotionProposalsSection } from "@/app/(app)/settings/_sections/promotion-proposals-section"

const listMock = vi.fn()
const acceptMock = vi.fn()
const mailboxesMock = vi.fn()

vi.mock("@/features/auth/use-auth", () => ({
  useAuthState: () => ({
    user: { email: "admin@example.com", role: "admin" },
  }),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxes: { list: (...args: unknown[]) => mailboxesMock(...args) },
    promotionProposals: {
      list: (...args: unknown[]) => listMock(...args),
      accept: (...args: unknown[]) => acceptMock(...args),
      dismiss: vi.fn(),
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
      <PromotionProposalsSection mailbox="sales@example.com" mailboxesLoading={false} />
    </QueryClientProvider>,
  )
}

describe("PromotionProposalsSection", () => {
  beforeEach(() => {
    listMock.mockReset()
    acceptMock.mockReset()
    mailboxesMock.mockReset()
    mailboxesMock.mockResolvedValue([mailbox])
  })

  it("accepts a pending proposal by id", async () => {
    const user = userEvent.setup()
    listMock.mockResolvedValue([
      {
        id: "prop-1",
        mailbox: "sales@example.com",
        kind: "urgency_rule",
        payload: { description: "statuspage.io HIGH → LOW" },
        impact_num: 4,
        impact_den: 4,
        precision_num: null,
        precision_den: null,
        evidence_ids: [],
        status: "pending",
        expires_at: "2026-10-01T00:00:00Z",
        created_at: null,
      },
    ])
    acceptMock.mockResolvedValue({ id: "prop-1", status: "accepted" })
    renderSection()
    expect(await screen.findByText("Promotion proposals")).toBeInTheDocument()
    await user.click(await screen.findByRole("button", { name: "Accept proposal urgency_rule" }))
    await waitFor(() => {
      expect(acceptMock).toHaveBeenCalledWith("prop-1")
    })
  })
})
