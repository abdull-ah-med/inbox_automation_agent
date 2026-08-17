import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const searchThreads = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/search",
  useSearchParams: () => new URLSearchParams("q=packet"),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    search: {
      threads: (...args: unknown[]) => searchThreads(...args),
    },
  },
}))

import SearchPage from "@/app/(app)/search/page"

const THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

describe("Search page", () => {
  beforeEach(() => {
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

  it("lists keyword matches for the query in the URL", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <SearchPage />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole("heading", { name: /search/i })).toBeInTheDocument()
    expect(
      await screen.findByRole("link", { name: /drug screen packet/i }),
    ).toHaveAttribute("href", `/threads/${THREAD_ID}`)
    expect(searchThreads).toHaveBeenCalledWith({ q: "packet" })
  })
})
