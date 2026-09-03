import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
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

  it("offers Awaiting action and drops awaiting client, vendor, and partner", async () => {
    // Bug this catches: mailbox state filter still listed waiting-on-others
    // states instead of the dashboard "awaiting action" queue.
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    expect(await screen.findByRole("option", { name: "Awaiting action" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: "Awaiting client" })).toBeNull()
    expect(screen.queryByRole("option", { name: "Awaiting vendor" })).toBeNull()
    expect(screen.queryByRole("option", { name: "Awaiting partner" })).toBeNull()
  })

  it("writes Awaiting action onto the mailbox URL as AWAITING_ACTION", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    await user.click(await screen.findByRole("option", { name: "Awaiting action" }))
    expect(replace).toHaveBeenCalledWith("/mailboxes/sales?state=AWAITING_ACTION")
  })

  it("writes Reply ready onto the mailbox URL as REPLY_REVIEW", async () => {
    // Bug this catches: label said Reply ready but URL still sent DRAFTED,
    // so FYI / action-without-letter threads appeared in the list.
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    await user.click(await screen.findByRole("option", { name: "Reply ready" }))
    expect(replace).toHaveBeenCalledWith("/mailboxes/sales?state=REPLY_REVIEW")
    expect(replace).not.toHaveBeenCalledWith("/mailboxes/sales?state=DRAFTED")
  })

  it("offers Stale, Spam, Open FYI, and Recently resolved by DraftAssistant", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    expect(await screen.findByRole("option", { name: "Stale" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Spam" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Open FYI" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Recently resolved by DraftAssistant" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: "Spam / no action" })).toBeNull()
    expect(screen.queryByRole("option", { name: "No action needed" })).toBeNull()
  })

  it("writes Stale onto the mailbox URL as STALE", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("combobox", { name: "Filter by state" }))
    await user.click(await screen.findByRole("option", { name: "Stale" }))
    expect(replace).toHaveBeenCalledWith("/mailboxes/sales?state=STALE")
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

  it("renders inline from and to date filters aligned with other controls", async () => {
    // Spec: filter row stays single-height — no floating From/To labels above
    // the triggers (those misalign against the state/urgency selects). Empty
    // triggers show From/To as placeholder copy; aria-labels stay for a11y.
    renderPage()
    const from = await screen.findByRole("button", { name: "From date" })
    const to = screen.getByRole("button", { name: "To date" })
    expect(from).toHaveTextContent(/^From$/)
    expect(to).toHaveTextContent(/^To$/)
    expect(screen.queryByText("From", { selector: "label" })).not.toBeInTheDocument()
    expect(screen.queryByText("To", { selector: "label" })).not.toBeInTheDocument()
  })

  it("opens the from date calendar popover", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage()
    await user.click(await screen.findByRole("button", { name: "From date" }))
    expect(screen.getByRole("grid")).toBeInTheDocument()
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
    expect(await screen.findByRole("button", { name: "From date" })).toHaveTextContent(
      /August 1.*2026/i,
    )
    expect(screen.getByRole("button", { name: "To date" })).toHaveTextContent(/August 28.*2026/i)
    expect(listThreads).toHaveBeenCalledWith(
      "sales",
      expect.objectContaining({ from: "2026-08-01", to: "2026-08-28" }),
    )
  })
})
