import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { UrgencyEditPopover } from "@/components/urgency-edit-popover"

const editUrgencyMock = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    drafts: {
      editUrgency: (...args: unknown[]) => editUrgencyMock(...args),
    },
  },
}))

const renderPopover = (disabled = false) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <UrgencyEditPopover
        draftId="draft-1"
        threadId="thread-1"
        currentUrgency="LOW"
        disabled={disabled}
      />
    </QueryClientProvider>,
  )
}

describe("UrgencyEditPopover", () => {
  beforeEach(() => {
    editUrgencyMock.mockReset()
    editUrgencyMock.mockResolvedValue({
      urgency: "HIGH",
      urgency_reason: "SLA deadline",
      updated_at: new Date().toISOString(),
      draft_id: "draft-1",
      thread_id: "thread-1",
    })
  })

  it("opens dialog and disables save when reason is empty", async () => {
    const user = userEvent.setup()
    renderPopover()
    await user.click(screen.getByRole("button", { name: "Edit urgency" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveAttribute("data-size", "md")
    expect(within(dialog).getByRole("button", { name: "Save urgency edit" })).toBeDisabled()
  })

  it("sends correct payload on save", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPopover()
    await user.click(screen.getByRole("button", { name: "Edit urgency" }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("combobox", { name: "Urgency level" }))
    await user.click(await screen.findByRole("option", { name: "HIGH" }))
    await user.type(within(dialog).getByLabelText("Urgency reason"), "Client has an SLA deadline")
    await user.click(within(dialog).getByRole("button", { name: "Save urgency edit" }))
    await waitFor(() => {
      expect(editUrgencyMock).toHaveBeenCalledWith("draft-1", {
        new_urgency: "HIGH",
        reason: "Client has an SLA deadline",
      })
    })
  })

  it("is keyboard accessible", async () => {
    const user = userEvent.setup()
    renderPopover()
    const trigger = screen.getByRole("button", { name: "Edit urgency" })
    trigger.focus()
    await user.keyboard("{Enter}")
    expect(await screen.findByRole("dialog")).toBeInTheDocument()
  })
})
