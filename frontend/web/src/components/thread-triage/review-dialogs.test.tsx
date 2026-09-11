import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import {
  ApproveDraftDialog,
  RejectDraftDialog,
  ResolvePromptDialog,
  RewriteDraftDialog,
} from "@/components/thread-triage/review-dialogs"

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

const renderApprove = (overrides: Partial<Parameters<typeof ApproveDraftDialog>[0]> = {}) => {
  const onApprovalScopeChange = vi.fn()
  render(
    <ApproveDraftDialog
      open
      onOpenChange={noop}
      approveBody="Happy to help."
      onApproveBodyChange={noop}
      approvalNote=""
      onApprovalNoteChange={noop}
      approvalScope=""
      onApprovalScopeChange={onApprovalScopeChange}
      busy={false}
      isPending={false}
      onConfirm={noop}
      {...overrides}
    />,
  )
  return { onApprovalScopeChange }
}

describe("ApproveDraftDialog", () => {
  it("offers once, similar, sender, and mailbox learning scopes", () => {
    renderApprove()

    expect(
      screen.getByRole("radio", { name: "Apply learning to this thread only" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("radio", { name: "Apply learning to similar emails" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("radio", {
        name: "Apply learning to similar emails from this sender",
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("radio", { name: "Apply learning always in this mailbox" }),
    ).toBeInTheDocument()
    expect(screen.getByText("Similar emails from this sender")).toBeInTheDocument()
    expect(screen.getByText("Always in this mailbox")).toBeInTheDocument()
  })
})

const renderReject = (overrides: Partial<Parameters<typeof RejectDraftDialog>[0]> = {}) => {
  const onProcessNoteChange = vi.fn()
  render(
    <RejectDraftDialog
      open
      onOpenChange={noop}
      rejectNote="Wrong process"
      onRejectNoteChange={noop}
      rejectReason="incomplete"
      onRejectReasonChange={noop}
      processNote=""
      onProcessNoteChange={onProcessNoteChange}
      busy={false}
      isPending={false}
      onConfirm={noop}
      {...overrides}
    />,
  )
  return { onProcessNoteChange }
}

describe("RejectDraftDialog process note", () => {
  it("asks what she would do instead when the letter is not a no-reply", () => {
    renderReject()
    expect(screen.getByLabelText("What would you do instead?")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Add correct process step" }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Start from suggested process" }),
    ).not.toBeInTheDocument()
  })

  it("hides the process box when no reply is needed", () => {
    renderReject({ rejectReason: "wrong_action" })
    expect(screen.queryByLabelText("What would you do instead?")).not.toBeInTheDocument()
  })
})

describe("RewriteDraftDialog", () => {
  it("asks whether to rewrite the draft with the process", () => {
    render(
      <RewriteDraftDialog
        open
        onOpenChange={noop}
        isPending={false}
        onConfirm={noop}
        onSkip={noop}
      />,
    )
    expect(screen.getByRole("dialog", { name: "Rewrite draft?" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Rewrite draft" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Not now" })).toBeInTheDocument()
  })
})
