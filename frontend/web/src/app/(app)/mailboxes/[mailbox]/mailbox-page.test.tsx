import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const replace = vi.fn()
const listThreads = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/mailboxes/sales",
  useParams: () => ({ mailbox: "sales" }),
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    mailboxes: {
      threads: (...args: unknown[]) => listThreads(...args),
    },
  },
}))

import MailboxPage from "@/app/(app)/mailboxes/[mailbox]/page"

const renderPage = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MailboxPage />
    </QueryClientProvider>,
  )
}

describe("Mailbox page filters", () => {
  beforeEach(() => {
    replace.mockReset()
    listThreads.mockReset()
    listThreads.mockResolvedValue({ items: [], next_cursor: null })
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
  })

  it("renders state and urgency as outline button dropdowns", async () => {
    renderPage()
    const stateFilter = await screen.findByRole("combobox", {
      name: "Filter by state",
    })
    const urgencyFilter = screen.getByRole("combobox", {
      name: "Filter by urgency",
    })
    const stale = screen.getByRole("button", { name: "Stale only" })
    expect(stateFilter.tagName).toBe("BUTTON")
    expect(urgencyFilter.tagName).toBe("BUTTON")
    expect(stateFilter.className).toContain("border-border")
    expect(urgencyFilter.className).toContain("border-border")
    expect(stale.className).toContain("border-border")
    expect(stateFilter.className).toContain("bg-background")
    expect(stale.className).toContain("bg-background")
  })

  it("writes the chosen state onto the mailbox URL", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(
      await screen.findByRole("combobox", { name: "Filter by state" }),
    )
    await user.click(
      await screen.findByRole("option", { name: "Needs human review" }),
    )
    expect(replace).toHaveBeenCalledWith(
      "/mailboxes/sales?state=REQUIRES_HUMAN",
    )
  })
})
