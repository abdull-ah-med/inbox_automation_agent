import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const askChat = vi.fn()
const listMailboxes = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    chat: {
      askStream: (...args: unknown[]) => askChat(...args),
    },
    mailboxes: {
      list: (...args: unknown[]) => listMailboxes(...args),
    },
  },
}))

import { InboxAssistant } from "@/components/inbox-assistant"

const THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

const mailboxList = [
  {
    mailbox: "sales",
    email_address: "sales@example.com",
    label: "Sales",
    thread_count: 4,
    unread_count: 0,
    awaiting_action_count: 1,
    filtered_count: 0,
    stale_count: 0,
    urgency_breakdown: {},
    recent_threads: [],
  },
]

const groundedResponse = {
  answer: "The overdue billing dispute is waiting on review.",
  citations: [
    {
      thread_id: THREAD_ID,
      mailbox: "sales@example.com",
      subject: "Invoice dispute — overdue billing",
      state: "REQUIRES_HUMAN",
      urgency: "HIGH",
      snippet: "Please review the overdue billing packet.",
      url_path: `/threads/${THREAD_ID}`,
    },
  ],
  retrieval_count: 1,
  mailbox: null,
  refused_write: false,
}

const deliverStream = (
  handlers: {
    onMeta?: (meta: {
      citations: typeof groundedResponse.citations
      retrieval_count: number
      mailbox: string | null
      refused_write: boolean
    }) => void
    onDelta?: (text: string) => void
    onDone?: () => void
    onStatus?: (text: string) => void
  },
  answer = groundedResponse.answer,
  extra: Partial<typeof groundedResponse> = {},
) => {
  handlers.onMeta?.({
    citations: extra.citations ?? groundedResponse.citations,
    retrieval_count: extra.retrieval_count ?? 1,
    mailbox: extra.mailbox ?? null,
    refused_write: extra.refused_write ?? false,
  })
  handlers.onDelta?.(answer)
  handlers.onDone?.()
}

const renderBot = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <InboxAssistant />
    </QueryClientProvider>,
  )
}

