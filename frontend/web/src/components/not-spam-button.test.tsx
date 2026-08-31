import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { NotSpamButton } from "@/components/not-spam-button"
import { renderWithProviders } from "@/test/render"

const markNotSpamMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      markNotSpam: (...args: unknown[]) => markNotSpamMock(...args),
    },
  },
}))

const renderButton = () =>
  renderWithProviders(
    <NotSpamButton threadId="e25cc63b-9db9-4c2d-af45-e598a639eaec" sender="orders@sample-lab.example.com" />,
  )

describe("NotSpamButton", () => {
  beforeEach(() => {
    markNotSpamMock.mockReset()
    markNotSpamMock.mockResolvedValue({
      thread_id: "e25cc63b-9db9-4c2d-af45-e598a639eaec",
      state: "DRAFTED",
      is_spam: false,
      sender_address: "orders@sample-lab.example.com",
      outlook_unchanged: true,
    })
  })

  it("explains that Outlook Junk will not change", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderButton()
    await user.click(screen.getByRole("button", { name: "Mark as not spam" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent(/Outlook/i)
    expect(dialog).toHaveTextContent(/cannot move/i)
    expect(dialog).toHaveTextContent("orders@sample-lab.example.com")
  })

  it("confirms the correction for this thread", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderButton()
    await user.click(screen.getByRole("button", { name: "Mark as not spam" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Confirm not spam" }))
    await waitFor(() => {
      expect(markNotSpamMock).toHaveBeenCalledWith("e25cc63b-9db9-4c2d-af45-e598a639eaec")
    })
  })
})
