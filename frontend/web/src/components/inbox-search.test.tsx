import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const push = vi.fn()
const searchThreads = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    search: {
      threads: (...args: unknown[]) => searchThreads(...args),
    },
  },
}))

import { InboxSearch } from "@/components/inbox-search"

const THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

const renderSearch = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <InboxSearch />
    </QueryClientProvider>,
  )
}

describe("InboxSearch", () => {
  beforeEach(() => {
    push.mockReset()
    searchThreads.mockReset()
    searchThreads.mockResolvedValue({
      query: "packet",
      mailbox: null,
      hits: [
        {
          thread_id: THREAD_ID,
          mailbox: "sales@example.com",
          conversation_id: "conv-sales-packet",
          subject: "Drug screen packet",
          state: "DRAFTED",
          urgency: "HIGH",
          snippet: "Please send the packet by Friday.",
          score: 0.02,
          last_message_at: "2026-08-05T15:00:00Z",
        },
      ],
    })
  })

  it("searches mail as keywords and lists matching threads", async () => {
    const user = userEvent.setup()
    renderSearch()
    const box = screen.getByRole("searchbox", { name: /search mail/i })
    await user.type(box, "packet")
    expect(
      await screen.findByRole("link", { name: /drug screen packet/i }),
    ).toHaveAttribute("href", `/threads/${THREAD_ID}`)
    expect(searchThreads).toHaveBeenCalledWith({ q: "packet" })
  })

  it("submits the keyword to the search page", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(
      screen.getByRole("searchbox", { name: /search mail/i }),
      "packet{Enter}",
    )
    expect(push).toHaveBeenCalledWith("/search?q=packet")
  })
})
