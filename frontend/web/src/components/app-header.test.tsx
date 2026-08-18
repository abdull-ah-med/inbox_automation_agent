import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const searchThreads = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
}))

vi.mock("@/lib/api-client", () => ({
  api: {
    search: {
      threads: (...args: unknown[]) => searchThreads(...args),
    },
    mailboxes: {
      list: vi.fn().mockResolvedValue([]),
    },
  },
}))

vi.mock("@/features/auth/use-auth", () => ({
  useAuthState: () => ({
    accessToken: "tok",
    user: { email: "elise@example.com" },
  }),
  useLogout: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light", setTheme: vi.fn() }),
}))

import { AppHeader } from "@/components/app-header"

const renderHeader = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <AppHeader />
    </QueryClientProvider>,
  )
}

describe("AppHeader search", () => {
  beforeEach(() => {
    searchThreads.mockReset()
    searchThreads.mockResolvedValue({ query: "", mailbox: null, hits: [] })
  })

  it("puts a mail search box in the header, not an ask dialog", () => {
    renderHeader()
    expect(screen.getByRole("searchbox", { name: /search mail/i })).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /ask the inbox/i }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("puts mail search on a full-width second row so the header cannot stretch the page", () => {
    renderHeader()
    const search = screen.getByRole("search")
    expect(search.parentElement).toHaveClass("col-span-2")
    expect(search.parentElement).toHaveClass("sm:col-span-1")
    expect(screen.getByRole("link", { name: /inbox triage automation/i })).toHaveClass(
      "truncate",
    )
  })

  it("does not clip search suggestions under the overview", async () => {
    const user = userEvent.setup()
    renderHeader()
    await user.click(screen.getByRole("searchbox", { name: /search mail/i }))
    expect(
      screen.getByRole("listbox", { name: /search suggestions/i }),
    ).toBeVisible()
    // overflow-x:hidden on the sticky header computes overflow-y to auto and
    // clips the absolutely positioned list where the overview begins.
    expect(screen.getByRole("banner").className.split(/\s+/)).not.toContain(
      "overflow-x-hidden",
    )
  })
})
