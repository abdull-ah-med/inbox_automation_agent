import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { RejectionMemorySection } from "@/app/(app)/settings/_sections/rejection-memory-section"

const listMock = vi.fn()
const setExcludedMock = vi.fn()

vi.mock("@/features/auth/use-auth", () => ({
  useAuthState: () => ({
    user: { email: "admin@example.com", role: "admin" },
  }),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    rejectionMemory: {
      list: (...args: unknown[]) => listMock(...args),
      setExcluded: (...args: unknown[]) => setExcludedMock(...args),
    },
  },
}))

const renderSection = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <RejectionMemorySection />
    </QueryClientProvider>,
  )
}

const sample = {
  id: "rej-1",
  draft_id: "draft-1",
  thread_id: "thread-1",
  mailbox: "elise@example.com",
  routing_category: "billing",
  reason_code: "factual",
  note: "Do not promise same-day turnaround.",
  reason_text: "Do not promise same-day turnaround.",
  is_excluded: false,
  created_at: "2026-08-20T12:00:00Z",
  draft_subject: "Re: Invoice",
  draft_body: "Hi Beau,\n\nWe can do that today.",
  draft_body_preview: "Hi Beau,\n\nWe can do that today.",
  preview_line: "Hi Beau, We can do that today.",
  sender_email: "beau@example.com",
  receiver_email: "elise@example.com",
}

describe("RejectionMemorySection", () => {
  beforeEach(() => {
    listMock.mockReset()
    setExcludedMock.mockReset()
  })

  it("renders empty state when there are no rejects", async () => {
    listMock.mockResolvedValue([])
    renderSection()
    expect(await screen.findByText(/No rejected drafts yet/i)).toBeInTheDocument()
  })

  it("renders title, parties, reason, and one-line preview", async () => {
    listMock.mockResolvedValue([sample])
    renderSection()
    expect(await screen.findByText("Re: Invoice")).toBeInTheDocument()
    expect(screen.getByText(/From beau@example.com/)).toBeInTheDocument()
    expect(screen.getByText(/To elise@example.com/)).toBeInTheDocument()
    expect(
      screen.getByText("Reason: Factual error — Do not promise same-day turnaround."),
    ).toBeInTheDocument()
    expect(screen.getByText("Hi Beau, We can do that today.")).toBeInTheDocument()
  })

  it("opens the full draft when the preview is clicked", async () => {
    const user = userEvent.setup()
    listMock.mockResolvedValue([sample])
    renderSection()
    await user.click(await screen.findByRole("button", { name: "Open draft: Re: Invoice" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByText(/We can do that today/)).toBeInTheDocument()
  })

  it("excludes a rejection when Exclude is clicked", async () => {
    const user = userEvent.setup()
    listMock.mockResolvedValue([sample])
    setExcludedMock.mockResolvedValue({ ...sample, is_excluded: true })
    renderSection()
    await screen.findByText("Re: Invoice")
    await user.click(
      screen.getByRole("button", { name: "Exclude this rejection from draft constraints" }),
    )
    await waitFor(() => {
      expect(setExcludedMock).toHaveBeenCalledWith("rej-1", true)
    })
  })
})