describe("InboxAssistant", () => {
  beforeEach(() => {
    askChat.mockReset()
    listMailboxes.mockReset()
    listMailboxes.mockResolvedValue(mailboxList)
    askChat.mockImplementation(
      async (
        _body: unknown,
        handlers: Parameters<typeof deliverStream>[0],
      ) => {
        deliverStream(handlers)
      },
    )
  })

  it("opens a chat panel named InboxAssistant", async () => {
    const user = userEvent.setup()
    renderBot()
    expect(screen.queryByRole("dialog", { name: /inboxassistant/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(screen.getByRole("dialog", { name: /inboxassistant/i })).toBeInTheDocument()
    expect(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
    ).toBeInTheDocument()
    expect(screen.getByText("Ask about the inbox")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Reset conversation" }),
    ).toBeInTheDocument()
    expect(screen.queryByRole("log")).not.toBeInTheDocument()
  })

  it("asks a question and shows a grounded overview with a thread citation", async () => {
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(askChat.mock.calls[0]?.[0]).toEqual({
      message: "billing disputes waiting on review",
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

  it("sends prior turns as history on a follow-up", async () => {
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "tell me more",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(askChat.mock.calls[1]?.[0]).toEqual({
      message: "tell me more",
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

  it("reset restores the empty chat without calling the API again", async () => {
    const user = userEvent.setup()
    renderBot()
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
    renderBot()
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
      async (
        _body: unknown,
        handlers: Parameters<typeof deliverStream>[0],
      ) => {
        deliverStream(handlers, groundedResponse.answer, { citations })
      },
    )
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText(/overdue packet/)).toBeInTheDocument()
    expect(document.querySelector("img")).toBeNull()
  })

  it("grows the message field as the user adds lines", async () => {
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const input = screen.getByRole("textbox", { name: /message inboxassistant/i })
    expect(input).toHaveAttribute("rows", "1")
    await user.type(input, "first line{Enter}second line{Enter}third line")
    expect(input).toHaveAttribute("rows", "3")
  })

  it("uses a compact composer with circular send and mailbox select above it", async () => {
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const send = screen.getByRole("button", { name: /^send$/i })
    expect(send.querySelector("svg.lucide-arrow-up")).not.toBeNull()
    expect(send.className).toMatch(/rounded-full/)
    expect(
      screen.getByRole("combobox", { name: "Mailbox" }),
    ).toBeInTheDocument()
    const input = screen.getByRole("textbox", { name: /message inboxassistant/i })
    expect(input.className).not.toMatch(/min-h-14/)
    const panel = screen.getByRole("dialog", { name: /inboxassistant/i })
    expect(panel.className).toMatch(/flex/)
    expect(panel.querySelector("[data-slot='card']")?.className).toMatch(
      /min-h-0/,
    )
  })

  it("hides the command-enter hint on small screens", async () => {
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const shortcut = screen.getByText("⌘")
    expect(shortcut.closest("p, span, div")).toHaveClass("hidden")
    expect(shortcut.closest("p, span, div")?.className).toMatch(/sm:(flex|inline-flex)/)
  })

  it("opens as a floating card instead of a full-screen sheet", async () => {
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    const panel = screen.getByRole("dialog", { name: /inboxassistant/i })
    expect(panel.className).not.toMatch(/\btop-20\b/)
    expect(panel.className).not.toMatch(/\binset-x-3\b/)
    expect(panel.className).toMatch(/max-h-\[70dvh\]/)
  })

  it("keeps the closed panel in the document so it can animate shut", async () => {
    const user = userEvent.setup()
    renderBot()
    expect(screen.queryByRole("dialog", { name: /inboxassistant/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(screen.getByRole("dialog", { name: /inboxassistant/i })).toHaveAttribute(
      "data-state",
      "open",
    )
    await user.click(screen.getByRole("button", { name: /close inboxassistant/i }))
    expect(screen.queryByRole("dialog", { name: /inboxassistant/i })).not.toBeInTheDocument()
    const panel = document.querySelector('[role="dialog"][aria-label="InboxAssistant"]')
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
    renderBot()
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
    renderBot()
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
    renderBot()
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
    renderBot()
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

  it("keeps incomplete markdown as typed text until the stream finishes", async () => {
    let handlers: Parameters<typeof deliverStream>[0] | null = null
    askChat.mockImplementation(
      (_body: unknown, nextHandlers: Parameters<typeof deliverStream>[0]) => {
        handlers = nextHandlers
        return new Promise(() => {})
      },
    )
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "latest on the info mailbox",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    await act(async () => {
      handlers?.onDelta?.("**Active")
    })
    expect(await screen.findByText("**Active")).toBeInTheDocument()
    expect(document.querySelector("strong")).toBeNull()
    await act(async () => {
      handlers?.onDelta?.("/Pending Items:** Bonnie")
    })
    expect(
      await screen.findByText("**Active/Pending Items:** Bonnie"),
    ).toBeInTheDocument()
    expect(document.querySelector("strong")).toBeNull()
    await act(async () => {
      handlers?.onDone?.()
    })
    const heading = await screen.findByText("Active/Pending Items:")
    expect(heading.tagName).toBe("STRONG")
    expect(screen.queryByText(/\*\*/)).toBeNull()
  })

  it("renders **bold** in the answer as emphasis, not asterisks", async () => {
    askChat.mockImplementation(
      async (
        _body: unknown,
        handlers: Parameters<typeof deliverStream>[0],
      ) => {
        deliverStream(
          handlers,
          "**Active/Pending Items:** Bonnie Moore is waiting.\nThe mailbox has two drafts.",
        )
      },
    )
    const user = userEvent.setup()
    renderBot()
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
      async (
        _body: unknown,
        handlers: Parameters<typeof deliverStream>[0],
      ) => {
        deliverStream(handlers, 'See **here** <img src=x onerror="alert(1)">')
      },
    )
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "info mailbox",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    expect(await screen.findByText("here")).toBeInTheDocument()
    expect(document.querySelector("img")).toBeNull()
  })

  it("lets the reviewer make the panel larger then smaller", async () => {
    const user = userEvent.setup()
    renderBot()
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
})
