import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const replace = vi.fn()
const listThreads = vi.fn()
const searchParamsString = vi.fn(() => "")

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/mailboxes/sales",
  useParams: () => ({ mailbox: "sales" }),
  useSearchParams: () => new URLSearchParams(searchParamsString()),
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
    searchParamsString.mockReturnValue("")
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
    expect(stateFilter.tagName).toBe("BUTTON")
    expect(urgencyFilter.tagName).toBe("BUTTON")
    expect(stateFilter.className).toContain("border-border")
    expect(urgencyFilter.className).toContain("border-border")
    expect(stateFilter.className).toContain("bg-background")
    expect(screen.queryByRole("button", { name: "Stale only" })).toBeNull()
    expect(screen.queryByRole("button", { name: /spam\/no-action/i })).toBeNull()
  })

  it("keeps Spam in the state filter and drops stale / show-filtered toggles", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    expect(await screen.findByRole("option", { name: "Spam" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Stale only" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Show spam/no-action" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Showing spam/no-action" })).toBeNull()
  })

  it("writes the chosen state onto the mailbox URL", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    await user.click(await screen.findByRole("option", { name: "Needs human review" }))
    expect(replace).toHaveBeenCalledWith("/mailboxes/sales?state=REQUIRES_HUMAN")
  })

  it("does not send stale_only or include_filtered on the list call", async () => {
    renderPage()
    await screen.findByRole("combobox", { name: "Filter by state" })
    const [, params] = listThreads.mock.calls[0] as [string, Record<string, unknown>]
    expect(params).not.toHaveProperty("stale_only")
    expect(params).not.toHaveProperty("include_filtered")
  })

  it("writes from date onto the mailbox URL", async () => {
    renderPage()
    const fromInput = await screen.findByLabelText("From date (UTC)")
    fireEvent.change(fromInput, { target: { value: "2026-08-01" } })
    expect(replace).toHaveBeenCalledWith("/mailboxes/sales?from=2026-08-01")
  })

  it("labels date filters with UTC semantics", async () => {
    renderPage()
    expect(await screen.findByText("From (UTC)")).toBeInTheDocument()
    expect(screen.getByText("To (UTC)")).toBeInTheDocument()
  })
})

describe("Mailbox page — useSearchParams Suspense boundary", () => {
  beforeEach(() => {
    replace.mockReset()
    listThreads.mockReset()
    searchParamsString.mockReturnValue("state=REQUIRES_HUMAN")
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

  it("applies the state filter from the URL without bailing out of Suspense", async () => {
    // Bug this catches: useSearchParams without a Suspense boundary opts the
    // whole mailbox route into CSR bailout during prerender.
    renderPage()
    const stateFilter = await screen.findByRole("combobox", {
      name: "Filter by state",
    })
    expect(stateFilter).toHaveTextContent(/needs human review/i)
    expect(listThreads).toHaveBeenCalledWith(
      "sales",
      expect.objectContaining({ state: "REQUIRES_HUMAN" }),
    )
  })

  it("applies date range from the URL", async () => {
    searchParamsString.mockReturnValue("from=2026-08-01&to=2026-08-28")
    renderPage()
    expect(await screen.findByLabelText("From date (UTC)")).toHaveValue("2026-08-01")
    expect(screen.getByLabelText("To date (UTC)")).toHaveValue("2026-08-28")
    expect(listThreads).toHaveBeenCalledWith(
      "sales",
      expect.objectContaining({ from: "2026-08-01", to: "2026-08-28" }),
    )
  })
})
