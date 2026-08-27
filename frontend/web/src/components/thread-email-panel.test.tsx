import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ThreadEmailPanel } from "@/components/thread-email-panel"
import type { MessageDetail } from "@/lib/types"

const BODY = "Please review the overdue billing packet."
const SENDER = "alice@example.com"
const THREAD_ID = "thread-1"
const HTML_BODY = "<b>Invoice</b> overdue"
const RICH_TITLE = `Rich view of email from ${SENDER}`

vi.mock("@/lib/api-client", () => ({
  api: {
    threads: {
      getMessageHtml: vi.fn(),
    },
  },
}))

vi.mock("@/components/html-email-frame", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/html-email-frame")>()
  return {
    ...actual,
    isHtmlEmailSupported: () => true,
  }
})

import { api } from "@/lib/api-client"

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
  outlook_url: "https://outlook.office.com/mail/msg-1",
}

const renderPanel = (messages: MessageDetail[] = [message]) =>
  render(<ThreadEmailPanel threadId={THREAD_ID} subject="Invoice dispute" messages={messages} />)

const mockHtmlOk = () => {
  vi.mocked(api.threads.getMessageHtml).mockResolvedValue({
    content_type: "html",
    html: HTML_BODY,
  })
}

describe("ThreadEmailPanel", () => {
  beforeEach(() => {
    vi.mocked(api.threads.getMessageHtml).mockReset()
  })

  it("defaults to plain text and does not fetch HTML until Rich view is clicked", () => {
    mockHtmlOk()
    renderPanel()
    expect(screen.getByText(BODY)).toBeInTheDocument()
    expect(screen.queryByTitle(RICH_TITLE)).not.toBeInTheDocument()
    expect(api.threads.getMessageHtml).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Rich view" })).toBeInTheDocument()
  })

  it("keeps the message open when the sender address is clicked", async () => {
    const user = userEvent.setup()
    renderPanel()
    expect(screen.getByText(BODY)).toBeInTheDocument()
    await user.click(screen.getByText(SENDER))
    expect(screen.getByText(BODY)).toBeInTheDocument()
    expect(screen.getByText(SENDER).closest("button")).toBeNull()
  })

  it("hides the message when Hide is clicked", async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole("button", { name: "Hide" }))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Show" }))
    expect(screen.getByText(BODY)).toBeInTheDocument()
  })

  it("shows Rich view on the same row as Outlook and Hide when expanded", () => {
    renderPanel()
    const outlook = screen.getByRole("link", {
      name: /Open email from alice@example.com in Outlook/i,
    })
    const hide = screen.getByRole("button", { name: "Hide" })
    const rich = screen.getByRole("button", { name: "Rich view" })
    expect(outlook.parentElement).toBe(hide.parentElement)
    expect(hide.parentElement).toBe(rich.parentElement)
  })

  it("swaps plain text for a sandboxed iframe on Rich view", async () => {
    const user = userEvent.setup()
    mockHtmlOk()
    renderPanel()
    await user.click(screen.getByRole("button", { name: "Rich view" }))
    const frame = await screen.findByTitle(RICH_TITLE)
    expect(frame.tagName).toBe("IFRAME")
    const sandbox = frame.getAttribute("sandbox") ?? ""
    expect(sandbox).toContain("allow-popups")
    expect(sandbox).not.toContain("allow-scripts")
    expect(frame.getAttribute("srcdoc") ?? "").toContain(HTML_BODY)
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Plain text" })).toBeInTheDocument()
  })

  it("restores plain EmailBody when Plain text is clicked", async () => {
    const user = userEvent.setup()
    mockHtmlOk()
    renderPanel()
    await user.click(screen.getByRole("button", { name: "Rich view" }))
    await screen.findByTitle(RICH_TITLE)
    await user.click(screen.getByRole("button", { name: "Plain text" }))
    expect(screen.getByText(BODY)).toBeInTheDocument()
    expect(screen.queryByTitle(RICH_TITLE)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Rich view" })).toBeInTheDocument()
  })

  it("shows an inline error with Retry when rich view fetch fails", async () => {
    const user = userEvent.setup()
    vi.mocked(api.threads.getMessageHtml).mockRejectedValue(new Error("network"))
    renderPanel()
    await user.click(screen.getByRole("button", { name: "Rich view" }))
    expect(await screen.findByText(/We couldn't load the rich view/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
    expect(screen.getByText(BODY)).toBeInTheDocument()

    mockHtmlOk()
    await user.click(screen.getByRole("button", { name: "Retry" }))
    await waitFor(() => {
      expect(screen.getByTitle(RICH_TITLE)).toBeInTheDocument()
    })
  })

  it("shows To, Cc, and Bcc on separate lines without merging Cc into To", () => {
    renderPanel([
      {
        ...message,
        to: ["sales@example.com"],
        cc: ["ops@example.com"],
        bcc: ["audit@example.com"],
      },
    ])
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
    renderPanel()
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

    renderPanel([inboundFirst, inboundLatest, outbound])

    expect(screen.queryByRole("button", { name: /show earlier/i })).not.toBeInTheDocument()

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

    expect(screen.getByText("The GitHub action is failing on main.")).toBeInTheDocument()
    expect(screen.queryByText("No new text in this reply")).not.toBeInTheDocument()
    expect(screen.queryByText("Can you check the Vercel deploy?")).not.toBeInTheDocument()
    expect(screen.getByText("From: Alice")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Show" })).toHaveLength(2)
    expect(screen.getByRole("button", { name: "Hide" })).toBeInTheDocument()
  })

  it("keeps a one-line peek of the preview when a message is hidden", async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole("button", { name: "Hide" }))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
    expect(screen.getByText("Please review")).toBeInTheDocument()
  })

  it("labels empty Graph meeting accepts instead of a blank body", () => {
    renderPanel([
      {
        ...message,
        id: "msg-meeting-1",
        direction: "outbound",
        sender: "elise@example.com",
        body_text: "",
        reply_text: "",
        body_preview: null,
        meeting_message_type: "meetingAccepted",
        meeting_response_type: "accepted",
      },
    ])
    expect(screen.getByText("Meeting accepted")).toBeInTheDocument()
    expect(screen.queryByText("(no message body)")).not.toBeInTheDocument()
  })
})
