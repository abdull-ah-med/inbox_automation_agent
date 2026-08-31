import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  askChat,
  deliverStream,
  groundedResponse,
  renderWithClient,
  resetInboxAssistantMocks,
  THREAD_ID,
} from "@/components/inbox-assistant.test-helpers"

vi.mock("@/lib/api-client", async () => {
  const helpers = await import("@/components/inbox-assistant.test-helpers")
  return {
    api: {
      chat: {
        askStream: (...args: unknown[]) => helpers.askChat(...args),
        createSession: (...args: unknown[]) => helpers.createSession(...args),
        getSession: (...args: unknown[]) => helpers.getSession(...args),
      },
      mailboxes: {
        list: (...args: unknown[]) => helpers.listMailboxes(...args),
      },
    },
  }
})

import { InboxAssistant } from "@/components/inbox-assistant"

describe("InboxAssistant basics", () => {
  beforeEach(() => {
    resetInboxAssistantMocks()
  })

  it("opens a chat panel named InboxAssistant", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    expect(screen.queryByRole("dialog", { name: /inboxassistant/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(screen.getByRole("dialog", { name: /inboxassistant/i })).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: /message inboxassistant/i })).toBeInTheDocument()
    expect(screen.getByText("Ask about the inbox")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reset conversation" })).toBeInTheDocument()
    expect(screen.queryByRole("log")).not.toBeInTheDocument()
  })

  it("asks a question and shows a grounded overview with a thread citation", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(askChat.mock.calls[0]?.[0]).toEqual({
      message: "billing disputes waiting on review",
      session_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    })
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /invoice dispute — overdue billing/i }),
    ).toHaveAttribute("href", `/threads/${THREAD_ID}`)
    expect(screen.getByRole("log")).toBeInTheDocument()
    expect(screen.queryByText("Ask about the inbox")).not.toBeInTheDocument()
  })

  it("aborts the in-flight ask when the panel unmounts mid-stream", async () => {
    let capturedSignal: AbortSignal | undefined
    askChat.mockImplementation((_body: unknown, _handlers: unknown, signal?: AbortSignal) => {
      capturedSignal = signal
      return new Promise(() => {
        // Never resolves — simulates a still-open SSE connection at unmount time.
      })
    })
    const user = userEvent.setup()
    const { unmount } = renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await waitFor(() => expect(askChat).toHaveBeenCalled())
    expect(capturedSignal?.aborted).toBe(false)

    unmount()

    expect(capturedSignal?.aborted).toBe(true)
  })

  it("sends prior turns as history on a follow-up", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    await user.type(screen.getByRole("textbox", { name: /message inboxassistant/i }), "tell me more")
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(askChat.mock.calls[1]?.[0]).toEqual({
      message: "tell me more",
      session_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      history: [
        { role: "user", content: "billing disputes waiting on review" },
        {
          role: "assistant",
          content: "The overdue billing dispute is waiting on review.",
          citations: [
            {
              thread_id: THREAD_ID,
              subject: "Invoice dispute — overdue billing",
            },
          ],
        },
      ],
    })
  })

  it("shows every cited thread, including the ninth O'Mason match", async () => {
    const user = userEvent.setup()
    const citations = Array.from({ length: 9 }, (_, index) => ({
      thread_id: `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa${index}`,
      mailbox: "support@sample-site.example.com",
      subject: `O'Mason thread ${index + 1}`,
      state: "RESOLVED",
      urgency: "NORMAL",
      snippet: "Users thread snippet",
      url_path: `/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa${index}`,
    }))
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, "Latest is O'Mason Lumber Users from 29 July.", {
          citations,
          retrieval_count: 9,
        })
      },
    )
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "give me the latest on omason",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/latest is o'mason lumber users/i)).toBeInTheDocument()
    expect(screen.getAllByRole("link", { name: /open thread/i })).toHaveLength(9)
    expect(screen.queryByText(/more cited thread/i)).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: /o'mason thread 9/i })).toBeInTheDocument()
  })

  it("reset restores the empty chat without calling the API again", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Reset conversation" }))
    expect(screen.getByText("Ask about the inbox")).toBeInTheDocument()
    expect(screen.queryByRole("log")).not.toBeInTheDocument()
    expect(askChat).toHaveBeenCalledTimes(1)
  })

  it("does not call the API for a blank question", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(askChat).not.toHaveBeenCalled()
    expect(screen.getByText(/enter a question/i)).toBeInTheDocument()
  })

  it("renders email snippets as text, not HTML", async () => {
    const citations = [
      {
        ...groundedResponse.citations[0],
        snippet: '<img src=x onerror="alert(1)"> overdue packet',
      },
    ]
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, groundedResponse.answer, { citations })
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(screen.getByRole("textbox", { name: /message inboxassistant/i }), "billing disputes")
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/overdue packet/)).toBeInTheDocument()
    expect(document.querySelector("img")).toBeNull()
  })

  it("grows the message field as the user adds lines", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const input = screen.getByRole("textbox", { name: /message inboxassistant/i })
    expect(input).toHaveAttribute("rows", "1")
    await user.type(input, "first line{Enter}second line{Enter}third line")
    expect(input).toHaveAttribute("rows", "3")
  })

  it("uses a compact composer with circular send and mailbox select above it", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const send = screen.getByRole("button", { name: /^send$/i })
    expect(send.querySelector("svg.lucide-arrow-up")).not.toBeNull()
    expect(send.className).toMatch(/rounded-full/)
    expect(screen.getByRole("combobox", { name: "Mailbox" })).toBeInTheDocument()
    const input = screen.getByRole("textbox", { name: /message inboxassistant/i })
    expect(input.className).not.toMatch(/min-h-14/)
    const panel = screen.getByRole("dialog", { name: /inboxassistant/i })
    expect(panel.className).toMatch(/flex/)
    expect(panel.querySelector("[data-slot='card']")?.className).toMatch(/min-h-0/)
  })

  it("hides the command-enter hint on small screens", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const shortcut = screen.getByText("⌘")
    expect(shortcut.closest("p, span, div")).toHaveClass("hidden")
    expect(shortcut.closest("p, span, div")?.className).toMatch(/sm:(flex|inline-flex)/)
  })

  it("opens as a floating card instead of a full-screen sheet", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const panel = screen.getByRole("dialog", { name: /inboxassistant/i })
    expect(panel.className).not.toMatch(/\btop-20\b/)
    expect(panel.className).not.toMatch(/\binset-x-3\b/)
    expect(panel.className).toMatch(/max-h-\[70dvh\]/)
  })

  it("anchors the open panel to the bottom-right with the launcher", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const panel = screen.getByRole("dialog", { name: /inboxassistant/i })
    // Native <dialog> UA sheets set left/inset-inline-start to 0; those must
    // be auto so the explicit right/bottom classes actually win.
    expect(panel.className).toMatch(/\bleft-auto\b/)
    expect(panel.className).toMatch(/\bstart-auto\b/)
    expect(panel.className).toMatch(/\bright-3\b/)
    expect(panel.className).toMatch(/\bbottom-3\b/)
    expect(panel.className).not.toMatch(/\bleft-0\b/)
    expect(panel.className).not.toMatch(/\bleft-3\b/)
  })

  it("keeps the transcript after close and reopen until Reset", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /close inboxassistant/i }))
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    expect(screen.getByText("billing disputes waiting on review")).toBeInTheDocument()
    expect(screen.queryByText("Ask about the inbox")).not.toBeInTheDocument()
  })
})
