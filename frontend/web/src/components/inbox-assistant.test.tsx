import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const askChat = vi.fn()
const listMailboxes = vi.fn()

vi.mock("@/lib/api-client", () => ({
  api: {
    chat: {
      ask: (...args: unknown[]) => askChat(...args),
    },
    mailboxes: {
      list: (...args: unknown[]) => listMailboxes(...args),
    },
  },
}))

vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({
    state,
    size,
    "aria-label": ariaLabel,
    "aria-hidden": ariaHidden,
  }: {
    state: string
    size?: number
    "aria-label"?: string
    "aria-hidden"?: boolean | "true"
  }) => (
    <div
      role="img"
      aria-label={ariaLabel ?? state}
      aria-hidden={ariaHidden}
      data-state={state}
      data-size={size}
    />
  ),
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
    askChat.mockResolvedValue(groundedResponse)
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
    expect(askChat).toHaveBeenCalledWith({
      message: "billing disputes waiting on review",
    })
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /invoice dispute — overdue billing/i }),
    ).toHaveAttribute("href", `/threads/${THREAD_ID}`)
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
    askChat.mockResolvedValue({
      ...groundedResponse,
      citations: [
        {
          ...groundedResponse.citations[0],
          snippet: '<img src=x onerror="alert(1)"> overdue packet',
        },
      ],
    })
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

  it("shows the composing orb instead of thinking copy while asking", async () => {
    let resolveAsk: (value: typeof groundedResponse) => void = () => {}
    askChat.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveAsk = resolve
        }),
    )
    const user = userEvent.setup()
    renderBot()
    await user.click(screen.getByRole("button", { name: /inboxassistant/i }))
    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument()
    await user.type(
      screen.getByRole("textbox", { name: /message inboxassistant/i }),
      "billing disputes waiting on review",
    )
    await user.click(screen.getByRole("button", { name: /^send$/i }))
    const orb = screen.getByRole("img", { name: /composing/i })
    expect(orb).toHaveAttribute("data-state", "composing")
    expect(orb).toHaveAttribute("data-size", "64")
    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument()
    resolveAsk(groundedResponse)
    expect(
      await screen.findByText("The overdue billing dispute is waiting on review."),
    ).toBeInTheDocument()
    expect(screen.queryByRole("img", { name: /composing/i })).not.toBeInTheDocument()
  })
})
