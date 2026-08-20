import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

const push = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, back: vi.fn(), replace: vi.fn() }),
}))

import { Breadcrumbs } from "@/components/breadcrumbs"
import { ThreadOriginBanner } from "@/components/thread-origin-banner"

describe("returning from an associated full thread", () => {
  it("sends Back to the original thread, not the mailbox", async () => {
    push.mockReset()
    const user = userEvent.setup()
    render(
      <Breadcrumbs
        items={[
          { label: "Overview", href: "/dashboard" },
          { label: "info", href: "/mailboxes/info" },
          {
            label: "Hart reminder 8/15",
            href: "/threads/thread-src",
          },
          { label: "SampleClient follow-up 8/14" },
        ]}
      />,
    )
    await user.click(
      screen.getByRole("button", { name: "Back to Hart reminder 8/15" }),
    )
    expect(push).toHaveBeenCalledWith("/threads/thread-src")
    expect(push).not.toHaveBeenCalledWith("/mailboxes/info")
  })

  it("shows a link back to the original thread subject", () => {
    render(
      <ThreadOriginBanner
        originId="thread-src"
        originSubject="Hart reminder 8/15"
      />,
    )
    const link = screen.getByRole("link", { name: "Back to Hart reminder 8/15" })
    expect(link).toHaveAttribute("href", "/threads/thread-src")
    expect(link).toHaveTextContent("Hart reminder 8/15")
  })
})
