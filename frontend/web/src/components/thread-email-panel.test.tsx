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
  reply_text: BODY,
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

  it("shows every message newest-first with Received and Sent labels and flat cards", () => {
    const inboundFirst: MessageDetail = {
      ...message,
      id: "msg-in-1",
      sender: "alice@example.com",
      direction: "inbound",
      reply_text: "Can you check the Vercel deploy?",
      body_text: "Can you check the Vercel deploy?",
      received_at: "2026-08-22T14:41:00Z",
    }
    const outbound: MessageDetail = {
      ...message,
      id: "msg-out-2",
      sender: "sales@example.com",
      direction: "outbound",
      to: ["alice@example.com"],
      reply_text: "",
      body_text:
        "From: Alice <alice@example.com>\nSent: Monday, August 22, 2022 10:43 AM\nTo: sales@example.com\nSubject: Re: Vercel deploy\n\nCan you check the Vercel deploy?",
      body_preview: "From: Alice",
      received_at: "2026-08-22T14:43:00Z",
    }
    const inboundLatest: MessageDetail = {
      ...message,
      id: "msg-in-3",
      sender: "alice@example.com",
      direction: "inbound",
      reply_text: "The GitHub action is failing on main.",
      body_text: "The GitHub action is failing on main.",
      received_at: "2026-08-22T15:02:00Z",
    }

    render(
      <ThreadEmailPanel
        subject="Re: Vercel deploy"
        // Deliberately out of order — panel must sort by received_at DESC
        messages={[inboundFirst, inboundLatest, outbound]}
      />,
    )

    expect(
      screen.queryByRole("button", { name: /show earlier/i }),
    ).not.toBeInTheDocument()

    const articles = screen.getAllByRole("article")
    expect(articles).toHaveLength(3)
    expect(articles[0]).toHaveAccessibleName(/alice@example.com, Received/)
    expect(articles[0]).toHaveTextContent("The GitHub action is failing on main.")
    expect(articles[1]).toHaveAccessibleName(/sales@example.com, Sent/)
    expect(articles[2]).toHaveAccessibleName(/alice@example.com, Received/)
    for (const article of articles) {
      expect(article.className).not.toMatch(/\bml-6\b/)
    }
    expect(screen.queryByRole("list")).toBeNull()

    // Only the newest message body is expanded; older cards show a peek + Show
    expect(screen.getByText("The GitHub action is failing on main.")).toBeInTheDocument()
    expect(screen.queryByText("No new text in this reply")).not.toBeInTheDocument()
    expect(screen.queryByText("Can you check the Vercel deploy?")).not.toBeInTheDocument()
    expect(screen.getByText("From: Alice")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Show" })).toHaveLength(2)
    expect(screen.getByRole("button", { name: "Hide" })).toBeInTheDocument()
  })

  it("keeps a one-line peek of the preview when a message is hidden", async () => {
    const user = userEvent.setup()
    render(<ThreadEmailPanel subject="Invoice dispute" messages={[message]} />)
    await user.click(screen.getByRole("button", { name: "Hide" }))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
    expect(screen.getByText("Please review")).toBeInTheDocument()
  })
})
