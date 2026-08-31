import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
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

  it("does not search until typing pauses", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(screen.getByRole("searchbox", { name: /search mail/i }), "packet")
    expect(searchThreads).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(searchThreads).toHaveBeenCalledTimes(1)
    })
    expect(searchThreads).toHaveBeenCalledWith({ q: "packet" })
  })

  it("searches mail as keywords and lists matching threads", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(screen.getByRole("searchbox", { name: /search mail/i }), "packet")
    expect(await screen.findByRole("link", { name: /drug screen packet/i })).toHaveAttribute(
      "href",
      `/threads/${THREAD_ID}`,
    )
    expect(searchThreads).toHaveBeenCalledWith({ q: "packet" })
  })

  it("submits the keyword to the search page", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(screen.getByRole("searchbox", { name: /search mail/i }), "packet{Enter}")
    expect(push).toHaveBeenCalledWith("/search?q=packet")
  })

  it("strips HTML from the keyword before searching", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(screen.getByRole("searchbox", { name: /search mail/i }), "<b>packet</b>")
    expect(await screen.findByRole("link", { name: /drug screen packet/i })).toBeInTheDocument()
    expect(searchThreads).toHaveBeenCalledWith({ q: "packet" })
  })

  it("keeps stacked from: and subject: filters in the query", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(
      screen.getByRole("searchbox", { name: /search mail/i }),
      "from:vendor@example.com subject:packet",
    )
    await waitFor(() => {
      expect(searchThreads).toHaveBeenCalledWith({
        q: "from:vendor@example.com subject:packet",
      })
    })
  })

  it("offers stackable filter operators when search is focused", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.click(screen.getByRole("searchbox", { name: /search mail/i }))
    expect(screen.getByRole("option", { name: /^from:/i })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /^contains:/i })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /^subject:/i })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /^direction:/i })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /^mailbox:/i })).toBeInTheDocument()
  })

  it("inserts a filter operator from the suggestion list", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.click(screen.getByRole("searchbox", { name: /search mail/i }))
    await user.click(screen.getByRole("option", { name: /^from:/i }))
    expect(screen.getByRole("searchbox", { name: /search mail/i })).toHaveValue("from:")
  })

  it("does not search until from: has a sender value", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.click(screen.getByRole("searchbox", { name: /search mail/i }))
    await user.click(screen.getByRole("option", { name: /^from:/i }))
    expect(screen.getByText(/type a sender name or email/i)).toBeVisible()
    await new Promise((resolve) => {
      window.setTimeout(resolve, 400)
    })
    expect(searchThreads).not.toHaveBeenCalled()
  })

  it("searches once from: includes a sender", async () => {
    const user = userEvent.setup()
    renderSearch()
    await user.type(
      screen.getByRole("searchbox", { name: /search mail/i }),
      "from:vendor@example.com",
    )
    await waitFor(() => {
      expect(searchThreads).toHaveBeenCalledWith({ q: "from:vendor@example.com" })
    })
  })

  it("uses a pointer cursor on the search clear cross", () => {
    renderSearch()
    expect(screen.getByRole("searchbox", { name: /search mail/i }).className).toMatch(
      /search-cancel-button\]:cursor-pointer/,
    )
  })

  it("marks the keyboard-highlighted suggestion as aria-selected and sets activedescendant", async () => {
    // Bug this catches: listbox options never expose which row is active.
    const user = userEvent.setup()
    renderSearch()
    const input = screen.getByRole("searchbox", { name: /search mail/i })
    await user.click(input)
    const fromOption = screen.getByRole("option", { name: /^from:/i })
    expect(fromOption).toHaveAttribute("aria-selected", "false")

    await user.keyboard("{ArrowDown}")
    expect(fromOption).toHaveAttribute("aria-selected", "true")
    expect(fromOption).toHaveAttribute("id")
    expect(input).toHaveAttribute("aria-activedescendant", fromOption.id)

    await user.keyboard("{ArrowDown}")
    const containsOption = screen.getByRole("option", { name: /^contains:/i })
    expect(containsOption).toHaveAttribute("aria-selected", "true")
    expect(fromOption).toHaveAttribute("aria-selected", "false")
    expect(input).toHaveAttribute("aria-activedescendant", containsOption.id)
  })
})
