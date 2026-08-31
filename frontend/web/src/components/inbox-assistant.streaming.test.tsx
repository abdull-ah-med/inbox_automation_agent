import { screen, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  askChat,
  deliverStream,
  getSession,
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

describe("InboxAssistant streaming", () => {
  beforeEach(() => {
    resetInboxAssistantMocks()
  })

  it("restores a stored session when InboxAssistant opens on an empty chat", async () => {
    window.localStorage.setItem("inboxassistant_session_all", "cccccccc-cccc-cccc-cccc-cccccccccccc")
    getSession.mockResolvedValue({
      session_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      mailbox: null,
      messages: [
        { role: "user", content: "billing disputes waiting on review" },
        {
          role: "assistant",
          content: "The overdue billing dispute is waiting on review.",
        },
      ],
    })
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    expect(screen.getByText("billing disputes waiting on review")).toBeInTheDocument()
  })

  it("shows a whole-answer markdown fence as prose, not backticks", async () => {
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(
          handlers,
          "```markdown\nThe overdue billing dispute is waiting on review.\n```",
        )
      },
    )
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
    expect(screen.queryByText(/```/)).toBeNull()
  })

  it("keeps the closed panel in the document so it can animate shut", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    expect(screen.queryByRole("dialog", { name: /inboxassistant/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(screen.getByRole("dialog", { name: /inboxassistant/i })).toHaveAttribute("data-state", "open")
    await user.click(screen.getByRole("button", { name: /close inboxassistant/i }))
    expect(screen.queryByRole("dialog", { name: /inboxassistant/i })).not.toBeInTheDocument()
    const panel = document.querySelector('dialog[aria-label="InboxAssistant"]')
    expect(panel).toHaveAttribute("data-state", "closed")
    expect(panel).toHaveAttribute("aria-hidden", "true")
  })

  it("shows Thinking status text while asking, not a composing orb", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    const status = screen.getByRole("status")
    expect(status).toHaveTextContent("Thinking")
    expect(status).toHaveAttribute("aria-busy", "true")
    expect(screen.queryByRole("img", { name: /composing/i })).not.toBeInTheDocument()
    await act(async () => {
      handlers?.onDelta?.(groundedResponse.answer)
    })
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("replaces Thinking with the tool status from the stream", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(screen.getByRole("status")).toHaveTextContent("Thinking")
    await act(async () => {
      handlers?.onStatus?.("Searching mail")
    })
    expect(screen.getByRole("status")).toHaveTextContent("Searching mail")
    expect(screen.queryByText("Thinking")).not.toBeInTheDocument()
    await act(async () => {
      handlers?.onStatus?.("Opening thread")
    })
    expect(screen.getByRole("status")).toHaveTextContent("Opening thread")
  })

  it("reveals the answer in token chunks as they arrive", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(screen.queryByText(/The overdue/)).not.toBeInTheDocument()
    await act(async () => {
      handlers?.onDelta?.("The overdue ")
    })
    expect(await screen.findByText(/The overdue/)).toBeInTheDocument()
    expect(
      screen.queryByText("The overdue billing dispute is waiting on review."),
    ).not.toBeInTheDocument()
    await act(async () => {
      handlers?.onDelta?.("billing dispute is waiting on review.")
    })
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
  })

  it("keeps citation cards hidden until the streamed answer finishes", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await act(async () => {
      handlers?.onMeta?.({
        citations: groundedResponse.citations,
        retrieval_count: 1,
        mailbox: null,
        refused_write: false,
      })
    })
    expect(
      screen.queryByRole("link", { name: /invoice dispute — overdue billing/i }),
    ).not.toBeInTheDocument()
    await act(async () => {
      handlers?.onDelta?.("The overdue ")
    })
    expect(await screen.findByText(/The overdue/)).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: /invoice dispute — overdue billing/i }),
    ).not.toBeInTheDocument()
    await act(async () => {
      handlers?.onDone?.()
    })
    expect(
      await screen.findByRole("link", {
        name: /invoice dispute — overdue billing/i,
      }),
    ).toHaveAttribute("href", `/threads/${THREAD_ID}`)
  })

  it("links [n] markers in the answer to the matching citation thread", async () => {
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, "Bonnie is still waiting on the overdue invoice [1].")
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "what is outstanding for Bonnie",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))

    const inline = await screen.findByRole("link", { name: /citation 1/i })
    expect(inline).toHaveAttribute("href", groundedResponse.citations[0].url_path)
    expect(inline).toHaveAccessibleName(/invoice dispute/i)
  })

  it("renders complete markdown emphasis while tokens are still streaming", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "latest on the info mailbox",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await act(async () => {
      handlers?.onDelta?.("**Active/Pending Items:** Bonnie")
    })
    const heading = await screen.findByText("Active/Pending Items:")
    expect(heading.tagName).toBe("STRONG")
    expect(screen.queryByText(/\*\*/)).toBeNull()
  })

  it("renders **bold** in the answer as emphasis, not asterisks", async () => {
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(
          handlers,
          "**Active/Pending Items:** Bonnie Moore is waiting.\nThe mailbox has two drafts.",
        )
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "latest on the info mailbox",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    const heading = await screen.findByText("Active/Pending Items:")
    expect(heading.tagName).toBe("STRONG")
    expect(screen.queryByText(/\*\*/)).toBeNull()
    expect(screen.getByText(/Bonnie Moore is waiting/)).toBeInTheDocument()
  })

  it("does not execute HTML that the model puts in an answer", async () => {
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        deliverStream(handlers, 'See **here** <img src=x onerror="alert(1)">')
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(screen.getByRole("textbox", { name: /message inboxassistant/i }), "info mailbox")
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await act(async () => {
      await new Promise<void>((resolve) => {
        let frames = 0
        const tick = () => {
          frames += 1
          if (frames >= 30) resolve()
          else requestAnimationFrame(tick)
        }
        requestAnimationFrame(tick)
      })
    })
    expect(await screen.findByText("here", {}, { timeout: 3000 })).toBeInTheDocument()
    expect(document.querySelector("img")).toBeNull()
  })

  it("lets the reviewer make the panel larger then smaller", async () => {
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const panel = screen.getByRole("dialog", { name: /inboxassistant/i })
    const larger = screen.getByRole("button", { name: "Make InboxAssistant larger" })
    const smaller = screen.getByRole("button", { name: "Make InboxAssistant smaller" })
    expect(smaller).toBeDisabled()
    expect(larger).toBeEnabled()
    expect(panel.className).toMatch(/sm:w-\[26rem\]/)
    await user.click(larger)
    expect(panel.className).toMatch(/sm:w-\[min\(48rem/)
    expect(panel.className).not.toMatch(/sm:w-\[36rem\]/)
    expect(larger).toBeDisabled()
    expect(smaller).toBeEnabled()
    await user.click(smaller)
    expect(panel.className).toMatch(/sm:w-\[26rem\]/)
    expect(larger).toBeEnabled()
    expect(smaller).toBeDisabled()
  })

  it("renders markdown while tokens are still streaming", async () => {
    askChat.mockImplementation(
      async (_body: unknown, handlers: Parameters<typeof deliverStream>[0]) => {
        handlers.onDelta?.("**Active/Pending Items:** Bonnie Moore")
      },
    )
    const user = userEvent.setup()
    renderWithClient(<InboxAssistant />)
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "latest on the info mailbox",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    const heading = await screen.findByText("Active/Pending Items:")
    expect(heading.tagName).toBe("STRONG")
    expect(screen.queryByText(/\*\*/)).toBeNull()
  })
})
