import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ResolvePromptDialog } from "@/components/thread-triage/review-dialogs"

const noop = () => undefined

const renderDialog = (overrides: Partial<Parameters<typeof ResolvePromptDialog>[0]> = {}) => {
  const onConfirm = vi.fn()
  render(
    <ResolvePromptDialog
      open
      onOpenChange={noop}
      actionsTaken=""
      onActionsTakenChange={noop}
      involved=""
      onInvolvedChange={noop}
      isPending={false}
      onConfirm={onConfirm}
      {...overrides}
    />,
  )
  return { onConfirm }
}

describe("ResolvePromptDialog", () => {
  it("disables Mark resolved until actions taken is non-empty", () => {
    renderDialog()
    expect(screen.getByRole("button", { name: "Mark thread resolved" })).toBeDisabled()
  })

  it("enables Mark resolved when actions taken is provided", () => {
    renderDialog({ actionsTaken: "Checked portal and logged the change" })
    expect(screen.getByRole("button", { name: "Mark thread resolved" })).toBeEnabled()
  })

  it("calls onConfirm when Mark resolved is clicked with actions taken", async () => {
    const user = userEvent.setup()
    const { onConfirm } = renderDialog({
      actionsTaken: "Confirmed no client action needed",
      involved: "accounting team",
    })

    await user.click(screen.getByRole("button", { name: "Mark thread resolved" }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})
