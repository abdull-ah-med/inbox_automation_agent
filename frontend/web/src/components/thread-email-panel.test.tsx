import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { ThreadEmailPanel } from "@/components/thread-email-panel"
import type { MessageDetail } from "@/lib/types"

const BODY = "Please review the overdue billing packet."
const SENDER = "alice@example.com"

const message: MessageDetail = {
  id: "msg-1",
  direction: "inbound",
  sender: SENDER,
  to: ["sales@example.com"],
  cc: [],
  bcc: [],
  body_text: BODY,
  body_preview: "Please review",
  received_at: "2026-08-18T14:00:00Z",
  has_attachments: false,
  outlook_url: null,
}

describe("ThreadEmailPanel", () => {
  it("keeps the message open when the sender address is clicked", async () => {
    const user = userEvent.setup()
    render(<ThreadEmailPanel subject="Invoice dispute" messages={[message]} />)
    expect(screen.getByText(BODY)).toBeInTheDocument()
    await user.click(screen.getByText(SENDER))
    expect(screen.getByText(BODY)).toBeInTheDocument()
    expect(screen.getByText(SENDER).closest("button")).toBeNull()
  })

  it("hides the message when Hide is clicked", async () => {
    const user = userEvent.setup()
    render(<ThreadEmailPanel subject="Invoice dispute" messages={[message]} />)
    await user.click(screen.getByRole("button", { name: "Hide" }))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Show" }))
    expect(screen.getByText(BODY)).toBeInTheDocument()
  })

  it("shows To, Cc, and Bcc on separate lines without merging Cc into To", () => {
    render(
      <ThreadEmailPanel
        subject="Invoice dispute"
        messages={[
          {
            ...message,
            to: ["sales@example.com"],
            cc: ["ops@example.com"],
            bcc: ["audit@example.com"],
          },
        ]}
      />,
    )
    const toLine = screen.getByText(/^To:/)
    const ccLine = screen.getByText(/^Cc:/)
    const bccLine = screen.getByText(/^Bcc:/)
    expect(toLine).toHaveTextContent("To: sales@example.com")
    expect(ccLine).toHaveTextContent("Cc: ops@example.com")
    expect(bccLine).toHaveTextContent("Bcc: audit@example.com")
    expect(toLine.textContent).not.toContain("ops@example.com")
    expect(toLine.textContent).not.toContain("audit@example.com")
  })

  it("omits Cc and Bcc lines when those lists are empty", () => {
    render(<ThreadEmailPanel subject="Invoice dispute" messages={[message]} />)
    expect(screen.getByText(/^To:/)).toBeInTheDocument()
    expect(screen.queryByText(/^Cc:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Bcc:/)).not.toBeInTheDocument()
  })
})
