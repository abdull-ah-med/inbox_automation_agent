import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const searchThreads = vi.fn()
const nav = { pathname: "/dashboard" }

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname: () => nav.pathname,
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
import { renderWithProviders } from "@/test/render"

const renderHeader = () => renderWithProviders(<AppHeader />)

describe("AppHeader search", () => {
  beforeEach(() => {
    nav.pathname = "/dashboard"
    searchThreads.mockReset()
    searchThreads.mockResolvedValue({ query: "", mailbox: null, hits: [] })
  })

  it("labels the theme toggle for the opposite of the resolved theme", () => {
    renderHeader()
    expect(screen.getByRole("button", { name: /switch to dark mode/i })).toBeInTheDocument()
  })

  it("puts a mail search box in the header, not an ask dialog", () => {
    renderHeader()
    expect(screen.getByRole("searchbox", { name: /search mail/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ask the inbox/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("puts mail search on a full-width second row below 950px so the header cannot stretch", () => {
    renderHeader()
    const search = screen.getByRole("search")
    expect(search.parentElement).toHaveClass("col-span-2")
    expect(search.parentElement).toHaveClass("min-[950px]:col-span-1")
    expect(search.parentElement).toHaveClass("min-[950px]:col-start-2")
    expect(search.parentElement).toHaveClass("min-[950px]:row-start-1")
    expect(screen.getByRole("link", { name: /inbox triage automation/i })).toHaveClass("truncate")
  })

  it("does not clip search suggestions under the overview", async () => {
    const user = userEvent.setup()
    renderHeader()
    await user.click(screen.getByRole("searchbox", { name: /search mail/i }))
    expect(screen.getByRole("listbox", { name: /search suggestions/i })).toBeVisible()
    // overflow-x:hidden on the sticky header computes overflow-y to auto and
    // clips the absolutely positioned list where the overview begins.
    expect(screen.getByRole("banner").className.split(/\s+/)).not.toContain("overflow-x-hidden")
  })
})

describe("AppHeader settings and click cursors", () => {
  beforeEach(() => {
    nav.pathname = "/dashboard"
    searchThreads.mockReset()
    searchThreads.mockResolvedValue({ query: "", mailbox: null, hits: [] })
  })

  it("marks settings as the current page on /settings", () => {
    nav.pathname = "/settings"
    renderHeader()
    expect(screen.getByRole("link", { name: "Open settings" })).toHaveAttribute(
      "aria-current",
      "page",
    )
  })

  it("does not mark settings current on the dashboard", () => {
    renderHeader()
    expect(screen.getByRole("link", { name: "Open settings" })).not.toHaveAttribute("aria-current")
  })

  it("uses a pointer cursor on settings, theme toggle, and sign out", () => {
    renderHeader()
    expect(screen.getByRole("link", { name: "Open settings" })).toHaveClass("cursor-pointer")
    expect(screen.getByRole("button", { name: /switch to dark mode/i })).toHaveClass(
      "cursor-pointer",
    )
    expect(screen.getByRole("button", { name: "Sign out" })).toHaveClass("cursor-pointer")
  })
})
